"""HumanFlag Agent — 人工介入节点（异常终点）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.state import KBState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PENDING_DIR = BASE_DIR / "knowledge" / "pending_review"


def human_flag_node(state: KBState) -> dict[str, Any]:
    """审核循环超过上限时的兜底 — 写入 pending_review/ 目录。"""
    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")

    logger.warning("[HumanFlag] 达到 %d 次审核仍未通过", iteration)
    logger.info("[HumanFlag] 最后反馈: %s", feedback[:200])

    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    filepath = PENDING_DIR / f"pending-{timestamp}.json"
    filepath.write_text(
        json.dumps({
            "timestamp": timestamp,
            "iterations_used": iteration,
            "last_feedback": feedback,
            "analyses": analyses,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info("[HumanFlag] 已保存到 %s", filepath)
    return {"needs_human_review": True}
