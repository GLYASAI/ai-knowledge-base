"""格式化模块：将知识条目转换为 Markdown、飞书卡片及每日简报。

纯函数模块，不发起任何网络请求。
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_type
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# relevance_score 在 1-10 量纲下的色阶阈值
_SCORE_GREEN = 8
_SCORE_YELLOW = 6


def _score_emoji(score: int | float) -> str:
    """按分值返回颜色指示符。

    Args:
        score: relevance_score，1-10 整数或浮点数。

    Returns:
        🟢 / 🟡 / 🔴 之一。
    """
    if score >= _SCORE_GREEN:
        return "🟢"
    if score >= _SCORE_YELLOW:
        return "🟡"
    return "🔴"


def _score_color(score: int | float) -> str:
    """按分值返回飞书卡片 header.template 颜色名称。

    Args:
        score: relevance_score，1-10 整数或浮点数。

    Returns:
        "green" / "yellow" / "red" 之一。
    """
    if score >= _SCORE_GREEN:
        return "green"
    if score >= _SCORE_YELLOW:
        return "yellow"
    return "red"


def _extract_score(article: dict[str, Any]) -> int | float:
    """从 article 中安全取出 relevance_score，缺失时返回 0。

    Args:
        article: 知识条目 dict。

    Returns:
        relevance_score 数值。
    """
    return article.get("analysis", {}).get("relevance_score", 0)


def json_to_markdown(article: dict[str, Any]) -> str:
    """将单篇知识条目格式化为 Markdown 字符串。

    输出段落依次为：标题、来源、日期、相关性评分、标签、摘要、原文链接。

    Args:
        article: 符合项目知识条目格式的 dict。

    Returns:
        渲染好的 Markdown 字符串。
    """
    title = article.get("title", "（无标题）")
    source_url = article.get("source_url", "")
    collected_at = article.get("collected_at", "")
    date_str = collected_at[:10] if collected_at else "未知"
    score = _extract_score(article)
    emoji = _score_emoji(score)
    tags = article.get("tags", [])
    tags_str = " ".join(f"`{t}`" for t in tags) if tags else "—"
    summary = article.get("summary", "")

    lines = [
        f"## {title}",
        "",
        f"- **来源**: {article.get('source', '未知')}",
        f"- **日期**: {date_str}",
        f"- **相关性评分**: {emoji} {score}/10",
        f"- **标签**: {tags_str}",
        "",
        summary,
        "",
        f"[查看原文]({source_url})" if source_url else "",
    ]
    return "\n".join(line for line in lines if line is not None)


def json_to_feishu(article: dict[str, Any]) -> dict[str, Any]:
    """将单篇知识条目格式化为飞书 interactive 卡片 dict。

    header.template 颜色按 relevance_score 染色：
    green（≥8）/ yellow（≥6）/ red（<6）。

    Args:
        article: 符合项目知识条目格式的 dict。

    Returns:
        可直接序列化为飞书消息体的 dict，msg_type 为 "interactive"。
    """
    title = article.get("title", "（无标题）")
    source_url = article.get("source_url", "")
    collected_at = article.get("collected_at", "")
    date_str = collected_at[:10] if collected_at else "未知"
    score = _extract_score(article)
    color = _score_color(score)
    tags = article.get("tags", [])
    tags_str = "  ".join(f"#{t}" for t in tags) if tags else "—"
    summary = article.get("summary", "")
    source = article.get("source", "未知")

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": summary,
            },
        },
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**来源**\n{source}",
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**日期**\n{date_str}",
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**相关性评分**\n{_score_emoji(score)} {score}/10",
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**标签**\n{tags_str}",
                    },
                },
            ],
        },
    ]

    if source_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看原文"},
                        "type": "default",
                        "url": source_url,
                    }
                ],
            }
        )

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }


def _load_articles_for_date(
    knowledge_dir: Path, date_str: str
) -> list[dict[str, Any]]:
    """从目录中加载指定日期的所有文章，按 relevance_score 降序返回。

    Args:
        knowledge_dir: 知识条目目录的 Path 对象。
        date_str: 日期字符串，格式 YYYY-MM-DD。

    Returns:
        排序后的文章 dict 列表。
    """
    articles: list[dict[str, Any]] = []
    for path in knowledge_dir.glob(f"{date_str}-*.json"):
        try:
            with path.open(encoding="utf-8") as fh:
                articles.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            logger.warning("跳过无法解析的文件: %s", path)
    articles.sort(key=_extract_score, reverse=True)
    return articles


def generate_daily_digest(
    knowledge_dir: str = "knowledge/articles",
    date: str | None = None,
    top_n: int = 5,
) -> dict[str, Any] | str:
    """生成每日知识简报，汇总当日 Top N 条目。

    Args:
        knowledge_dir: 知识条目目录路径（相对或绝对）。
        date: 目标日期，格式 YYYY-MM-DD；None 时默认使用今天。
        top_n: 取评分最高的前 N 篇。

    Returns:
        包含 "markdown"、"feishu" 两个键的 dict；
        当日无文章时返回提示字符串 "📭 {date} 暂无新增知识条目"。
    """
    if date is None:
        date = date_type.today().isoformat()

    dir_path = Path(knowledge_dir)
    articles = _load_articles_for_date(dir_path, date)
    top_articles = articles[:top_n]

    if not top_articles:
        return f"📭 {date} 暂无新增知识条目"

    total = len(articles)
    header_line = f"# 📚 AI 知识日报 · {date}"
    sub_line = f"共收录 {total} 篇，展示 Top {len(top_articles)}"

    # ── Markdown ──────────────────────────────────────────────────────────────
    md_parts = [header_line, "", sub_line, "", "---", ""]
    for i, art in enumerate(top_articles, 1):
        md_parts.append(f"### {i}. {art.get('title', '（无标题）')}")
        md_parts.append("")
        md_parts.append(json_to_markdown(art))
        md_parts.append("")
        md_parts.append("---")
        md_parts.append("")
    markdown_text = "\n".join(md_parts)

    # ── 飞书（单张汇总卡片）────────────────────────────────────────────────────
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": sub_line},
        },
        {"tag": "hr"},
    ]
    for i, art in enumerate(top_articles, 1):
        title = art.get("title", "（无标题）")
        source_url = art.get("source_url", "")
        score = _extract_score(art)
        tags = art.get("tags", [])
        tags_str = "  ".join(f"#{t}" for t in tags) if tags else ""
        summary = art.get("summary", "")
        title_link = f"[{title}]({source_url})" if source_url else title
        body = f"**{i}. {title_link}**\n{_score_emoji(score)} {score}/10  {tags_str}\n{summary}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body}})
        if i < len(top_articles):
            elements.append({"tag": "hr"})

    feishu_digest = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📚 AI 知识日报 · {date}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }

    return {
        "date": date,
        "articles": top_articles,
        "markdown": markdown_text,
        "feishu": feishu_digest,
    }
