"""整理节点 — 过滤、去重、格式化 analyses 为 articles，并保存到磁盘。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.cost_guard import BudgetExceededError
from tests.security import filter_output
from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ARTICLES_DIR = BASE_DIR / "knowledge" / "articles"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TODAY_COMPACT = TODAY.replace("-", "")

DEFAULT_RELEVANCE_THRESHOLD = 0.5

REVISE_SYSTEM = """\
你是一个 AI 技术编辑。根据审核反馈修改知识条目。
请严格以 JSON 格式回复修改后的完整条目（保留所有原有字段）。"""


def _make_article_id(source: str, index: int) -> str:
    """生成文章 ID: {source}-{YYYYMMDD}-{NNN}。"""
    return f"{source}-{TODAY_COMPACT}-{index:03d}"


def _make_filename(source: str, title: str) -> str:
    """生成文件名: {date}-{source}-{slug}.json。"""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.split("/")[-1].lower()).strip("-")
    slug = slug[:50]
    return f"{TODAY}-{source}-{slug}.json"


def _filter_articles_pii(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对 articles 的文本字段做 PII 过滤。"""
    for article in articles:
        for field in ("title", "summary"):
            if article.get(field):
                filtered, detections = filter_output(article[field])
                if detections:
                    logger.warning("[Organize] PII detected in %s: %s", article.get("id", ""), detections)
                article[field] = filtered
    return articles


def organize_node(state: KBState) -> dict[str, Any]:
    """过滤低分条目、按 URL 去重，有审核反馈时用 LLM 修正。"""
    logger.info("[Organize] 开始整理")
    tracker = dict(state.get("cost_tracker") or {})
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")

    plan = state.get("plan") or {}
    threshold = float(plan.get("relevance_threshold", DEFAULT_RELEVANCE_THRESHOLD))
    # relevance_score 为 1-10 整数，threshold 为 0-1 小数，转换为 10 分制
    min_score = threshold * 10

    # 首轮：从 analyses 构建 articles
    if iteration == 0 or not state.get("articles"):
        counters: dict[str, int] = {}
        articles: list[dict[str, Any]] = []

        for item in state.get("analyses", []):
            analysis = item.get("llm_analysis", {})
            score = analysis.get("relevance_score", 0)
            if score < min_score:
                logger.info("[Organize] 低分过滤 (%.1f < %.1f): %s", score, min_score, item.get("title", ""))
                continue

            source = item.get("source", "unknown")
            counters[source] = counters.get(source, 0) + 1

            articles.append({
                "id": _make_article_id(source, counters[source]),
                "title": re.sub(r"\s+", " ", item.get("title", "")).strip(),
                "source": source,
                "source_url": item.get("url", ""),
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

        logger.info("[Organize] 首轮整理完成，%d 条（threshold=%.1f）", len(articles), min_score)
        return {"articles": _filter_articles_pii(articles), "cost_tracker": tracker}

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
            result, usage = chat_json(prompt, system=REVISE_SYSTEM, node_name="organize")
            tracker = accumulate_usage(tracker, usage)
            if isinstance(result, dict):
                revised.append(result)
            else:
                revised.append(article)
        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.warning("[Organize] 修正失败，保留原文: %s", exc)
            revised.append(article)

    logger.info("[Organize] 修正完成，%d 条", len(revised))
    return {"articles": _filter_articles_pii(revised), "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# Node: 保存
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
