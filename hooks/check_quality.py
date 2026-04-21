"""知识条目 5 维度质量评分。

用法:
    python hooks/check_quality.py <json_file> [json_file2 ...]
    python hooks/check_quality.py knowledge/articles/*.json
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VALID_STATUSES = {"draft", "review", "published", "archived"}
ID_PATTERN = re.compile(r"^[a-z]+-\d{8}-\d{3}$")
URL_PATTERN = re.compile(r"^https?://.+")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

STANDARD_TAGS = {
    "agent", "agent-framework", "llm", "rag", "fine-tuning",
    "prompt-engineering", "multimodal", "code-assistant",
    "runtime", "open-source", "mcp", "evaluation",
    "training", "inference", "deployment", "safety",
    "personalization", "writing-assistant", "data-pipeline",
    "tool-use", "reasoning", "planning", "memory",
    "knowledge-graph", "embedding", "vector-db",
    "chatbot", "workflow", "automation", "devtools",
}

TECH_KEYWORDS = {
    "agent", "llm", "rag", "transformer", "token",
    "embedding", "fine-tune", "fine-tuning", "inference",
    "api", "sdk", "mcp", "prompt", "vector",
    "model", "gpu", "训练", "推理", "微调",
    "模型", "向量", "框架", "部署", "架构",
}

HOLLOW_WORDS_CN = {
    "赋能", "抓手", "闭环", "打通", "全链路",
    "底层逻辑", "颗粒度", "对齐", "拉通", "沉淀",
    "强大的", "革命性的",
}

HOLLOW_WORDS_EN = {
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "disruptive", "next-generation",
    "best-in-class", "world-class", "state-of-the-art",
    "paradigm-shifting", "synergy",
}

BAR_WIDTH = 20


@dataclass
class DimensionScore:
    """单维度评分。"""

    name: str
    score: float
    max_score: float
    brief: str = ""

    @property
    def ratio(self) -> float:
        """得分率 0.0 ~ 1.0。"""
        return self.score / self.max_score if self.max_score else 0.0


@dataclass
class QualityReport:
    """完整质量报告。"""

    file_path: str
    dimensions: list[DimensionScore] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        """加权总分。"""
        return sum(d.score for d in self.dimensions)

    @property
    def max_total(self) -> float:
        """总满分。"""
        return sum(d.max_score for d in self.dimensions)

    @property
    def grade(self) -> str:
        """等级 A/B/C。"""
        score = self.total_score
        if score >= 80:
            return "A"
        if score >= 60:
            return "B"
        return "C"


def _bar(ratio: float) -> str:
    """生成文本进度条。"""
    filled = round(ratio * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _score_summary(data: dict) -> DimensionScore:
    """摘要质量评分（满分 25）。"""
    dim = DimensionScore(name="摘要质量", score=0.0, max_score=25.0)
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        dim.brief = "summary 不是字符串"
        return dim

    length = len(summary)
    if length >= 50:
        dim.score = 15.0
    elif length >= 20:
        dim.score = 10.0
    else:
        dim.score = 5.0 * (length / 20)

    text_lower = summary.lower()
    matched = [kw for kw in TECH_KEYWORDS if kw in text_lower]
    keyword_bonus = min(len(matched) * 2.5, 10.0)
    dim.score = min(dim.score + keyword_bonus, 25.0)

    parts = [f"{length}字"]
    if matched:
        parts.append(f"含{len(matched)}个关键词")
    dim.brief = " ".join(parts)

    return dim


def _score_tech_depth(data: dict) -> DimensionScore:
    """技术深度评分（满分 25）。"""
    dim = DimensionScore(name="技术深度", score=0.0, max_score=25.0)
    analysis = data.get("analysis")
    if not isinstance(analysis, dict):
        dim.brief = "缺少 analysis"
        return dim

    score = analysis.get("relevance_score")
    if isinstance(score, (int, float)) and 1 <= score <= 10:
        dim.score = score / 10 * 25
        dim.brief = f"score={score}/10"
    else:
        dim.brief = f"score 无效: {score!r}"

    return dim


def _score_format(data: dict) -> DimensionScore:
    """格式规范评分（满分 20，5 项各 4 分）。"""
    dim = DimensionScore(name="格式规范", score=0.0, max_score=20.0)

    checks = {
        "id": isinstance(data.get("id"), str) and bool(ID_PATTERN.match(data["id"])),
        "title": isinstance(data.get("title"), str) and len(data["title"]) > 0,
        "source_url": isinstance(data.get("source_url"), str) and bool(URL_PATTERN.match(data["source_url"])),
        "status": data.get("status") in VALID_STATUSES,
        "collected_at": isinstance(data.get("collected_at"), str) and bool(TIMESTAMP_PATTERN.match(data["collected_at"])),
    }

    passed = []
    failed = []
    for field_name, ok in checks.items():
        if ok:
            dim.score += 4.0
            passed.append(field_name)
        else:
            failed.append(field_name)

    dim.brief = f"{len(passed)}/5项通过" + (f" 缺{','.join(failed)}" if failed else "")

    return dim


def _score_tags(data: dict) -> DimensionScore:
    """标签精度评分（满分 15）。"""
    dim = DimensionScore(name="标签精度", score=0.0, max_score=15.0)
    tags = data.get("tags")
    if not isinstance(tags, list):
        dim.brief = "tags 不是列表"
        return dim

    count = len(tags)
    if count == 0:
        dim.brief = "无标签"
        return dim

    if 1 <= count <= 3:
        dim.score = 8.0
    elif count <= 5:
        dim.score = 5.0
    else:
        dim.score = 3.0

    valid = [t for t in tags if isinstance(t, str) and t in STANDARD_TAGS]
    tag_bonus = min(len(valid) * (7.0 / max(count, 1)), 7.0)
    dim.score = min(dim.score + tag_bonus, 15.0)

    dim.brief = f"{count}个标签 标准{len(valid)}个"

    return dim


def _score_hollow(data: dict) -> DimensionScore:
    """空洞词检测评分（满分 15，无空洞词即满分）。"""
    dim = DimensionScore(name="空洞词检测", score=15.0, max_score=15.0)

    texts: list[str] = []
    for key in ("title", "summary"):
        val = data.get(key)
        if isinstance(val, str):
            texts.append(val)
    analysis = data.get("analysis")
    if isinstance(analysis, dict):
        for key in ("score_reason",):
            val = analysis.get(key)
            if isinstance(val, str):
                texts.append(val)
        highlights = analysis.get("tech_highlights")
        if isinstance(highlights, list):
            texts.extend(h for h in highlights if isinstance(h, str))

    combined = " ".join(texts).lower()
    found: list[str] = []

    for word in HOLLOW_WORDS_CN:
        if word in combined:
            found.append(word)

    for word in HOLLOW_WORDS_EN:
        if re.search(rf"\b{re.escape(word)}\b", combined):
            found.append(word)

    penalty = min(len(found) * 3.0, 15.0)
    dim.score = max(15.0 - penalty, 0.0)

    if found:
        dim.brief = f"发现{len(found)}个: {', '.join(found)}"
    else:
        dim.brief = "无空洞词"

    return dim


def evaluate_file(path: Path) -> QualityReport | None:
    """评估单个 JSON 文件，返回质量报告；解析失败返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    report = QualityReport(file_path=str(path))
    report.dimensions = [
        _score_summary(data),
        _score_tech_depth(data),
        _score_format(data),
        _score_tags(data),
        _score_hollow(data),
    ]
    return report


def format_report(report: QualityReport) -> str:
    """将报告格式化为可打印文本。"""
    lines = [f"{'=' * 60}", f"文件: {report.file_path}", f"{'-' * 60}"]

    for dim in report.dimensions:
        bar = _bar(dim.ratio)
        line = f"  {dim.name:<8} {bar} {dim.score:5.1f}/{dim.max_score:<3.0f}"
        if dim.brief:
            line += f" {dim.brief}"
        lines.append(line)

    lines.append(f"{'-' * 60}")
    lines.append(
        f"  总分: {report.total_score:.1f}/{report.max_total:.0f}"
        f"  等级: {report.grade}"
    )
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def main() -> None:
    """入口函数：解析参数并执行评分。"""
    if len(sys.argv) < 2:
        print(
            "用法: python hooks/check_quality.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    paths = [Path(arg) for arg in sys.argv[1:]]

    total = 0
    grade_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    has_c = False

    for path in paths:
        if not path.exists():
            print(f"[SKIP] {path} — 文件不存在", file=sys.stderr)
            continue

        report = evaluate_file(path)
        if report is None:
            print(f"[SKIP] {path} — 无法解析", file=sys.stderr)
            continue

        total += 1
        grade_counts[report.grade] += 1
        if report.grade == "C":
            has_c = True
        print(format_report(report))

    print(f"\n--- 评分汇总 ---")
    print(f"总计: {total} | A: {grade_counts['A']} | B: {grade_counts['B']} | C: {grade_counts['C']}")

    sys.exit(1 if has_c else 0)


if __name__ == "__main__":
    main()
