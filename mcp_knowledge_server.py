#!/usr/bin/env python3
"""MCP Server for AI Knowledge Base.

Provides search and retrieval tools for local knowledge articles
via JSON-RPC 2.0 over stdio (MCP protocol).
"""

import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

ARTICLES_DIR = Path(__file__).parent / "knowledge" / "articles"

SERVER_INFO = {
    "name": "ai-knowledge-base",
    "version": "0.1.0",
}

TOOLS = [
    {
        "name": "search_articles",
        "description": "按关键词搜索知识库文章的标题和摘要，返回匹配结果列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量上限，默认 5",
                    "default": 5,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_article",
        "description": "按文章 ID 获取完整的知识条目内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "文章唯一 ID",
                },
            },
            "required": ["article_id"],
        },
    },
    {
        "name": "knowledge_stats",
        "description": "返回知识库统计信息：文章总数、来源分布、热门标签 Top 10。",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def load_articles() -> list[dict[str, Any]]:
    """Load all JSON articles from the articles directory."""
    articles = []
    if not ARTICLES_DIR.exists():
        logger.warning("Articles directory not found: %s", ARTICLES_DIR)
        return articles

    for filepath in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                articles.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load %s: %s", filepath, e)
    return articles


def search_articles(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search articles by keyword in title and summary."""
    articles = load_articles()
    keyword_lower = keyword.lower()
    results = []

    for article in articles:
        title = article.get("title", "").lower()
        summary = article.get("summary", "").lower()
        tags = [t.lower() for t in article.get("tags", [])]

        if keyword_lower in title or keyword_lower in summary or keyword_lower in tags:
            score = 0
            if keyword_lower in title:
                score += 2
            if keyword_lower in summary:
                score += 1
            if keyword_lower in tags:
                score += 1
            results.append((score, article))

    results.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": a.get("id"),
            "title": a.get("title"),
            "summary": a.get("summary"),
            "source": a.get("source"),
            "tags": a.get("tags", []),
        }
        for _, a in results[:limit]
    ]


def get_article(article_id: str) -> dict[str, Any] | None:
    """Get a single article by its ID."""
    articles = load_articles()
    for article in articles:
        if article.get("id") == article_id:
            return article
    return None


def knowledge_stats() -> dict[str, Any]:
    """Return knowledge base statistics."""
    articles = load_articles()
    source_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()

    for article in articles:
        source_counter[article.get("source", "unknown")] += 1
        for tag in article.get("tags", []):
            tag_counter[tag] += 1

    return {
        "total_articles": len(articles),
        "sources": dict(source_counter.most_common()),
        "top_tags": dict(tag_counter.most_common(10)),
    }


def make_response(req_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response."""
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_initialize(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Handle the MCP initialize request."""
    return make_response(req_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
        },
        "serverInfo": SERVER_INFO,
    })


def handle_tools_list(req_id: Any) -> dict[str, Any]:
    """Handle tools/list request."""
    return make_response(req_id, {"tools": TOOLS})


def handle_tools_call(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Handle tools/call request."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if tool_name == "search_articles":
        keyword = arguments.get("keyword", "")
        limit = arguments.get("limit", 5)
        if not keyword:
            return make_response(req_id, {
                "content": [{"type": "text", "text": "错误：keyword 参数不能为空"}],
                "isError": True,
            })
        results = search_articles(keyword, limit)
        return make_response(req_id, {
            "content": [{"type": "text", "text": json.dumps(results, ensure_ascii=False, indent=2)}],
        })

    elif tool_name == "get_article":
        article_id = arguments.get("article_id", "")
        if not article_id:
            return make_response(req_id, {
                "content": [{"type": "text", "text": "错误：article_id 参数不能为空"}],
                "isError": True,
            })
        article = get_article(article_id)
        if article is None:
            return make_response(req_id, {
                "content": [{"type": "text", "text": f"未找到文章：{article_id}"}],
                "isError": True,
            })
        return make_response(req_id, {
            "content": [{"type": "text", "text": json.dumps(article, ensure_ascii=False, indent=2)}],
        })

    elif tool_name == "knowledge_stats":
        stats = knowledge_stats()
        return make_response(req_id, {
            "content": [{"type": "text", "text": json.dumps(stats, ensure_ascii=False, indent=2)}],
        })

    else:
        return make_response(req_id, {
            "content": [{"type": "text", "text": f"未知工具：{tool_name}"}],
            "isError": True,
        })


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    """Route a JSON-RPC request to the appropriate handler."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return handle_initialize(req_id, params)
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return handle_tools_list(req_id)
    elif method == "tools/call":
        return handle_tools_call(req_id, params)
    elif method == "ping":
        return make_response(req_id, {})
    else:
        if req_id is not None:
            return make_error(req_id, -32601, f"Method not found: {method}")
        return None


def main() -> None:
    """Run the MCP server over stdio."""
    logger.info("MCP Knowledge Server started (pid=%d)", os.getpid())
    logger.info("Articles directory: %s", ARTICLES_DIR)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            error_resp = make_error(None, -32700, f"Parse error: {e}")
            sys.stdout.write(json.dumps(error_resp) + "\n")
            sys.stdout.flush()
            continue

        logger.info("Received: %s", request.get("method", "unknown"))

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            logger.info("Responded to: %s (id=%s)", request.get("method"), request.get("id"))

    logger.info("MCP Knowledge Server stopped")


if __name__ == "__main__":
    main()
