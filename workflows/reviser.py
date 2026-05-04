"""修正节点 — 根据审核反馈修改 analyses。"""

from __future__ import annotations

import json
import logging
from typing import Any

from tests.cost_guard import BudgetExceededError
from workflows.model_client import accumulate_usage, chat_json
from workflows.reviewer import MAX_REVIEW_ITEMS
from workflows.state import KBState

logger = logging.getLogger(__name__)

REVISE_SYSTEM = """\
你是一个 AI 技术编辑。根据审核反馈修改分析结果列表。

要求：
- 逐条修改，保留所有原有字段
- 重点根据反馈改进薄弱维度
- 技术术语保留英文，摘要保持 20-100 字
- 返回修改后的完整 JSON 数组（与输入结构一致）"""


def revise_node(state: KBState) -> dict[str, Any]:
    """根据审核反馈修改 analyses，返回改进后的列表。"""
    analyses = state.get("analyses", [])
    feedback = (state.get("review_feedback") or "").strip()
    tracker = dict(state.get("cost_tracker") or {})

    if not analyses or not feedback:
        logger.debug("[Revise] 无需修正（analyses=%d, feedback=%s）", len(analyses), bool(feedback))
        return {}

    logger.info("[Revise] 开始修正前 %d 条分析，feedback: %s", min(len(analyses), MAX_REVIEW_ITEMS), feedback[:80])

    to_revise = analyses[:MAX_REVIEW_ITEMS]
    rest = analyses[MAX_REVIEW_ITEMS:]

    items_text = json.dumps(to_revise, ensure_ascii=False, indent=2)
    prompt = (
        f"以下是 {len(to_revise)} 条分析结果：\n\n{items_text}\n\n"
        f"审核反馈：\n{feedback}\n\n"
        f"请根据反馈逐条修改，返回修改后的完整 JSON 数组。"
    )

    try:
        resp, usage = chat_json(prompt, system=REVISE_SYSTEM, temperature=0.4, node_name="revise")
        tracker = accumulate_usage(tracker, usage)
    except BudgetExceededError:
        raise
    except Exception as exc:
        logger.warning("[Revise] LLM 调用失败，保留原文: %s", exc)
        return {"analyses": to_revise + rest, "cost_tracker": tracker}

    if isinstance(resp, list):
        improved = resp
    elif isinstance(resp, dict) and "analyses" in resp:
        improved = resp["analyses"]
    else:
        logger.warning("[Revise] 返回格式异常，保留原文")
        improved = to_revise

    logger.info("[Revise] 修正完成，%d 条（跳过 %d 条未审核项）", len(improved), len(rest))
    return {"analyses": improved + rest, "cost_tracker": tracker}
