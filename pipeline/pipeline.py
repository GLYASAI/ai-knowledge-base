"""四步知识库自动化流水线：采集 → 分析 → 整理 → 保存。

用法:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --sources rss --limit 10 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from model_client import chat_with_retry, get_provider, calculate_cost

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "knowledge" / "raw"
ARTICLES_DIR = BASE_DIR / "knowledge" / "articles"
RSS_CONFIG_PATH = Path(__file__).resolve().parent / "rss_sources.yaml"

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
GITHUB_SEARCH_QUERY = "AI OR LLM OR agent OR GPT language:python"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TODAY_COMPACT = TODAY.replace("-", "")

ANALYZE_SYSTEM_PROMPT = """\
你是一个 AI 技术分析助手。给定一个开源项目或技术文章的信息，你需要：
1. 用中文写一句话摘要（20-100 字），技术术语保留英文
2. 提取 2-3 个技术亮点（tech_highlights）
3. 给出 relevance_score（1-10 分）和一句评分理由
4. 推荐 2-5 个英文标签（小写，用连字符连接）
5. 判断目标受众: beginner / intermediate / advanced

请严格以 JSON 格式回复，不要添加其他文本：
{
  "summary": "...",
  "tech_highlights": ["...", "..."],
  "relevance_score": 8,
  "score_reason": "...",
  "tags": ["...", "..."],
  "audience": "intermediate"
}"""


# ---------------------------------------------------------------------------
# Step 1: 采集 (Collect)
# ---------------------------------------------------------------------------


def _http_get(url: str, **kwargs: Any) -> httpx.Response:
    """带重试的 HTTP GET，处理代理/SSL 间歇性故障。"""
    kwargs.setdefault("timeout", 30.0)
    kwargs.setdefault("follow_redirects", True)
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = httpx.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.ConnectError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < 3:
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "HTTP GET 失败 (%d/3)，%ds 后重试: %s", attempt, wait, exc,
                )
                import time
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def collect_github(limit: int) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关仓库。"""
    logger.info("从 GitHub 采集，limit=%d", limit)
    params = {
        "q": GITHUB_SEARCH_QUERY,
        "sort": "stars",
        "order": "desc",
        "per_page": min(limit, 100),
    }
    headers = {"Accept": "application/vnd.github.v3+json"}

    resp = _http_get(GITHUB_SEARCH_API, params=params, headers=headers)
    items = resp.json().get("items", [])[:limit]

    results = []
    for item in items:
        results.append({
            "source": "github",
            "title": item.get("full_name", ""),
            "url": item.get("html_url", ""),
            "description": item.get("description") or "",
            "stars": item.get("stargazers_count", 0),
            "language": item.get("language") or "",
            "topics": item.get("topics", []),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
        })
    logger.info("GitHub 采集完成，共 %d 条", len(results))
    return results


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
    pub_pattern = re.compile(
        r"<(?:pubDate|published|updated)[^>]*>(.*?)"
        r"</(?:pubDate|published|updated)>",
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

        pub_m = pub_pattern.search(block)
        pub_date = pub_m.group(1).strip() if pub_m else ""

        if title and link:
            entries.append({
                "title": _strip_html(title),
                "url": link,
                "description": _strip_html(desc),
                "pub_date": pub_date,
            })

    return entries


def _strip_cdata(text: str) -> str:
    """移除 CDATA 包裹。"""
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    return text.strip()


def _strip_html(text: str) -> str:
    """移除 HTML 标签。"""
    return re.sub(r"<[^>]+>", "", text).strip()


def collect_rss(limit: int) -> list[dict[str, Any]]:
    """从已配置的 RSS 源采集内容。"""
    logger.info("从 RSS 源采集，limit=%d", limit)

    if not RSS_CONFIG_PATH.exists():
        logger.warning("RSS 配置文件不存在: %s", RSS_CONFIG_PATH)
        return []

    config = yaml.safe_load(RSS_CONFIG_PATH.read_text(encoding="utf-8"))
    sources = [s for s in config.get("sources", []) if s.get("enabled")]
    logger.info("已启用 RSS 源: %d 个", len(sources))

    results: list[dict[str, Any]] = []
    remaining = limit

    for src in sources:
        if remaining <= 0:
            break

        name = src["name"]
        url = src["url"]
        category = src.get("category", "")
        logger.info("  拉取 RSS: %s", name)

        try:
            resp = _http_get(url)
            entries = _parse_rss_xml(resp.text, remaining)
        except (httpx.HTTPError, httpx.TransportError) as exc:
            logger.warning("  RSS 拉取失败 (%s): %s", name, exc)
            continue

        for entry in entries:
            results.append({
                "source": "rss",
                "rss_name": name,
                "category": category,
                "title": entry["title"],
                "url": entry["url"],
                "description": entry["description"],
                "pub_date": entry.get("pub_date", ""),
            })
        remaining -= len(entries)
        logger.info("  获取 %d 条 from %s", len(entries), name)

    logger.info("RSS 采集完成，共 %d 条", len(results))
    return results


def step_collect(
    sources: list[str], limit: int,
) -> list[dict[str, Any]]:
    """Step 1: 采集。"""
    logger.info("=" * 60)
    logger.info("Step 1/4: 采集 (Collect)")
    logger.info("=" * 60)

    all_items: list[dict[str, Any]] = []

    per_source_limit = limit // max(len(sources), 1)
    remainder = limit - per_source_limit * len(sources)

    for i, src in enumerate(sources):
        src_limit = per_source_limit + (1 if i < remainder else 0)
        if src == "github":
            all_items.extend(collect_github(src_limit))
        elif src == "rss":
            all_items.extend(collect_rss(src_limit))
        else:
            logger.warning("未知数据源: %s，跳过", src)

    logger.info("采集总计: %d 条", len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Step 2: 分析 (Analyze)
# ---------------------------------------------------------------------------


def _build_analyze_prompt(item: dict[str, Any]) -> str:
    """构造单条分析 prompt。"""
    parts = [f"项目/文章: {item.get('title', '')}"]
    if item.get("url"):
        parts.append(f"链接: {item['url']}")
    if item.get("description"):
        parts.append(f"描述: {item['description']}")
    if item.get("stars"):
        parts.append(f"Star 数: {item['stars']}")
    if item.get("language"):
        parts.append(f"语言: {item['language']}")
    if item.get("topics"):
        parts.append(f"标签: {', '.join(item['topics'])}")
    if item.get("category"):
        parts.append(f"分类: {item['category']}")
    return "\n".join(parts)


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """从 LLM 回复中提取 JSON。"""
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        return None
    try:
        return json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return None


def _mock_analysis(item: dict[str, Any]) -> dict[str, Any]:
    """dry-run 模式下生成占位分析结果。"""
    desc = item.get("description", "")
    title = item.get("title", "")
    summary = desc[:100] if len(desc) >= 20 else f"{title} — 待 LLM 分析生成正式摘要。"
    topics = item.get("topics", [])
    tags = topics[:5] if topics else ["ai", "pending-review"]
    return {
        "summary": summary,
        "tech_highlights": ["[dry-run] 待分析"],
        "relevance_score": 5,
        "score_reason": "[dry-run] 占位评分",
        "tags": tags,
        "audience": "intermediate",
    }


def step_analyze(
    items: list[dict[str, Any]], *, dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Step 2: 调用 LLM 分析每条内容。"""
    logger.info("=" * 60)
    logger.info("Step 2/4: 分析 (Analyze)%s", " [DRY-RUN]" if dry_run else "")
    logger.info("=" * 60)

    if dry_run:
        for i, item in enumerate(items, 1):
            logger.info("[%d/%d] 模拟分析: %s", i, len(items), item.get("title", ""))
            item["llm_analysis"] = _mock_analysis(item)
        logger.info("模拟分析完成: %d 条", len(items))
        return items

    provider = get_provider()
    logger.info("LLM 提供商: %s, 模型: %s", provider.provider_name, provider.default_model)

    total_cost = 0.0
    analyzed: list[dict[str, Any]] = []

    for i, item in enumerate(items, 1):
        logger.info("[%d/%d] 分析: %s", i, len(items), item.get("title", ""))
        prompt = _build_analyze_prompt(item)
        messages = [
            {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            resp = chat_with_retry(provider, messages, temperature=0.3)
        except Exception as exc:
            logger.error("  LLM 调用失败，跳过: %s", exc)
            continue

        cost = calculate_cost(resp.usage, resp.model)
        total_cost += cost
        logger.info(
            "  tokens=%d, cost=$%.6f",
            resp.usage.total_tokens, cost,
        )

        analysis = _parse_llm_json(resp.content)
        if not analysis:
            logger.warning("  LLM 返回非 JSON，跳过: %s", resp.content[:200])
            continue

        item["llm_analysis"] = analysis
        analyzed.append(item)

    logger.info("分析完成: %d/%d 成功，总成本 $%.6f", len(analyzed), len(items), total_cost)
    return analyzed


# ---------------------------------------------------------------------------
# Step 3: 整理 (Organize)
# ---------------------------------------------------------------------------


def _make_article_id(source: str, index: int) -> str:
    """生成文章 ID: {source}-{YYYYMMDD}-{NNN}。"""
    return f"{source}-{TODAY_COMPACT}-{index:03d}"


def _make_filename(source: str, title: str) -> str:
    """生成文件名: {date}-{source}-{slug}.json。"""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.split("/")[-1].lower()).strip("-")
    slug = slug[:50]
    return f"{TODAY}-{source}-{slug}.json"


def step_organize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 3: 去重 + 格式标准化 + 校验。"""
    logger.info("=" * 60)
    logger.info("Step 3/4: 整理 (Organize)")
    logger.info("=" * 60)

    seen_urls: set[str] = set()

    existing_urls = _load_existing_urls()
    seen_urls.update(existing_urls)
    logger.info("已有文章 URL: %d 条", len(existing_urls))

    articles: list[dict[str, Any]] = []
    skipped_dup = 0

    counters: dict[str, int] = {}

    for item in items:
        url = item.get("url", "")
        if url in seen_urls:
            skipped_dup += 1
            logger.debug("  去重跳过: %s", url)
            continue
        seen_urls.add(url)

        source = item.get("source", "unknown")
        counters[source] = counters.get(source, 0) + 1
        index = counters[source]

        analysis = item.get("llm_analysis", {})

        article = {
            "id": _make_article_id(source, index),
            "title": _normalize_title(item.get("title", "")),
            "source": source,
            "source_url": url,
            "collected_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "summary": analysis.get("summary", item.get("description", "")[:100]),
            "analysis": {
                "tech_highlights": analysis.get("tech_highlights", []),
                "relevance_score": analysis.get("relevance_score", 5),
                "score_reason": analysis.get("score_reason", ""),
                "audience": analysis.get("audience", "intermediate"),
            },
            "tags": analysis.get("tags", []),
            "status": "draft",
        }

        errors = _validate_article(article)
        if errors:
            logger.warning("  校验失败 (%s): %s", article["title"], errors)
            for err in errors:
                logger.warning("    - %s", err)
            continue

        article["_filename"] = _make_filename(source, item.get("title", ""))
        articles.append(article)

    logger.info(
        "整理完成: %d 条有效，%d 条重复跳过",
        len(articles), skipped_dup,
    )
    return articles


def _normalize_title(title: str) -> str:
    """清理标题中的多余空白。"""
    return re.sub(r"\s+", " ", title).strip()


def _load_existing_urls() -> set[str]:
    """扫描已有文章，提取 source_url 用于去重。"""
    urls: set[str] = set()
    if not ARTICLES_DIR.exists():
        return urls
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if url := data.get("source_url"):
                urls.add(url)
        except (json.JSONDecodeError, OSError):
            continue
    return urls


def _validate_article(article: dict[str, Any]) -> list[str]:
    """简要校验文章结构。"""
    errors: list[str] = []
    required = ("id", "title", "source_url", "summary", "tags", "status")
    for field in required:
        if not article.get(field):
            errors.append(f"缺少必填字段: {field}")

    if isinstance(article.get("summary"), str) and len(article["summary"]) < 20:
        errors.append(f"摘要过短: {len(article['summary'])} 字")

    score = article.get("analysis", {}).get("relevance_score")
    if score is not None and not (1 <= score <= 10):
        errors.append(f"relevance_score 超出范围: {score}")

    return errors


# ---------------------------------------------------------------------------
# Step 4: 保存 (Save)
# ---------------------------------------------------------------------------


def step_save(
    articles: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
    dry_run: bool = False,
) -> None:
    """Step 4: 保存原始数据和结构化文章。"""
    logger.info("=" * 60)
    logger.info("Step 4/4: 保存 (Save)%s", " [DRY-RUN]" if dry_run else "")
    logger.info("=" * 60)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"raw_{timestamp}.json"
    if not dry_run:
        raw_path.write_text(
            json.dumps(raw_items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("原始数据已保存: %s (%d 条)", raw_path, len(raw_items))
    else:
        logger.info("[DRY-RUN] 将保存原始数据: %s (%d 条)", raw_path, len(raw_items))

    saved = 0
    for article in articles:
        filename = article.pop("_filename", f"{article['id']}.json")
        path = ARTICLES_DIR / filename

        if path.exists():
            logger.info("  跳过已存在: %s", path.name)
            continue

        if not dry_run:
            path.write_text(
                json.dumps(article, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("  已保存: %s", path.name)
        else:
            logger.info("  [DRY-RUN] 将保存: %s", path.name)
        saved += 1

    logger.info("保存完成: %d 篇文章", saved)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_pipeline(
    sources: list[str],
    limit: int,
    dry_run: bool = False,
) -> None:
    """执行完整流水线。"""
    logger.info("流水线启动 — sources=%s, limit=%d, dry_run=%s", sources, limit, dry_run)
    start = datetime.now(timezone.utc)

    raw_items = step_collect(sources, limit)
    if not raw_items:
        logger.warning("未采集到任何数据，流水线结束")
        return

    analyzed = step_analyze(raw_items, dry_run=dry_run)

    articles = step_organize(analyzed)
    step_save(articles, raw_items, dry_run=dry_run)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=" * 60)
    logger.info(
        "流水线完成 — 采集 %d → 分析 %d → 保存 %d，耗时 %.1fs",
        len(raw_items), len(analyzed), len(articles), elapsed,
    )
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        description="AI 知识库自动化采集流水线",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="github,rss",
        help="数据源，逗号分隔 (默认: github,rss)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="采集条数上限 (默认: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式，不实际写入文件，分析步骤使用占位数据",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志输出",
    )
    return parser


def main() -> None:
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    valid_sources = {"github", "rss"}
    for src in sources:
        if src not in valid_sources:
            logger.error("无效数据源: %s，可选: %s", src, ", ".join(valid_sources))
            sys.exit(1)

    run_pipeline(
        sources=sources,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
