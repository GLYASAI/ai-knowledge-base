"""AI 知识库评估测试 — 验证 LLM 分析质量。"""

from __future__ import annotations

import warnings

import pytest
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", category=pytest.PytestUnknownMarkWarning)

from workflows.model_client import chat, chat_json  # noqa: E402

# ---------------------------------------------------------------------------
# 评估用例
# ---------------------------------------------------------------------------

EVAL_CASES = [
    {
        "name": "正面案例：技术文章",
        "input": {
            "title": "vLLM: 高性能 LLM 推理引擎",
            "description": "vLLM 使用 PagedAttention 实现高吞吐量 LLM 推理，支持连续批处理和多种模型架构。",
            "stars": 35000,
            "language": "Python",
        },
        "expected": {
            "has_summary": True,
            "min_relevance": 7,
            "keywords": ["LLM", "推理"],
        },
    },
    {
        "name": "负面案例：无关内容",
        "input": {
            "title": "awesome-recipes",
            "description": "A collection of cooking recipes from around the world, including Italian pasta and Japanese sushi. No AI or machine learning content.",
            "stars": 500,
            "language": "Markdown",
        },
        "expected": {
            "has_summary": True,
            "max_relevance": 5,
            "keywords": [],
        },
    },
    {
        "name": "边界案例：极短输入",
        "input": {
            "title": "AI",
            "description": "",
            "stars": 0,
            "language": "",
        },
        "expected": {
            "has_summary": True,
            "no_crash": True,
        },
    },
]


def _build_prompt(item: dict) -> str:
    """构造分析 prompt。"""
    parts = [f"项目: {item.get('title', '')}"]
    if item.get("description"):
        parts.append(f"描述: {item['description']}")
    if item.get("stars"):
        parts.append(f"Star 数: {item['stars']}")
    if item.get("language"):
        parts.append(f"语言: {item['language']}")
    return "\n".join(parts)


ANALYZE_SYSTEM = """\
你是一个 AI 技术分析助手。给定一个项目信息，请用 JSON 格式回复：
{
  "summary": "中文摘要（20-100 字）",
  "relevance_score": 8,
  "tags": ["tag1", "tag2"]
}

relevance_score 评分标准（1-10）：
- 9-10: 与 AI/LLM/Agent 领域直接相关的核心项目
- 7-8: 与 AI 相关但非核心
- 5-6: 有一定关联
- 3-4: 关联较弱
- 1-2: 与 AI/LLM/Agent 完全无关（如烹饪、体育、音乐等）"""


# ---------------------------------------------------------------------------
# 本地验证测试（不调用 LLM）
# ---------------------------------------------------------------------------


def test_eval_cases_structure():
    """验证 EVAL_CASES 的数据结构完整性。"""
    assert len(EVAL_CASES) >= 3

    for case in EVAL_CASES:
        assert "name" in case, "缺少 name 字段"
        assert "input" in case, "缺少 input 字段"
        assert "expected" in case, "缺少 expected 字段"
        assert isinstance(case["input"], dict)
        assert isinstance(case["expected"], dict)
        assert "title" in case["input"], "input 缺少 title"


# ---------------------------------------------------------------------------
# LLM 评估测试
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    "case",
    EVAL_CASES,
    ids=[c["name"] for c in EVAL_CASES],
)
def test_analyze_quality(case: dict):
    """对每个评估用例调用 LLM 并验证输出质量。"""
    prompt = _build_prompt(case["input"])
    result, usage = chat_json(prompt, system=ANALYZE_SYSTEM)
    expected = case["expected"]

    assert isinstance(result, dict), "LLM 返回非 dict"

    if expected.get("has_summary"):
        summary = result.get("summary", "")
        assert len(summary) > 0, "摘要为空"

    if expected.get("no_crash"):
        assert result is not None

    if "min_relevance" in expected:
        score = result.get("relevance_score", 0)
        assert score >= expected["min_relevance"], (
            f"relevance_score {score} < 预期最低 {expected['min_relevance']}"
        )

    if "max_relevance" in expected:
        score = result.get("relevance_score", 10)
        assert score <= expected["max_relevance"], (
            f"relevance_score {score} > 预期最高 {expected['max_relevance']}"
        )

    if expected.get("keywords"):
        summary = result.get("summary", "")
        tags = " ".join(result.get("tags", []))
        combined = summary + " " + tags
        for kw in expected["keywords"]:
            assert kw.lower() in combined.lower(), (
                f"关键词 '{kw}' 未出现在摘要或标签中"
            )


@pytest.mark.slow
def test_llm_as_judge():
    """LLM-as-Judge：让 LLM 对分析结果打分。"""
    # 先生成一条分析结果
    prompt = _build_prompt(EVAL_CASES[0]["input"])
    analysis, _ = chat_json(prompt, system=ANALYZE_SYSTEM)

    # 让 LLM 作为裁判评分
    judge_prompt = (
        f"请对以下 AI 技术分析结果的质量打分（1-10 分），只返回一个整数：\n\n"
        f"原始输入：{EVAL_CASES[0]['input']['title']} - {EVAL_CASES[0]['input']['description']}\n\n"
        f"分析结果：{analysis}\n\n"
        f"评分标准：摘要是否准确简洁、相关性评分是否合理、标签是否恰当。\n"
        f"请只返回一个 1-10 的整数。"
    )
    score_text, _ = chat(judge_prompt, system="你是一个评审专家，只返回一个整数分数。")
    score = int("".join(c for c in score_text.strip() if c.isdigit())[:2])

    assert 1 <= score <= 10, f"分数超出范围: {score}"
    assert score >= 5, f"LLM-as-Judge 评分过低: {score}/10"
