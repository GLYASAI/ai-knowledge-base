"""Supervisor 监督模式：Worker 生成 + Supervisor 审核循环。

Worker Agent 接收任务输出 JSON 分析报告，Supervisor Agent 审核质量，
不通过则带反馈重做，最多循环 max_retries 轮。

用法:
    python -m patterns.supervisor "分析 LangGraph 的技术架构"
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from workflows.model_client import chat

logger = logging.getLogger(__name__)

WORKER_SYSTEM = """\
你是一个专业的 AI 技术分析师。根据用户给出的任务，输出结构化的 JSON 分析报告。

报告格式：
{
  "title": "分析主题",
  "summary": "一句话摘要",
  "key_points": ["要点1", "要点2", "要点3"],
  "tech_details": "技术细节描述（100-300字）",
  "conclusion": "结论与建议"
}

请严格以 JSON 格式回复，不要添加其他文本。"""

SUPERVISOR_SYSTEM = """\
你是一个严格的质量审核员。对以下 AI 分析报告进行评分和审核。

评分维度（每项 1-10 分）：
- 准确性：信息是否准确、有无事实错误
- 深度：分析是否有深度、有无独到见解
- 格式：JSON 结构是否完整、字段是否齐全

审核规则：
- 总分 = (准确性 + 深度 + 格式) / 3，四舍五入取整
- 总分 >= 7 为通过
- 不通过时必须给出具体改进建议

请严格以 JSON 格式回复：
{"passed": true/false, "score": 8, "accuracy": 8, "depth": 7, "format": 9, "feedback": "具体反馈"}"""


def _parse_json(text: str) -> dict[str, Any] | None:
    """从文本中提取 JSON。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        cleaned = "\n".join(lines[start:end])

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def worker(task: str, feedback: str | None = None) -> str:
    """Worker Agent：根据任务生成分析报告。"""
    prompt = f"任务：{task}"
    if feedback:
        prompt += f"\n\n上一轮审核反馈（请据此改进）：{feedback}"

    text, _usage = chat(prompt, system=WORKER_SYSTEM)
    return text


def review(task: str, output: str) -> dict[str, Any]:
    """Supervisor Agent：审核 Worker 的输出质量。"""
    prompt = f"原始任务：{task}\n\n待审核的分析报告：\n{output}"
    text, _usage = chat(prompt, system=SUPERVISOR_SYSTEM)

    result = _parse_json(text)
    if not result:
        return {"passed": False, "score": 0, "feedback": "审核结果解析失败，请重做"}

    if "score" not in result:
        acc = result.get("accuracy", 5)
        dep = result.get("depth", 5)
        fmt = result.get("format", 5)
        result["score"] = round((acc + dep + fmt) / 3)

    if "passed" not in result:
        result["passed"] = result["score"] >= 7

    return result


def supervisor(task: str, max_retries: int = 3) -> dict[str, Any]:
    """Supervisor 监督循环：Worker 生成 → Supervisor 审核 → 不通过则重做。

    Returns:
        {
            "output": dict,       # Worker 最终输出（解析后的 JSON）
            "attempts": int,      # 实际尝试次数
            "final_score": int,   # 最终审核分数
            "warning": str|None,  # 超过重试次数时的警告
        }
    """
    feedback = None

    for attempt in range(1, max_retries + 1):
        logger.info("第 %d/%d 轮 — Worker 生成中...", attempt, max_retries)
        raw_output = worker(task, feedback=feedback)

        logger.info("第 %d/%d 轮 — Supervisor 审核中...", attempt, max_retries)
        review_result = review(task, raw_output)
        score = review_result.get("score", 0)
        passed = review_result.get("passed", False)

        logger.info(
            "第 %d/%d 轮 — score=%d, passed=%s",
            attempt, max_retries, score, passed,
        )

        if passed:
            parsed = _parse_json(raw_output)
            return {
                "output": parsed or {"raw": raw_output},
                "attempts": attempt,
                "final_score": score,
            }

        feedback = review_result.get("feedback", "质量不达标，请改进")
        logger.info("未通过，反馈: %s", feedback)

    logger.warning("已达最大重试次数 %d，强制返回", max_retries)
    parsed = _parse_json(raw_output)
    return {
        "output": parsed or {"raw": raw_output},
        "attempts": max_retries,
        "final_score": score,
        "warning": f"未通过质量审核（score={score}），已达最大重试次数",
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "分析 LangGraph 框架的核心架构和适用场景"

    result = supervisor(task)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
