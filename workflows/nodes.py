"""LangGraph 工作流节点 — 采集 / 分析 / 整理 / 审核 / 保存。

每个节点是纯函数：接收 KBState，返回 dict（部分状态更新）。
LLM 调用统一走 model_client，token 用量通过 cost_tracker 在节点间累积。

LangGraph 会按图的 edge 来编排这些节点。典型执行顺序：
collect → analyze → organize → review
                        ↑          ↓
                        └── (not passed && iteration < 2)
                                   ↓
                               (passed) → save
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from workflows.model_client import accumulate_usage, chat, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTICLES_DIR = BASE_DIR / "knowledge" / "articles"
RSS_CONFIG_PATH = BASE_DIR / "pipeline" / "rss_sources.yaml"

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "AI OR LLM OR agent OR GPT language:python"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TODAY_COMPACT = TODAY.replace("-", "")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """通过 urllib 发起 GET 请求并返回 JSON。"""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _http_get_text(url: str) -> str:
    """通过 urllib 发起 GET 请求并返回文本。"""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def _strip_cdata(text: str) -> str:
    """移除 CDATA 包裹。"""
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    return text.strip()


def _strip_html(text: str) -> str:
    """移除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss_xml(xml_text: str, limit: int) -> list[dict[str, str]]:
    """用正则从 RSS/Atom XML 中提取条目。"""
    entries: list[dict[str, str]] = []

    item_pattern = re.compile(
        r"<(?:item|entry)[\s>].*?</(?:item|entry)>", re.DOTALL,
    )
    title_pattern = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL)
    link_pattern = re.compile(
        r'<link[^>]*href=["\']([^"\']+)["\']|<link[^>]*>(.*?)</link>',
        re.DOTALL,
    )
    desc_pattern = re.compile(
        r"<(?:description|summary|content)[^>]*>(.*?)"
        r"</(?:description|summary|content)>",
        re.DOTALL,
    )

    for match in item_pattern.finditer(xml_text):
        if len(entries) >= limit:
            break
        block = match.group(0)

        title_m = title_pattern.search(block)
        title = _strip_cdata(title_m.group(1)) if title_m else ""

        link_m = link_pattern.search(block)
        link = ""
        if link_m:
            link = link_m.group(1) or link_m.group(2) or ""
        link = link.strip()

        desc_m = desc_pattern.search(block)
        desc = _strip_cdata(desc_m.group(1))[:500] if desc_m else ""

        if title and link:
            entries.append({
                "title": _strip_html(title),
                "url": link,
                "description": _strip_html(desc),
            })

    return entries


def _make_article_id(source: str, index: int) -> str:
    """生成文章 ID: {source}-{YYYYMMDD}-{NNN}。"""
    return f"{source}-{TODAY_COMPACT}-{index:03d}"


def _make_filename(source: str, title: str) -> str:
    """生成文件名: {date}-{source}-{slug}.json。"""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.split("/")[-1].lower()).strip("-")
    slug = slug[:50]
    return f"{TODAY}-{source}-{slug}.json"


def _load_existing_urls() -> set[str]:
    """扫描已有文章，提取 source_url 用于去重。"""
    urls: set[str] = set()
    if not ARTICLES_DIR.exists():
        return urls
    for path in ARTICLES_DIR.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if url := data.get("source_url"):
                urls.add(url)
        except (json.JSONDecodeError, OSError):
            continue
    return urls


# ---------------------------------------------------------------------------
# Node 1: 采集
# ---------------------------------------------------------------------------

GITHUB_LIMIT = 20
RSS_LIMIT = 10


def _collect_github(limit: int = GITHUB_LIMIT) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关仓库。"""
    logger.info("[Collect] GitHub 采集，limit=%d", limit)
    results: list[dict[str, Any]] = []
    try:
        params = (
            f"?q={urllib.request.quote(GITHUB_SEARCH_QUERY)}"
            f"&sort=stars&order=desc&per_page={min(limit, 100)}"
        )
        data = _http_get_json(
            GITHUB_SEARCH_API + params,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        for item in data.get("items", [])[:limit]:
            results.append({
                "source": "github",
                "title": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "description": item.get("description") or "",
                "stars": item.get("stargazers_count", 0),
                "language": item.get("language") or "",
                "topics": item.get("topics", []),
            })
    except Exception as exc:
        logger.warning("[Collect] GitHub 采集失败: %s", exc)
    logger.info("[Collect] GitHub 采集完成，%d 条", len(results))
    return results


def _collect_rss(limit: int = RSS_LIMIT) -> list[dict[str, Any]]:
    """从已配置的 RSS 源采集内容。"""
    logger.info("[Collect] RSS 采集，limit=%d", limit)
    results: list[dict[str, Any]] = []
    if not RSS_CONFIG_PATH.exists():
        logger.warning("[Collect] RSS 配置文件不存在: %s", RSS_CONFIG_PATH)
        return results

    config = yaml.safe_load(RSS_CONFIG_PATH.read_text(encoding="utf-8"))
    enabled = [s for s in config.get("sources", []) if s.get("enabled")]
    remaining = limit

    for src in enabled:
        if remaining <= 0:
            break
        try:
            xml_text = _http_get_text(src["url"])
            entries = _parse_rss_xml(xml_text, remaining)
            for entry in entries:
                results.append({
                    "source": "rss",
                    "rss_name": src["name"],
                    "category": src.get("category", ""),
                    "title": entry["title"],
                    "url": entry["url"],
                    "description": entry["description"],
                })
            remaining -= len(entries)
        except Exception as exc:
            logger.warning("[Collect] RSS '%s' 失败: %s", src["name"], exc)

    logger.info("[Collect] RSS 采集完成，%d 条", len(results))
    return results


def collect_node(state: KBState) -> dict[str, Any]:
    """采集 GitHub 仓库和 RSS 源的 AI 相关内容。"""
    logger.info("[Collect] 开始采集")
    sources: list[dict[str, Any]] = []
    sources.extend(_collect_github())
    sources.extend(_collect_rss())
    logger.info("[Collect] 采集完成，共 %d 条", len(sources))
    return {"sources": sources}


# ---------------------------------------------------------------------------
# Node 2: 分析
# ---------------------------------------------------------------------------

ANALYZE_SYSTEM = """\
你是一个 AI 技术分析助手。给定一个开源项目或技术文章的信息，你需要：
1. 用中文写一句话摘要（20-100 字），技术术语保留英文
2. 提取 2-3 个技术亮点（tech_highlights）
3. 给出 relevance_score（1-10 分）和一句评分理由
4. 推荐 2-5 个英文标签（小写，用连字符连接）
5. 判断目标受众: beginner / intermediate / advanced

请严格以 JSON 格式回复：
{
  "summary": "...",
  "tech_highlights": ["...", "..."],
  "relevance_score": 8,
  "score_reason": "...",
  "tags": ["...", "..."],
  "audience": "intermediate"
}"""


def _build_analyze_prompt(item: dict[str, Any]) -> str:
    """构造单条分析 prompt。"""
    parts = [f"项目/文章: {item.get('title', '')}"]
    if item.get("url"):
        parts.append(f"链接: {item['url']}")
    if item.get("description"):
        parts.append(f"描述: {item['description']}")
    if item.get("stars"):
        parts.append(f"Star 数: {item['stars']}")
    if item.get("language"):
        parts.append(f"语言: {item['language']}")
    if item.get("topics"):
        parts.append(f"标签: {', '.join(item['topics'])}")
    if item.get("category"):
        parts.append(f"分类: {item['category']}")
    return "\n".join(parts)


def analyze_node(state: KBState) -> dict[str, Any]:
    """用 LLM 对每条数据生成中文摘要、标签、评分。"""
    logger.info("[Analyze] 开始分析 %d 条", len(state["sources"]))
    tracker = state.get("cost_tracker") or {}
    analyses: list[dict[str, Any]] = []

    for i, item in enumerate(state["sources"], 1):
        logger.info("[Analyze] [%d/%d] %s", i, len(state["sources"]), item.get("title", ""))
        prompt = _build_analyze_prompt(item)

        try:
            result, usage = chat_json(prompt, system=ANALYZE_SYSTEM)
        except Exception as exc:
            logger.warning("[Analyze] LLM 调用失败，跳过: %s", exc)
            continue

        tracker = accumulate_usage(tracker, usage)

        if not isinstance(result, dict):
            logger.warning("[Analyze] 返回非 dict，跳过")
            continue

        analyses.append({**item, "llm_analysis": result})

    logger.info("[Analyze] 分析完成，%d/%d 成功", len(analyses), len(state["sources"]))
    return {"analyses": analyses, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# Node 3: 整理
# ---------------------------------------------------------------------------

REVISE_SYSTEM = """\
你是一个 AI 技术编辑。根据审核反馈修改知识条目。
请严格以 JSON 格式回复修改后的完整条目（保留所有原有字段）。"""


def organize_node(state: KBState) -> dict[str, Any]:
    """过滤低分条目、按 URL 去重，有审核反馈时用 LLM 修正。"""
    logger.info("[Organize] 开始整理")
    tracker = state.get("cost_tracker") or {}
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")

    # 首轮：从 analyses 构建 articles
    if iteration == 0:
        existing_urls = _load_existing_urls()
        seen_urls: set[str] = set(existing_urls)
        counters: dict[str, int] = {}
        articles: list[dict[str, Any]] = []

        for item in state.get("analyses", []):
            analysis = item.get("llm_analysis", {})
            score = analysis.get("relevance_score", 0)
            if score < 6:
                logger.info("[Organize] 低分过滤 (%.1f): %s", score, item.get("title", ""))
                continue

            url = item.get("url", "")
            if url in seen_urls:
                logger.info("[Organize] 去重跳过: %s", url)
                continue
            seen_urls.add(url)

            source = item.get("source", "unknown")
            counters[source] = counters.get(source, 0) + 1

            articles.append({
                "id": _make_article_id(source, counters[source]),
                "title": re.sub(r"\s+", " ", item.get("title", "")).strip(),
                "source": source,
                "source_url": url,
                "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": analysis.get("summary", item.get("description", "")[:100]),
                "analysis": {
                    "tech_highlights": analysis.get("tech_highlights", []),
                    "relevance_score": analysis.get("relevance_score", 5),
                    "score_reason": analysis.get("score_reason", ""),
                    "audience": analysis.get("audience", "intermediate"),
                },
                "tags": analysis.get("tags", []),
                "status": "draft",
            })

        logger.info("[Organize] 首轮整理完成，%d 条", len(articles))
        return {"articles": articles, "cost_tracker": tracker}

    # 后续轮次：根据审核反馈用 LLM 修正
    if not feedback:
        logger.info("[Organize] 无审核反馈，跳过修正")
        return {"articles": state.get("articles", []), "cost_tracker": tracker}

    logger.info("[Organize] 第 %d 轮修正，根据反馈调整", iteration)
    revised: list[dict[str, Any]] = []

    for article in state.get("articles", []):
        prompt = (
            f"以下是一条知识条目：\n{json.dumps(article, ensure_ascii=False, indent=2)}\n\n"
            f"审核反馈：\n{feedback}\n\n"
            f"请根据反馈修改该条目，保留所有字段，以 JSON 格式返回修改后的完整条目。"
        )
        try:
            result, usage = chat_json(prompt, system=REVISE_SYSTEM)
            tracker = accumulate_usage(tracker, usage)
            if isinstance(result, dict):
                revised.append(result)
            else:
                revised.append(article)
        except Exception as exc:
            logger.warning("[Organize] 修正失败，保留原文: %s", exc)
            revised.append(article)

    logger.info("[Organize] 修正完成，%d 条", len(revised))
    return {"articles": revised, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# Node 4: 审核
# ---------------------------------------------------------------------------

REVIEW_SYSTEM = """\
你是一个严格的 AI 内容审核员。请对以下知识条目列表进行四维度评审：

1. **摘要质量** (summary_quality): 是否准确、简洁、20-100 字
2. **标签准确** (tag_accuracy): 标签是否与内容匹配
3. **分类合理** (classification): audience 和 relevance_score 是否合理
4. **一致性** (consistency): 多条条目之间格式、风格是否统一

请以 JSON 格式回复：
{
  "passed": true/false,
  "overall_score": 0.85,
  "feedback": "具体修改建议（passed=true 时可为空字符串）",
  "scores": {
    "summary_quality": 0.9,
    "tag_accuracy": 0.8,
    "classification": 0.85,
    "consistency": 0.9
  }
}"""


def review_node(state: KBState) -> dict[str, Any]:
    """LLM 四维度评分审核，iteration >= 2 时强制通过。"""
    tracker = state.get("cost_tracker") or {}
    iteration = state.get("iteration", 0)
    articles = state.get("articles", [])
    logger.info("[Review] 开始审核，iteration=%d, articles=%d", iteration, len(articles))

    if not articles:
        logger.info("[Review] 无条目，跳过审核")
        return {
            "review_passed": True,
            "review_feedback": "无条目，自动通过",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    if iteration >= 2:
        logger.info("[Review] 已达最大审核次数，强制通过")
        return {
            "review_passed": True,
            "review_feedback": "强制通过：已达最大审核次数",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    articles_text = json.dumps(articles, ensure_ascii=False, indent=2)
    prompt = f"请审核以下 {len(articles)} 条知识条目：\n\n{articles_text}"
    try:
        resp, usage = chat_json(prompt, system=REVIEW_SYSTEM)
        tracker = accumulate_usage(tracker, usage)
    except Exception as exc:
        logger.warning("[Review] LLM 审核失败，强制通过: %s", exc)
        return {
            "review_passed": True,
            "review_feedback": f"审核失败: {exc}，自动通过",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    if not isinstance(resp, dict):
        return {
            "review_passed": True,
            "review_feedback": "强制通过：LLM 返回格式异常",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    passed = bool(resp.get("passed", False))
    logger.info(
        "[Review] overall=%.2f, passed=%s, scores=%s",
        resp.get("overall_score", 0), passed, resp.get("scores", {}),
    )
    return {
        "review_passed": passed,
        "review_feedback": resp.get("feedback", "") if not passed else "",
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }


# ---------------------------------------------------------------------------
# Node 5: 保存
# ---------------------------------------------------------------------------


def save_node(state: KBState) -> dict[str, Any]:
    """将 articles 写入 knowledge/articles/ 并更新 index.json。"""
    logger.info("[Save] 开始保存")
    articles = state.get("articles", [])
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []

    for article in articles:
        filename = _make_filename(article.get("source", "unknown"), article.get("title", ""))
        path = ARTICLES_DIR / filename

        if path.exists():
            logger.info("[Save] 跳过已存在: %s", filename)
            continue

        path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved_files.append(filename)
        logger.info("[Save] 已保存: %s", filename)

    # 更新 index.json
    index_path = ARTICLES_DIR / "index.json"
    index: list[dict[str, str]] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []

    existing_ids = {entry["id"] for entry in index if "id" in entry}
    for article in articles:
        if article.get("id") in existing_ids:
            continue
        index.append({
            "id": article.get("id", ""),
            "title": article.get("title", ""),
            "source_url": article.get("source_url", ""),
            "filename": _make_filename(
                article.get("source", "unknown"), article.get("title", ""),
            ),
        })

    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[Save] index.json 已更新，共 %d 条记录", len(index))

    logger.info("[Save] 保存完成，新增 %d 篇", len(saved_files))
    return {"articles": articles}


# ---------------------------------------------------------------------------
# 测试用审核节点（验证循环后移除）
# ---------------------------------------------------------------------------

_TEST_FEEDBACKS = [
    "第 1 轮：部分摘要超过 100 字，请精简；标签应使用小写连字符格式。",
    "第 2 轮：relevance_score 偏高，建议重新评估；audience 分类不够准确。",
]


def review_node_test(state: KBState) -> dict[str, Any]:
    """测试用审核节点：前 2 次强制不通过，第 3 次通过。"""
    tracker = state.get("cost_tracker") or {}
    iteration = state.get("iteration", 0)

    if iteration >= 2:
        logger.info("[ReviewTest] iteration=%d, review_passed=True（测试通过）", iteration)
        return {
            "review_passed": True,
            "review_feedback": "第 3 轮：审核通过，所有条目符合规范。",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    feedback = _TEST_FEEDBACKS[iteration]
    logger.info("[ReviewTest] iteration=%d, review_passed=False, feedback=%s", iteration, feedback)
    return {
        "review_passed": False,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }
