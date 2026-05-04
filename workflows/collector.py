"""采集节点 — 从 GitHub 和 RSS 源采集 AI 相关内容。"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from tests.security import sanitize_input
from workflows.state import KBState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
RSS_CONFIG_PATH = BASE_DIR / "pipeline" / "rss_sources.yaml"

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "AI OR LLM OR agent OR GPT language:python"

DEFAULT_GITHUB_LIMIT = 20
DEFAULT_RSS_LIMIT = 10


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """通过 urllib 发起 GET 请求并返回 JSON。"""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _http_get_text(url: str) -> str:
    """通过 urllib 发起 GET 请求并返回文本。"""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def _strip_cdata(text: str) -> str:
    """移除 CDATA 包裹。"""
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    return text.strip()


def _strip_html(text: str) -> str:
    """移除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss_xml(xml_text: str, limit: int) -> list[dict[str, str]]:
    """用正则从 RSS/Atom XML 中提取条目。"""
    entries: list[dict[str, str]] = []

    item_pattern = re.compile(
        r"<(?:item|entry)[\s>].*?</(?:item|entry)>", re.DOTALL,
    )
    title_pattern = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL)
    link_pattern = re.compile(
        r'<link[^>]*href=["\']([^"\']+)["\']|<link[^>]*>(.*?)</link>',
        re.DOTALL,
    )
    desc_pattern = re.compile(
        r"<(?:description|summary|content)[^>]*>(.*?)"
        r"</(?:description|summary|content)>",
        re.DOTALL,
    )

    for match in item_pattern.finditer(xml_text):
        if len(entries) >= limit:
            break
        block = match.group(0)

        title_m = title_pattern.search(block)
        title = _strip_cdata(title_m.group(1)) if title_m else ""

        link_m = link_pattern.search(block)
        link = ""
        if link_m:
            link = link_m.group(1) or link_m.group(2) or ""
        link = link.strip()

        desc_m = desc_pattern.search(block)
        desc = _strip_cdata(desc_m.group(1))[:500] if desc_m else ""

        if title and link:
            entries.append({
                "title": _strip_html(title),
                "url": link,
                "description": _strip_html(desc),
            })

    return entries


def _collect_github(limit: int) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关仓库。"""
    logger.info("[Collect] GitHub 采集，limit=%d", limit)
    results: list[dict[str, Any]] = []
    try:
        params = (
            f"?q={urllib.request.quote(GITHUB_SEARCH_QUERY)}"
            f"&sort=stars&order=desc&per_page={min(limit, 100)}"
        )
        data = _http_get_json(
            GITHUB_SEARCH_API + params,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        for item in data.get("items", [])[:limit]:
            results.append({
                "source": "github",
                "title": item.get("full_name", ""),
                "url": item.get("html_url", ""),
                "description": item.get("description") or "",
                "stars": item.get("stargazers_count", 0),
                "language": item.get("language") or "",
                "topics": item.get("topics", []),
            })
    except Exception as exc:
        logger.warning("[Collect] GitHub 采集失败: %s", exc)
    logger.info("[Collect] GitHub 采集完成，%d 条", len(results))
    return results


def _collect_rss(limit: int) -> list[dict[str, Any]]:
    """从已配置的 RSS 源采集内容。"""
    logger.info("[Collect] RSS 采集，limit=%d", limit)
    results: list[dict[str, Any]] = []
    if not RSS_CONFIG_PATH.exists():
        logger.warning("[Collect] RSS 配置文件不存在: %s", RSS_CONFIG_PATH)
        return results

    config = yaml.safe_load(RSS_CONFIG_PATH.read_text(encoding="utf-8"))
    enabled = [s for s in config.get("sources", []) if s.get("enabled")]
    remaining = limit

    for src in enabled:
        if remaining <= 0:
            break
        try:
            xml_text = _http_get_text(src["url"])
            entries = _parse_rss_xml(xml_text, remaining)
            for entry in entries:
                results.append({
                    "source": "rss",
                    "rss_name": src["name"],
                    "category": src.get("category", ""),
                    "title": entry["title"],
                    "url": entry["url"],
                    "description": entry["description"],
                })
            remaining -= len(entries)
        except Exception as exc:
            logger.warning("[Collect] RSS '%s' 失败: %s", src["name"], exc)

    logger.info("[Collect] RSS 采集完成，%d 条", len(results))
    return results


def collect_node(state: KBState) -> dict[str, Any]:
    """采集 GitHub 仓库和 RSS 源的 AI 相关内容。"""
    github_limit = int(os.getenv("GITHUB_LIMIT", str(DEFAULT_GITHUB_LIMIT)))
    rss_limit = int(os.getenv("RSS_LIMIT", str(DEFAULT_RSS_LIMIT)))

    logger.info("[Collect] 开始采集，github_limit=%d, rss_limit=%d", github_limit, rss_limit)
    sources: list[dict[str, Any]] = []
    sources.extend(_collect_github(github_limit))
    sources.extend(_collect_rss(rss_limit))

    # 对采集到的文本字段做输入清洗
    for item in sources:
        for field in ("title", "description"):
            if item.get(field):
                cleaned, warns = sanitize_input(item[field])
                if warns:
                    logger.warning("[Collect] 清洗 %s: %s", item.get("url", ""), warns)
                item[field] = cleaned

    logger.info("[Collect] 采集完成，共 %d 条", len(sources))
    return {"sources": sources}
