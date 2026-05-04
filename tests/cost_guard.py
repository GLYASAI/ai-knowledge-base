"""多 Agent 预算守卫 — 追踪 LLM 调用成本并实施三重保护。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """预算超限异常。"""


@dataclass
class CostRecord:
    """单次 LLM 调用记录。"""

    timestamp: str
    node_name: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    model: str = ""


class CostGuard:
    """三重保护预算守卫：记录、预警、熔断。"""

    def __init__(
        self,
        budget: float = 1.0,
        alert_threshold: float = 0.8,
        input_price_per_million: float = 1.0,
        output_price_per_million: float = 2.0,
    ) -> None:
        self.budget = budget
        self.alert_threshold = alert_threshold
        self.input_price_per_million = input_price_per_million
        self.output_price_per_million = output_price_per_million
        self.records: list[CostRecord] = []

    def _calc_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """根据定价计算单次调用成本。"""
        return (
            prompt_tokens * self.input_price_per_million
            + completion_tokens * self.output_price_per_million
        ) / 1_000_000

    @property
    def total_cost(self) -> float:
        """累计总成本。"""
        return sum(r.cost for r in self.records)

    @property
    def total_prompt_tokens(self) -> int:
        """累计 prompt tokens。"""
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        """累计 completion tokens。"""
        return sum(r.completion_tokens for r in self.records)

    def record(self, node_name: str, usage: dict[str, int], model: str = "") -> CostRecord:
        """记录一次 LLM 调用的 token 用量。"""
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = self._calc_cost(prompt_tokens, completion_tokens)

        rec = CostRecord(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            node_name=node_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            model=model,
        )
        self.records.append(rec)
        logger.debug("[CostGuard] %s: +%.6f (total=%.6f)", node_name, cost, self.total_cost)
        return rec

    def check(self) -> dict[str, Any]:
        """检查预算状态，超限时抛出 BudgetExceededError。"""
        total = self.total_cost
        ratio = total / self.budget if self.budget > 0 else 0.0

        if total >= self.budget:
            raise BudgetExceededError(
                f"预算超限：已花费 ¥{total:.6f}，预算 ¥{self.budget:.2f}"
            )

        if ratio >= self.alert_threshold:
            return {
                "status": "warning",
                "total_cost": total,
                "budget": self.budget,
                "usage_ratio": round(ratio, 4),
                "message": f"预算预警：已使用 {ratio:.1%}（¥{total:.6f} / ¥{self.budget:.2f}）",
            }

        return {
            "status": "ok",
            "total_cost": total,
            "budget": self.budget,
            "usage_ratio": round(ratio, 4),
            "message": "正常",
        }

    def get_report(self) -> dict[str, Any]:
        """生成成本报告，按节点分组统计。"""
        by_node: dict[str, dict[str, Any]] = {}
        for rec in self.records:
            if rec.node_name not in by_node:
                by_node[rec.node_name] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost": 0.0,
                }
            entry = by_node[rec.node_name]
            entry["calls"] += 1
            entry["prompt_tokens"] += rec.prompt_tokens
            entry["completion_tokens"] += rec.completion_tokens
            entry["cost"] += rec.cost

        for entry in by_node.values():
            entry["cost"] = round(entry["cost"], 6)

        return {
            "total_cost": round(self.total_cost, 6),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_calls": len(self.records),
            "budget": self.budget,
            "usage_ratio": round(self.total_cost / self.budget, 4) if self.budget > 0 else 0.0,
            "by_node": by_node,
        }

    def save_report(self, path: str | Path | None = None) -> Path:
        """保存成本报告到 JSON 文件。"""
        if path is None:
            path = Path("cost_report.json")
        else:
            path = Path(path)

        report = self.get_report()
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[CostGuard] 报告已保存到 %s", path)
        return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    passed = 0
    failed = 0

    # --- 测试 1：成本追踪正确 ---
    guard = CostGuard(budget=1.0)
    guard.record("analyze", {"prompt_tokens": 1000, "completion_tokens": 500})
    guard.record("review", {"prompt_tokens": 2000, "completion_tokens": 300})

    expected_cost = (1000 * 1.0 + 500 * 2.0) / 1_000_000 + (2000 * 1.0 + 300 * 2.0) / 1_000_000
    assert guard.total_prompt_tokens == 3000, f"prompt_tokens 错误: {guard.total_prompt_tokens}"
    assert guard.total_completion_tokens == 800, f"completion_tokens 错误: {guard.total_completion_tokens}"
    assert abs(guard.total_cost - expected_cost) < 1e-9, f"total_cost 错误: {guard.total_cost} != {expected_cost}"
    passed += 1
    logger.info("✓ 测试 1 通过：成本追踪正确 (cost=%.6f)", guard.total_cost)

    # --- 测试 2：预算超限检测 ---
    guard2 = CostGuard(budget=0.001)
    guard2.record("analyze", {"prompt_tokens": 500, "completion_tokens": 500})
    try:
        guard2.check()
        logger.error("✗ 测试 2 失败：未抛出 BudgetExceededError")
        failed += 1
    except BudgetExceededError:
        passed += 1
        logger.info("✓ 测试 2 通过：预算超限正确抛出 BudgetExceededError")

    # --- 测试 3：预警阈值触发 ---
    guard3 = CostGuard(budget=0.01, alert_threshold=0.8)
    # 花费刚好 >= 80% 但 < 100%
    guard3.record("analyze", {"prompt_tokens": 4000, "completion_tokens": 2000})
    result = guard3.check()
    assert result["status"] == "warning", f"status 错误: {result['status']}"
    passed += 1
    logger.info("✓ 测试 3 通过：预警阈值触发 (ratio=%.1f%%)", result["usage_ratio"] * 100)

    logger.info("\n全部测试完成：%d 通过，%d 失败", passed, failed)
