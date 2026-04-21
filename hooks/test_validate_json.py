"""validate_json.py 的单元测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_json import validate_file

VALID_ENTRY = {
    "id": "github-20260317-001",
    "title": "OpenClaw: 开源 AI Agent 运行时",
    "source_url": "https://github.com/example/project",
    "summary": "一款开源 AI Agent 运行时框架，支持多平台部署与路由调度，适合构建企业级智能体系统。",
    "tags": ["agent", "runtime"],
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


class TestValidEntry(unittest.TestCase):
    """合法条目应零错误。"""

    def test_pass(self):
        path = _write_json(VALID_ENTRY)
        self.assertEqual(validate_file(path), [])

    def test_all_statuses(self):
        for status in ("draft", "review", "published", "archived"):
            path = _write_json(_make_entry(status=status))
            self.assertEqual(validate_file(path), [], f"status={status} should pass")

    def test_with_analysis(self):
        entry = _make_entry(analysis={
            "relevance_score": 8,
            "audience": "intermediate",
        })
        path = _write_json(entry)
        self.assertEqual(validate_file(path), [])


class TestJsonParsing(unittest.TestCase):
    """JSON 解析失败。"""

    def test_invalid_json(self):
        path = _write_json(None, raw_text="{not valid json!!!")
        errors = validate_file(path)
        self.assertEqual(len(errors), 1)
        self.assertIn("JSON 解析失败", errors[0])

    def test_top_level_array(self):
        path = _write_json(None, raw_text="[1, 2, 3]")
        errors = validate_file(path)
        self.assertIn("顶层结构必须是 JSON 对象", errors)


class TestRequiredFields(unittest.TestCase):
    """缺少必填字段。"""

    def test_missing_single_field(self):
        for field in ("id", "title", "source_url", "summary", "tags", "status"):
            entry = {**VALID_ENTRY}
            del entry[field]
            path = _write_json(entry)
            errors = validate_file(path)
            matched = [e for e in errors if f"缺少必填字段: {field}" in e]
            self.assertTrue(matched, f"should detect missing '{field}'")

    def test_missing_all_fields(self):
        path = _write_json({})
        errors = validate_file(path)
        missing = [e for e in errors if "缺少必填字段" in e]
        self.assertEqual(len(missing), 6)


class TestFieldTypes(unittest.TestCase):
    """字段类型错误。"""

    def test_id_not_string(self):
        path = _write_json(_make_entry(id=12345))
        errors = validate_file(path)
        matched = [e for e in errors if "字段 'id' 类型错误" in e]
        self.assertEqual(len(matched), 1)

    def test_tags_not_list(self):
        path = _write_json(_make_entry(tags="not-a-list"))
        errors = validate_file(path)
        matched = [e for e in errors if "字段 'tags' 类型错误" in e]
        self.assertEqual(len(matched), 1)

    def test_status_not_string(self):
        path = _write_json(_make_entry(status=123))
        errors = validate_file(path)
        matched = [e for e in errors if "字段 'status' 类型错误" in e]
        self.assertEqual(len(matched), 1)


class TestIdFormat(unittest.TestCase):
    """ID 格式校验。"""

    def test_valid_ids(self):
        for id_val in ("github-20260317-001", "hn-20261231-999"):
            path = _write_json(_make_entry(id=id_val))
            errors = validate_file(path)
            self.assertEqual(errors, [], f"id={id_val} should pass")

    def test_old_format_rejected(self):
        path = _write_json(_make_entry(id="2026-04-19-github-voice-skill"))
        errors = validate_file(path)
        matched = [e for e in errors if "ID 格式错误" in e]
        self.assertEqual(len(matched), 1)

    def test_missing_sequence_number(self):
        path = _write_json(_make_entry(id="github-20260317"))
        errors = validate_file(path)
        matched = [e for e in errors if "ID 格式错误" in e]
        self.assertEqual(len(matched), 1)

    def test_uppercase_source_rejected(self):
        path = _write_json(_make_entry(id="GitHub-20260317-001"))
        errors = validate_file(path)
        matched = [e for e in errors if "ID 格式错误" in e]
        self.assertEqual(len(matched), 1)


class TestStatus(unittest.TestCase):
    """无效 status 值。"""

    def test_invalid_status(self):
        path = _write_json(_make_entry(status="pending"))
        errors = validate_file(path)
        matched = [e for e in errors if "status 值无效" in e]
        self.assertEqual(len(matched), 1)

    def test_empty_status(self):
        path = _write_json(_make_entry(status=""))
        errors = validate_file(path)
        matched = [e for e in errors if "status 值无效" in e]
        self.assertEqual(len(matched), 1)


class TestUrl(unittest.TestCase):
    """URL 格式校验。"""

    def test_http_url(self):
        path = _write_json(_make_entry(source_url="http://example.com"))
        self.assertEqual(validate_file(path), [])

    def test_https_url(self):
        path = _write_json(_make_entry(source_url="https://example.com/path"))
        self.assertEqual(validate_file(path), [])

    def test_ftp_rejected(self):
        path = _write_json(_make_entry(source_url="ftp://example.com"))
        errors = validate_file(path)
        matched = [e for e in errors if "source_url 格式错误" in e]
        self.assertEqual(len(matched), 1)

    def test_no_scheme_rejected(self):
        path = _write_json(_make_entry(source_url="example.com"))
        errors = validate_file(path)
        matched = [e for e in errors if "source_url 格式错误" in e]
        self.assertEqual(len(matched), 1)


class TestSummaryLength(unittest.TestCase):
    """摘要长度校验。"""

    def test_too_short(self):
        path = _write_json(_make_entry(summary="太短了"))
        errors = validate_file(path)
        matched = [e for e in errors if "摘要过短" in e]
        self.assertEqual(len(matched), 1)

    def test_exactly_20_chars(self):
        path = _write_json(_make_entry(summary="a" * 20))
        errors = validate_file(path)
        self.assertFalse([e for e in errors if "摘要过短" in e])


class TestTagsNotEmpty(unittest.TestCase):
    """标签至少 1 个。"""

    def test_empty_tags(self):
        path = _write_json(_make_entry(tags=[]))
        errors = validate_file(path)
        matched = [e for e in errors if "tags 不能为空" in e]
        self.assertEqual(len(matched), 1)


class TestAnalysisOptionalFields(unittest.TestCase):
    """analysis 中的可选字段。"""

    def test_score_out_of_range_high(self):
        entry = _make_entry(analysis={"relevance_score": 11})
        path = _write_json(entry)
        errors = validate_file(path)
        matched = [e for e in errors if "relevance_score 超出范围" in e]
        self.assertEqual(len(matched), 1)

    def test_score_out_of_range_low(self):
        entry = _make_entry(analysis={"relevance_score": 0})
        path = _write_json(entry)
        errors = validate_file(path)
        matched = [e for e in errors if "relevance_score 超出范围" in e]
        self.assertEqual(len(matched), 1)

    def test_score_boundary_valid(self):
        for score in (1, 5, 10):
            entry = _make_entry(analysis={"relevance_score": score})
            path = _write_json(entry)
            errors = validate_file(path)
            self.assertFalse(
                [e for e in errors if "relevance_score" in e],
                f"score={score} should pass",
            )

    def test_score_not_number(self):
        entry = _make_entry(analysis={"relevance_score": "high"})
        path = _write_json(entry)
        errors = validate_file(path)
        matched = [e for e in errors if "relevance_score 超出范围" in e]
        self.assertEqual(len(matched), 1)

    def test_invalid_audience(self):
        entry = _make_entry(analysis={"audience": "expert"})
        path = _write_json(entry)
        errors = validate_file(path)
        matched = [e for e in errors if "audience 值无效" in e]
        self.assertEqual(len(matched), 1)

    def test_valid_audiences(self):
        for aud in ("beginner", "intermediate", "advanced"):
            entry = _make_entry(analysis={"audience": aud})
            path = _write_json(entry)
            errors = validate_file(path)
            self.assertFalse(
                [e for e in errors if "audience" in e],
                f"audience={aud} should pass",
            )

    def test_no_analysis_is_fine(self):
        path = _write_json(VALID_ENTRY)
        self.assertEqual(validate_file(path), [])


class TestFileErrors(unittest.TestCase):
    """文件级错误。"""

    def test_nonexistent_file(self):
        path = Path("/tmp/does_not_exist_at_all.json")
        errors = validate_file(path)
        self.assertEqual(len(errors), 1)
        self.assertIn("无法读取文件", errors[0])


class TestMainCli(unittest.TestCase):
    """CLI 入口集成测试。"""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """运行 validate_json.py 并返回结果。"""
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent / "validate_json.py"), *args],
            capture_output=True, text=True,
        )

    def test_pass_exit_0(self):
        path = _write_json(VALID_ENTRY)
        result = self._run(str(path))
        self.assertEqual(result.returncode, 0)
        self.assertIn("[PASS]", result.stderr)

    def test_fail_exit_1(self):
        path = _write_json({})
        result = self._run(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_no_args_exit_1(self):
        result = self._run()
        self.assertEqual(result.returncode, 1)

    def test_multiple_files(self):
        good = _write_json(VALID_ENTRY)
        bad = _write_json({})
        result = self._run(str(good), str(bad))
        self.assertEqual(result.returncode, 1)
        self.assertIn("通过: 1", result.stderr)
        self.assertIn("失败: 1", result.stderr)

    def test_summary_line(self):
        path = _write_json(VALID_ENTRY)
        result = self._run(str(path))
        self.assertIn("总计: 1 | 通过: 1 | 失败: 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
