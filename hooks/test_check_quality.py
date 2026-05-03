"""check_quality.py 的单元测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_quality import (
    DimensionScore,
    QualityReport,
    evaluate_file,
    format_report,
    _score_summary,
    _score_tech_depth,
    _score_format,
    _score_tags,
    _score_hollow,
)

VALID_ENTRY = {
    "id": "github-20260317-001",
    "title": "OpenClaw: 开源 AI Agent 运行时",
    "source": "github-trending",
    "source_url": "https://github.com/example/project",
    "collected_at": "2026-03-01T10:00:00Z",
    "summary": "一款开源 AI Agent 运行时框架，支持多平台部署与路由调度，适合构建企业级智能体系统。",
    "analysis": {
        "tech_highlights": ["多 Agent 路由", "50+ 平台支持"],
        "relevance_score": 9,
    },
    "tags": ["agent", "runtime", "open-source"],
    "status": "draft",
}


def _write_json(data, *, raw_text: str | None = None) -> Path:
    """将数据写入临时 JSON 文件并返回路径。"""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".json", mode="w", encoding="utf-8", delete=False,
    )
    tmp.write(raw_text if raw_text is not None else json.dumps(data, ensure_ascii=False))
    tmp.close()
    return Path(tmp.name)


def _make_entry(**overrides) -> dict:
    """基于合法条目生成变体。"""
    entry = {**VALID_ENTRY, **overrides}
    return entry


# ---------- dataclass ----------

class TestDimensionScore(unittest.TestCase):

    def test_ratio_normal(self):
        d = DimensionScore(name="test", score=15.0, max_score=25.0)
        self.assertAlmostEqual(d.ratio, 0.6)

    def test_ratio_zero_max(self):
        d = DimensionScore(name="test", score=0.0, max_score=0.0)
        self.assertEqual(d.ratio, 0.0)

    def test_brief_default_empty(self):
        d = DimensionScore(name="test", score=0.0, max_score=10.0)
        self.assertEqual(d.brief, "")


class TestQualityReport(unittest.TestCase):

    def test_grade_a(self):
        r = QualityReport(file_path="test.json", dimensions=[
            DimensionScore("d1", 80.0, 100.0),
        ])
        self.assertEqual(r.grade, "A")

    def test_grade_b(self):
        r = QualityReport(file_path="test.json", dimensions=[
            DimensionScore("d1", 70.0, 100.0),
        ])
        self.assertEqual(r.grade, "B")

    def test_grade_c(self):
        r = QualityReport(file_path="test.json", dimensions=[
            DimensionScore("d1", 50.0, 100.0),
        ])
        self.assertEqual(r.grade, "C")

    def test_grade_boundary_80(self):
        r = QualityReport(file_path="t", dimensions=[
            DimensionScore("d1", 80.0, 100.0),
        ])
        self.assertEqual(r.grade, "A")

    def test_grade_boundary_60(self):
        r = QualityReport(file_path="t", dimensions=[
            DimensionScore("d1", 60.0, 100.0),
        ])
        self.assertEqual(r.grade, "B")

    def test_total_score(self):
        r = QualityReport(file_path="t", dimensions=[
            DimensionScore("d1", 10.0, 25.0),
            DimensionScore("d2", 20.0, 25.0),
        ])
        self.assertAlmostEqual(r.total_score, 30.0)
        self.assertAlmostEqual(r.max_total, 50.0)


# ---------- 摘要质量 ----------

class TestScoreSummary(unittest.TestCase):

    def test_long_summary_full_base(self):
        dim = _score_summary({"summary": "a" * 50})
        self.assertEqual(dim.max_score, 25.0)
        self.assertGreaterEqual(dim.score, 15.0)
        self.assertIn("50字", dim.brief)

    def test_medium_summary_base(self):
        dim = _score_summary({"summary": "a" * 25})
        self.assertGreaterEqual(dim.score, 10.0)
        self.assertIn("25字", dim.brief)

    def test_short_summary_low(self):
        dim = _score_summary({"summary": "短"})
        self.assertLess(dim.score, 5.0)

    def test_tech_keywords_bonus(self):
        dim = _score_summary({"summary": "基于 LLM 的 Agent 框架支持 RAG 和向量检索的推理能力很强很强很强"})
        self.assertGreater(dim.score, 15.0)
        self.assertIn("关键词", dim.brief)

    def test_no_summary(self):
        dim = _score_summary({})
        self.assertEqual(dim.score, 0.0)

    def test_non_string_summary(self):
        dim = _score_summary({"summary": 123})
        self.assertEqual(dim.score, 0.0)
        self.assertIn("不是字符串", dim.brief)

    def test_score_capped_at_25(self):
        dim = _score_summary({"summary": "Agent LLM RAG 模型 框架 向量 推理 部署 架构 Token embedding fine-tuning inference SDK" * 5})
        self.assertLessEqual(dim.score, 25.0)


# ---------- 技术深度 ----------

class TestScoreTechDepth(unittest.TestCase):

    def test_score_10(self):
        dim = _score_tech_depth({"analysis": {"relevance_score": 10}})
        self.assertAlmostEqual(dim.score, 25.0)
        self.assertIn("score=10/10", dim.brief)

    def test_score_1(self):
        dim = _score_tech_depth({"analysis": {"relevance_score": 1}})
        self.assertAlmostEqual(dim.score, 2.5)

    def test_score_5(self):
        dim = _score_tech_depth({"analysis": {"relevance_score": 5}})
        self.assertAlmostEqual(dim.score, 12.5)

    def test_no_analysis(self):
        dim = _score_tech_depth({})
        self.assertEqual(dim.score, 0.0)
        self.assertIn("缺少", dim.brief)

    def test_missing_relevance_score(self):
        dim = _score_tech_depth({"analysis": {}})
        self.assertEqual(dim.score, 0.0)

    def test_invalid_score(self):
        dim = _score_tech_depth({"analysis": {"relevance_score": "high"}})
        self.assertEqual(dim.score, 0.0)
        self.assertIn("无效", dim.brief)

    def test_out_of_range_score(self):
        dim = _score_tech_depth({"analysis": {"relevance_score": 11}})
        self.assertEqual(dim.score, 0.0)


# ---------- 格式规范 ----------

class TestScoreFormat(unittest.TestCase):

    def test_all_valid(self):
        dim = _score_format(VALID_ENTRY)
        self.assertAlmostEqual(dim.score, 20.0)
        self.assertIn("5/5项通过", dim.brief)

    def test_bad_id(self):
        entry = _make_entry(id="bad-id-format")
        dim = _score_format(entry)
        self.assertAlmostEqual(dim.score, 16.0)
        self.assertIn("4/5项通过", dim.brief)
        self.assertIn("id", dim.brief)

    def test_bad_url(self):
        entry = _make_entry(source_url="not-a-url")
        dim = _score_format(entry)
        self.assertAlmostEqual(dim.score, 16.0)

    def test_bad_status(self):
        entry = _make_entry(status="pending")
        dim = _score_format(entry)
        self.assertAlmostEqual(dim.score, 16.0)

    def test_missing_collected_at(self):
        entry = {**VALID_ENTRY}
        del entry["collected_at"]
        dim = _score_format(entry)
        self.assertAlmostEqual(dim.score, 16.0)

    def test_all_bad(self):
        entry = {
            "id": 123,
            "title": "",
            "source_url": "ftp://bad",
            "status": "unknown",
        }
        dim = _score_format(entry)
        self.assertAlmostEqual(dim.score, 0.0)
        self.assertIn("0/5项通过", dim.brief)


# ---------- 标签精度 ----------

class TestScoreTags(unittest.TestCase):

    def test_three_standard_tags(self):
        dim = _score_tags({"tags": ["agent", "runtime", "open-source"]})
        self.assertAlmostEqual(dim.score, 15.0)
        self.assertIn("3个标签", dim.brief)
        self.assertIn("标准3个", dim.brief)

    def test_single_standard_tag(self):
        dim = _score_tags({"tags": ["agent"]})
        self.assertGreaterEqual(dim.score, 10.0)

    def test_no_standard_tags(self):
        dim = _score_tags({"tags": ["custom-tag-1", "custom-tag-2"]})
        self.assertAlmostEqual(dim.score, 8.0)
        self.assertIn("标准0个", dim.brief)

    def test_empty_tags(self):
        dim = _score_tags({"tags": []})
        self.assertEqual(dim.score, 0.0)
        self.assertIn("无标签", dim.brief)

    def test_too_many_tags(self):
        dim = _score_tags({"tags": ["a", "b", "c", "d", "e", "f", "g"]})
        self.assertLessEqual(dim.score, 10.0)

    def test_tags_not_list(self):
        dim = _score_tags({"tags": "agent"})
        self.assertEqual(dim.score, 0.0)
        self.assertIn("不是列表", dim.brief)

    def test_four_tags_moderate(self):
        dim = _score_tags({"tags": ["agent", "llm", "rag", "extra"]})
        self.assertGreater(dim.score, 5.0)


# ---------- 空洞词检测 ----------

class TestScoreHollow(unittest.TestCase):

    def test_clean_text(self):
        dim = _score_hollow(VALID_ENTRY)
        self.assertAlmostEqual(dim.score, 15.0)
        self.assertIn("无空洞词", dim.brief)

    def test_cn_hollow_word(self):
        entry = _make_entry(summary="这个框架通过赋能开发者实现了全链路的智能化部署方案非常有价值")
        dim = _score_hollow(entry)
        self.assertLess(dim.score, 15.0)
        self.assertIn("发现", dim.brief)

    def test_en_hollow_word(self):
        entry = _make_entry(summary="This is a groundbreaking revolutionary framework for building agents and more")
        dim = _score_hollow(entry)
        self.assertLess(dim.score, 15.0)

    def test_multiple_hollow_words(self):
        entry = _make_entry(summary="赋能开发者打通闭环实现全链路底层逻辑的颗粒度对齐拉通沉淀非常有价值的东西")
        dim = _score_hollow(entry)
        self.assertEqual(dim.score, 0.0)

    def test_hollow_in_analysis(self):
        entry = _make_entry(analysis={
            "score_reason": "这是一个革命性的项目",
            "tech_highlights": ["打通上下游数据链路"],
        })
        dim = _score_hollow(entry)
        self.assertLess(dim.score, 15.0)

    def test_penalty_per_word(self):
        entry = _make_entry(summary="赋能开发者的非常实用的一个框架，适用于很多场景，性能很好很不错")
        dim = _score_hollow(entry)
        self.assertAlmostEqual(dim.score, 12.0)

    def test_no_summary_no_penalty(self):
        dim = _score_hollow({})
        self.assertAlmostEqual(dim.score, 15.0)


# ---------- evaluate_file ----------

class TestEvaluateFile(unittest.TestCase):

    def test_valid_file(self):
        path = _write_json(VALID_ENTRY)
        report = evaluate_file(path)
        self.assertIsNotNone(report)
        self.assertEqual(len(report.dimensions), 5)
        self.assertGreater(report.total_score, 0)

    def test_invalid_json(self):
        path = _write_json(None, raw_text="{bad json!")
        report = evaluate_file(path)
        self.assertIsNone(report)

    def test_not_dict(self):
        path = _write_json(None, raw_text="[1,2,3]")
        report = evaluate_file(path)
        self.assertIsNone(report)

    def test_nonexistent_file(self):
        report = evaluate_file(Path("/tmp/nonexistent_quality_test.json"))
        self.assertIsNone(report)

    def test_high_quality_entry(self):
        entry = _make_entry(
            summary="基于 LLM 和 RAG 的 Agent 推理框架，支持向量检索和模型部署，可用于构建复杂的多智能体协作系统，支持企业级场景。",
            analysis={"relevance_score": 10},
            tags=["agent", "llm", "rag"],
        )
        path = _write_json(entry)
        report = evaluate_file(path)
        self.assertEqual(report.grade, "A")

    def test_low_quality_entry(self):
        entry = {
            "id": "bad",
            "title": "",
            "source_url": "not-url",
            "summary": "短",
            "tags": [],
            "status": "unknown",
        }
        path = _write_json(entry)
        report = evaluate_file(path)
        self.assertEqual(report.grade, "C")


# ---------- format_report ----------

class TestFormatReport(unittest.TestCase):

    def test_contains_grade(self):
        path = _write_json(VALID_ENTRY)
        report = evaluate_file(path)
        text = format_report(report)
        self.assertIn("等级:", text)

    def test_contains_progress_bar(self):
        path = _write_json(VALID_ENTRY)
        report = evaluate_file(path)
        text = format_report(report)
        self.assertTrue("█" in text or "░" in text)

    def test_contains_all_dimensions(self):
        path = _write_json(VALID_ENTRY)
        report = evaluate_file(path)
        text = format_report(report)
        for name in ("摘要质量", "技术深度", "格式规范", "标签精度", "空洞词检测"):
            self.assertIn(name, text)

    def test_each_dimension_is_single_line(self):
        path = _write_json(VALID_ENTRY)
        report = evaluate_file(path)
        text = format_report(report)
        dim_names = ("摘要质量", "技术深度", "格式规范", "标签精度", "空洞词检测")
        dim_lines = [l for l in text.splitlines() if any(n in l for n in dim_names)]
        self.assertEqual(len(dim_lines), 5)
        for line in dim_lines:
            self.assertIn("█", line)
            self.assertIn("/", line)


# ---------- CLI ----------

class TestMainCli(unittest.TestCase):

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """运行 check_quality.py 并返回结果。"""
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent / "check_quality.py"), *args],
            capture_output=True, text=True,
        )

    def test_no_args_exit_1(self):
        result = self._run()
        self.assertEqual(result.returncode, 1)

    def test_good_file_exit_0(self):
        entry = _make_entry(
            summary="基于 LLM 和 RAG 的 Agent 推理框架，支持向量检索和模型部署，可用于构建复杂的多智能体协作系统。",
            analysis={"relevance_score": 10},
            tags=["agent", "llm", "rag"],
        )
        path = _write_json(entry)
        result = self._run(str(path))
        self.assertEqual(result.returncode, 0)

    def test_bad_file_exit_1(self):
        entry = {"id": "x", "title": "", "source_url": "x", "summary": "短", "tags": [], "status": "x"}
        path = _write_json(entry)
        result = self._run(str(path))
        self.assertEqual(result.returncode, 1)

    def test_summary_line(self):
        path = _write_json(VALID_ENTRY)
        result = self._run(str(path))
        self.assertIn("评分汇总", result.stdout)
        self.assertIn("总计:", result.stdout)

    def test_multiple_files(self):
        good = _write_json(_make_entry(
            summary="基于 LLM 和 RAG 的 Agent 推理框架，支持向量检索和模型部署，可用于构建复杂的多智能体协作系统。",
            analysis={"relevance_score": 10},
            tags=["agent", "llm", "rag"],
        ))
        bad = _write_json({"id": "x", "title": "", "source_url": "x", "summary": "短", "tags": [], "status": "x"})
        result = self._run(str(good), str(bad))
        self.assertEqual(result.returncode, 1)
        self.assertIn("A: 1", result.stdout)
        self.assertIn("C: 1", result.stdout)


if __name__ == "__main__":
    unittest.main()
