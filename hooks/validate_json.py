"""校验知识条目 JSON 文件的格式与内容。

用法:
    python hooks/validate_json.py <json_file> [json_file2 ...]
    python hooks/validate_json.py knowledge/articles/*.json
"""

import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}
ID_PATTERN = re.compile(r"^[a-z]+-\d{8}-\d{3}$")
URL_PATTERN = re.compile(r"^https?://.+")


def validate_file(path: Path) -> list[str]:
    """校验单个 JSON 文件，返回错误列表。"""
    errors: list[str] = []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"无法读取文件: {exc}"]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"JSON 解析失败: {exc}"]

    if not isinstance(data, dict):
        return ["顶层结构必须是 JSON 对象"]

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"缺少必填字段: {field}")
        elif not isinstance(data[field], expected_type):
            actual = type(data[field]).__name__
            errors.append(
                f"字段 '{field}' 类型错误: 期望 {expected_type.__name__}，"
                f"实际 {actual}"
            )

    if isinstance(data.get("id"), str) and not ID_PATTERN.match(data["id"]):
        errors.append(
            f"ID 格式错误: '{data['id']}'，"
            f"期望 {{source}}-{{YYYYMMDD}}-{{NNN}}（如 github-20260317-001）"
        )

    if isinstance(data.get("status"), str) and data["status"] not in VALID_STATUSES:
        errors.append(
            f"status 值无效: '{data['status']}'，"
            f"可选值: {', '.join(sorted(VALID_STATUSES))}"
        )

    if isinstance(data.get("source_url"), str) and not URL_PATTERN.match(data["source_url"]):
        errors.append(f"source_url 格式错误: '{data['source_url']}'")

    if isinstance(data.get("summary"), str) and len(data["summary"]) < 20:
        errors.append(
            f"摘要过短: {len(data['summary'])} 字，最少 20 字"
        )

    if isinstance(data.get("tags"), list) and len(data["tags"]) < 1:
        errors.append("tags 不能为空，至少需要 1 个标签")

    analysis = data.get("analysis")
    if isinstance(analysis, dict):
        score = analysis.get("relevance_score")
        if score is not None:
            if not isinstance(score, (int, float)) or not (1 <= score <= 10):
                errors.append(
                    f"relevance_score 超出范围: {score}，应为 1-10"
                )

        audience = analysis.get("audience")
        if audience is not None:
            if audience not in VALID_AUDIENCES:
                errors.append(
                    f"audience 值无效: '{audience}'，"
                    f"可选值: {', '.join(sorted(VALID_AUDIENCES))}"
                )

    return errors


def main() -> None:
    """入口函数：解析参数并执行校验。"""
    if len(sys.argv) < 2:
        logger.error("用法: python hooks/validate_json.py <json_file> [json_file2 ...]")
        sys.exit(1)

    paths = [Path(arg) for arg in sys.argv[1:]]

    total = 0
    passed = 0
    failed = 0

    for path in paths:
        if not path.exists():
            logger.error("[SKIP] %s — 文件不存在", path)
            failed += 1
            total += 1
            continue

        total += 1
        errors = validate_file(path)

        if errors:
            failed += 1
            logger.error("[FAIL] %s", path)
            for err in errors:
                logger.error("  - %s", err)
        else:
            passed += 1
            logger.info("[PASS] %s", path)

    logger.info("--- 校验汇总 ---")
    logger.info("总计: %d | 通过: %d | 失败: %d", total, passed, failed)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
