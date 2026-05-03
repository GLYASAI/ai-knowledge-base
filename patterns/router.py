"""Router 路由模式：两层意图分类 + 三种处理器。

第一层：关键词快速匹配（零成本，不调 LLM）
第二层：LLM 分类兜底（处理模糊意图）

意图类型：
    github_search   — 搜索 GitHub 项目
    knowledge_query — 从本地知识库检索
    general_chat    — 通用对话
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from workflows.model_client import chat, chat_json

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTICLES_DIR = BASE_DIR / "knowledge" / "articles"
INDEX_PATH = ARTICLES_DIR / "index.json"

INTENTS = ("github_search", "knowledge_query", "general_chat")

KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["github", "仓库", "repo", "star", "trending", "开源项目"], "github_search"),
    (["知识库", "文章", "已有", "本地", "检索", "收录", "knowledge"], "knowledge_query"),
]

CLASSIFY_SYSTEM = """\
你是一个意图分类器。根据用户输入，判断属于以下哪个意图：
- github_search: 用户想搜索 GitHub 上的项目或仓库
- knowledge_query: 用户想从本地知识库中查找已收录的文章或项目
- general_chat: 通用对话、闲聊或其他问题

请严格以 JSON 格式回复，不要添加其他文本：
{"intent": "github_search"}"""


# ---------------------------------------------------------------------------
# 第一层：关键词匹配
# ---------------------------------------------------------------------------


def classify_by_keywords(query: str) -> str | None:
    """通过关键词快速匹配意图，匹配不上返回 None。"""
    q = query.lower()
    for keywords, intent in KEYWORD_RULES:
        if any(kw in q for kw in keywords):
            logger.debug("关键词匹配命中: %s → %s", keywords, intent)
            return intent
    return None


# ---------------------------------------------------------------------------
# 第二层：LLM 分类
# ---------------------------------------------------------------------------


def classify_by_llm(query: str) -> str:
    """调用 LLM 对模糊意图进行分类。"""
    logger.debug("关键词未命中，调用 LLM 分类")
    try:
        result, usage = chat_json(
            f"用户输入：{query}",
            system=CLASSIFY_SYSTEM,
        )
        intent = result.get("intent", "general_chat")
        if intent not in INTENTS:
            logger.warning("LLM 返回未知意图 '%s'，回退到 general_chat", intent)
            return "general_chat"
        logger.debug("LLM 分类结果: %s", intent)
        return intent
    except Exception as exc:
        logger.error("LLM 分类失败，回退到 general_chat: %s", exc)
        return "general_chat"


def classify(query: str) -> str:
    """两层意图分类：关键词优先，LLM 兜底。"""
    return classify_by_keywords(query) or classify_by_llm(query)


# ---------------------------------------------------------------------------
# 处理器：github_search
# ---------------------------------------------------------------------------

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"


def handle_github_search(query: str) -> str:
    """搜索 GitHub 仓库并返回格式化结果。"""
    encoded_query = urllib.parse.quote(query)
    url = f"{GITHUB_SEARCH_API}?q={encoded_query}&sort=stars&order=desc&per_page=5"

    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github.v3+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("GitHub 搜索失败: %s", exc)
        return f"GitHub 搜索失败: {exc}"

    items = data.get("items", [])
    if not items:
        return f"未在 GitHub 上找到与 '{query}' 相关的项目。"

    lines = [f"GitHub 搜索结果（关键词: {query}）:\n"]
    for i, item in enumerate(items, 1):
        stars = item.get("stargazers_count", 0)
        desc = item.get("description") or "无描述"
        lines.append(
            f"{i}. {item['full_name']} ⭐{stars:,}\n"
            f"   {desc}\n"
            f"   {item['html_url']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 处理器：knowledge_query
# ---------------------------------------------------------------------------


def _load_articles() -> list[dict[str, Any]]:
    """加载知识库文章索引。"""
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    logger.debug("index.json 不存在，动态扫描 articles 目录")
    articles = []
    if not ARTICLES_DIR.exists():
        return articles
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            articles.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("跳过无效文件 %s: %s", path.name, exc)
    return articles


def handle_knowledge_query(query: str) -> str:
    """从本地知识库检索匹配的文章。"""
    articles = _load_articles()
    if not articles:
        return "本地知识库为空，暂无可检索的文章。"

    q = query.lower()
    matches = []
    for article in articles:
        title = article.get("title", "").lower()
        summary = article.get("summary", "").lower()
        tags = " ".join(article.get("tags", [])).lower()
        searchable = f"{title} {summary} {tags}"
        if any(word in searchable for word in q.split()):
            matches.append(article)

    if not matches:
        return f"未在知识库中找到与 '{query}' 相关的文章（共 {len(articles)} 篇）。"

    matches.sort(
        key=lambda a: a.get("analysis", {}).get("relevance_score", 0),
        reverse=True,
    )
    top = matches[:5]

    lines = [f"知识库检索结果（共匹配 {len(matches)} 篇）:\n"]
    for i, article in enumerate(top, 1):
        score = article.get("analysis", {}).get("relevance_score", "?")
        lines.append(
            f"{i}. [{score}分] {article.get('title', '无标题')}\n"
            f"   {article.get('summary', '无摘要')}\n"
            f"   {article.get('source_url', '')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 处理器：general_chat
# ---------------------------------------------------------------------------


def handle_general_chat(query: str) -> str:
    """调用 LLM 直接回答。"""
    text, _usage = chat(query)
    return text


# ---------------------------------------------------------------------------
# 路由入口
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "github_search": handle_github_search,
    "knowledge_query": handle_knowledge_query,
    "general_chat": handle_general_chat,
}


def route(query: str) -> str:
    """统一入口：分类意图并分发到对应处理器。"""
    intent = classify(query)
    logger.debug("路由: '%s' → %s", query, intent)
    handler = HANDLERS[intent]
    return handler(query)


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "用一句话解释什么是 Transformer"

    intent = classify(query)
    sys.stdout.write(f"[意图] {intent}\n\n")
    handler = HANDLERS[intent]
    result = handler(query)
    sys.stdout.write(result + "\n")
