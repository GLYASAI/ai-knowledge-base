"""分析节点 — 去重后用 LLM 对采集数据生成摘要、标签、评分。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tests.cost_guard import BudgetExceededError
from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTICLES_DIR = BASE_DIR / "knowledge" / "articles"

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
    """去重后用 LLM 对每条数据生成中文摘要、标签、评分。"""
    sources = state["sources"]
    logger.info("[Analyze] 开始分析 %d 条", len(sources))
    tracker = state.get("cost_tracker") or {}
    analyses: list[dict[str, Any]] = []

    existing_urls = _load_existing_urls()
    seen_urls: set[str] = set(existing_urls)

    for i, item in enumerate(sources, 1):
        url = item.get("url", "")
        if url in seen_urls:
            logger.info("[Analyze] 去重跳过: %s", url)
            continue
        seen_urls.add(url)

        logger.info("[Analyze] [%d/%d] %s", i, len(sources), item.get("title", ""))
        prompt = _build_analyze_prompt(item)

        try:
            result, usage = chat_json(prompt, system=ANALYZE_SYSTEM, node_name="analyze")
        except BudgetExceededError:
            logger.warning("[Analyze] 预算超限，终止分析")
            break
        except Exception as exc:
            logger.warning("[Analyze] LLM 调用失败，跳过: %s", exc)
            continue

        tracker = accumulate_usage(tracker, usage)

        if not isinstance(result, dict):
            logger.warning("[Analyze] 返回非 dict，跳过")
            continue

        analyses.append({**item, "llm_analysis": result})

    logger.info("[Analyze] 分析完成，%d/%d 成功", len(analyses), len(sources))
    return {"analyses": analyses, "cost_tracker": tracker}
