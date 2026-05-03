"""审核节点 — 5 维度加权评分，审核 analyses 质量。"""

from __future__ import annotations

import json
import logging
from typing import Any

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

logger = logging.getLogger(__name__)

MAX_REVIEW_ITEMS = 5
PASS_THRESHOLD = 7.0

WEIGHTS = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}

REVIEW_SYSTEM = """\
你是一个严格的 AI 内容审核员。请对以下分析结果进行五维度评审，每个维度 1-10 分（整数）：

1. **摘要质量** (summary_quality): 是否准确、简洁、20-100 字，技术术语保留英文
2. **技术深度** (technical_depth): tech_highlights 是否抓住核心技术点，分析是否有深度
3. **相关性** (relevance): 与 AI/LLM/Agent 领域的相关程度，relevance_score 是否合理
4. **原创性** (originality): 分析视角是否有独到见解，而非简单复述描述
5. **格式规范** (formatting): 字段完整性、标签格式、audience 分类是否规范

请以 JSON 格式回复：
{
  "scores": {
    "summary_quality": 8,
    "technical_depth": 7,
    "relevance": 9,
    "originality": 6,
    "formatting": 8
  },
  "feedback": "具体修改建议（如无问题可为空字符串）"
}"""


def _compute_weighted_score(scores: dict[str, int | float]) -> float:
    """根据 WEIGHTS 计算加权总分。"""
    total = 0.0
    for dim, weight in WEIGHTS.items():
        total += float(scores.get(dim, 0)) * weight
    return round(total, 2)


def review_node(state: KBState) -> dict[str, Any]:
    """5 维度加权评分审核 analyses，iteration >= 2 时强制通过。"""
    tracker = dict(state.get("cost_tracker") or {})
    iteration = state.get("iteration", 0)
    analyses = state.get("analyses", [])

    logger.info("[Review] 开始审核，iteration=%d, analyses=%d", iteration, len(analyses))

    if not analyses:
        logger.info("[Review] 无分析结果，跳过审核")
        return {
            "review_passed": True,
            "review_feedback": "无分析结果，自动通过",
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

    # 只审核前 5 条，控制 token 消耗
    review_items = analyses[:MAX_REVIEW_ITEMS]
    items_text = json.dumps(review_items, ensure_ascii=False, indent=2)
    prompt = f"请审核以下 {len(review_items)} 条分析结果：\n\n{items_text}"

    try:
        resp, usage = chat_json(prompt, system=REVIEW_SYSTEM, temperature=0.1)
        tracker = accumulate_usage(tracker, usage)
    except Exception as exc:
        logger.warning("[Review] LLM 审核失败，自动通过: %s", exc)
        return {
            "review_passed": True,
            "review_feedback": f"审核异常: {exc}，自动通过",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    if not isinstance(resp, dict) or "scores" not in resp:
        logger.warning("[Review] LLM 返回格式异常，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "格式异常，自动通过",
            "iteration": iteration + 1,
            "cost_tracker": tracker,
        }

    scores = resp["scores"]
    weighted = _compute_weighted_score(scores)
    passed = weighted >= PASS_THRESHOLD

    feedback = resp.get("feedback", "").strip()

    logger.info(
        "[Review] weighted=%.2f, passed=%s, scores=%s",
        weighted, passed, scores,
    )

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }
