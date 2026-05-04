"""Planner 节点 — 根据目标采集量选择执行策略。"""

from __future__ import annotations

import logging
import os
from typing import Any

from workflows.state import KBState

logger = logging.getLogger(__name__)

DEFAULT_TARGET_COUNT = 10

STRATEGIES = {
    "lite": {
        "tier": "lite",
        "per_source_limit": 5,
        "relevance_threshold": 0.7,
        "max_iterations": 1,
        "rationale": "目标量少，收紧相关性阈值以保证质量，单轮审核即可",
    },
    "standard": {
        "tier": "standard",
        "per_source_limit": 10,
        "relevance_threshold": 0.5,
        "max_iterations": 2,
        "rationale": "中等目标量，平衡覆盖面与质量，允许两轮修正",
    },
    "full": {
        "tier": "full",
        "per_source_limit": 20,
        "relevance_threshold": 0.4,
        "max_iterations": 3,
        "rationale": "大批量采集，放宽阈值扩大覆盖面，最多三轮修正保障质量",
    },
}


def plan_strategy(target_count: int | None = None) -> dict[str, Any]:
    """根据目标采集量返回执行策略。"""
    if target_count is None:
        target_count = int(os.getenv("PLANNER_TARGET_COUNT", str(DEFAULT_TARGET_COUNT)))

    if target_count < 10:
        plan = dict(STRATEGIES["lite"])
    elif target_count < 20:
        plan = dict(STRATEGIES["standard"])
    else:
        plan = dict(STRATEGIES["full"])

    plan["target_count"] = target_count
    logger.info("[Planner] target=%d, tier=%s", target_count, plan["tier"])
    return plan


def planner_node(state: KBState) -> dict[str, Any]:
    """LangGraph 节点包装：生成执行策略写入 state。"""
    plan = plan_strategy()
    return {"plan": plan}
