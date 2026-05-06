"""知识库交互机器人模块。

提供知识搜索、用户订阅管理、权限控制及统一消息处理入口。
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_ARTICLES_DIR = _REPO_ROOT / "knowledge" / "articles"
_STATE_DIR = _REPO_ROOT / "state"
_SUBSCRIPTIONS_FILE = _STATE_DIR / "subscriptions.json"
_PERMISSIONS_FILE = _STATE_DIR / "permissions.json"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Intent(Enum):
    """用户意图类型。值为字符串，可直接用于展示。"""

    SEARCH = "SEARCH"
    BROWSE_TODAY = "BROWSE_TODAY"
    BROWSE_TOP = "BROWSE_TOP"
    SUBSCRIBE = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


class Permission(Enum):
    """权限级别（数值越大权限越高）。"""

    READ = 1
    WRITE = 2
    DELETE = 3


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class Article:
    """知识条目数据类。"""

    id: str
    title: str
    source_url: str
    summary: str
    tags: list[str]
    status: str
    collected_at: str
    relevance_score: int = 5

    @classmethod
    def from_dict(cls, data: dict) -> "Article":
        """从字典构造 Article。

        Args:
            data: 知识条目原始字典。

        Returns:
            Article 实例。
        """
        analysis = data.get("analysis", {})
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            source_url=data.get("source_url", ""),
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            status=data.get("status", "draft"),
            collected_at=data.get("collected_at", ""),
            relevance_score=analysis.get("relevance_score", 5),
        )

    def collected_date(self) -> Optional[date]:
        """解析 collected_at 并返回 date 对象。"""
        if not self.collected_at:
            return None
        try:
            return datetime.fromisoformat(
                self.collected_at.replace("Z", "+00:00")
            ).date()
        except ValueError:
            return None


@dataclass
class Subscription:
    """用户订阅记录。"""

    user_id: str
    tags: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# 意图识别
# ---------------------------------------------------------------------------

# 命令前缀正则
_CMD_SEARCH = re.compile(r"^/search\s*(.*)", re.IGNORECASE)
_CMD_TODAY = re.compile(r"^/today\b", re.IGNORECASE)
_CMD_TOP = re.compile(r"^/top(?:\s+(\d+))?", re.IGNORECASE)
_CMD_SUBSCRIBE = re.compile(r"^/subscribe\s*(.*)", re.IGNORECASE)
_CMD_UNSUBSCRIBE = re.compile(r"^/unsubscribe\b", re.IGNORECASE)
_CMD_HELP = re.compile(r"^/help\b", re.IGNORECASE)

# 自然语言关键词（按意图分组）
_NL_SEARCH = re.compile(r"搜索|查询|查找|找一找|find|search", re.IGNORECASE)
_NL_TODAY = re.compile(r"今天|今日|daily|最新动态", re.IGNORECASE)
_NL_TOP = re.compile(r"热门|排行|top|最热|精选", re.IGNORECASE)
_NL_SUBSCRIBE = re.compile(r"^(?!.*退订|取消).*订阅", re.IGNORECASE)
_NL_UNSUBSCRIBE = re.compile(r"退订|取消订阅|unsubscribe", re.IGNORECASE)
_NL_HELP = re.compile(r"帮助|help|怎么用|使用说明|命令列表", re.IGNORECASE)


def recognize_intent(text: str) -> tuple[Intent, str]:
    """识别用户输入的意图（规则匹配，不调用 LLM）。

    优先匹配命令前缀（/search, /today, /top, /subscribe, /help），
    再匹配自然语言关键词。

    Args:
        text: 用户原始输入文本。

    Returns:
        (Intent 枚举, 参数字符串) 二元组。参数字符串含义取决于意图：
        - SEARCH: 搜索关键词
        - TOP: 返回条数（字符串数字，默认 "5"）
        - SUBSCRIBE: 标签列表（逗号分隔字符串）
        - 其他: 空字符串
    """
    text = text.strip()

    # --- 命令前缀优先 ---
    m = _CMD_SEARCH.match(text)
    if m:
        return Intent.SEARCH, m.group(1).strip()

    if _CMD_TODAY.match(text):
        return Intent.BROWSE_TODAY, ""

    m = _CMD_TOP.match(text)
    if m:
        n = m.group(1) or "5"
        return Intent.BROWSE_TOP, n

    if _CMD_UNSUBSCRIBE.match(text):
        return Intent.UNSUBSCRIBE, ""

    m = _CMD_SUBSCRIBE.match(text)
    if m:
        return Intent.SUBSCRIBE, m.group(1).strip()

    if _CMD_HELP.match(text):
        return Intent.HELP, ""

    # --- 自然语言匹配 ---
    if _NL_UNSUBSCRIBE.search(text):
        return Intent.UNSUBSCRIBE, ""

    if _NL_TODAY.search(text):
        return Intent.BROWSE_TODAY, ""

    if _NL_TOP.search(text):
        return Intent.BROWSE_TOP, "5"

    if _NL_SUBSCRIBE.search(text):
        return Intent.SUBSCRIBE, ""

    if _NL_HELP.search(text):
        return Intent.HELP, ""

    if _NL_SEARCH.search(text):
        # 去掉触发词，剩余部分视作查询词
        query = re.sub(_NL_SEARCH, "", text).strip()
        return Intent.SEARCH, query

    return Intent.UNKNOWN, text


# ---------------------------------------------------------------------------
# KnowledgeSearchEngine
# ---------------------------------------------------------------------------


class KnowledgeSearchEngine:
    """知识条目搜索引擎。

    从 `knowledge/articles/` 目录读取 JSON 文件，支持关键词、
    标签、日期范围过滤。
    """

    def __init__(self, articles_dir: "Path | str" = _ARTICLES_DIR) -> None:
        """初始化搜索引擎。

        Args:
            articles_dir: 知识条目 JSON 文件所在目录，接受 Path 或字符串路径。
        """
        self._articles_dir = Path(articles_dir)
        self._cache: Optional[list[Article]] = None

    def _load(self, force: bool = False) -> list[Article]:
        """加载并缓存所有知识条目。

        Args:
            force: 是否强制刷新缓存。

        Returns:
            Article 列表。
        """
        if self._cache is not None and not force:
            return self._cache

        articles: list[Article] = []
        for path in sorted(self._articles_dir.glob("*.json")):
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                articles.append(Article.from_dict(data))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("跳过无效文件 %s: %s", path.name, exc)

        self._cache = articles
        logger.debug("已加载 %d 条知识条目", len(articles))
        return articles

    def search(
        self,
        query: str = "",
        keyword: str = "",
        tags: Optional[list[str]] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        limit: int = 10,
    ) -> list[Article]:
        """搜索知识条目。

        Args:
            query: 关键词，匹配标题和摘要（大小写不敏感）。空字符串表示不过滤。
            keyword: query 的别名，两者同时传入时 query 优先。
            tags: 标签过滤列表，条目必须包含其中至少一个标签。
            date_from: 采集日期下限（含）。
            date_to: 采集日期上限（含）。
            limit: 最多返回条数。

        Returns:
            按 relevance_score 降序排列的 Article 列表。
        """
        articles = self._load()
        kw = (query or keyword).lower()

        results = []
        for art in articles:
            if kw and kw not in art.title.lower() and kw not in art.summary.lower():
                continue
            if tags and not any(t in art.tags for t in tags):
                continue
            art_date = art.collected_date()
            if date_from and (art_date is None or art_date < date_from):
                continue
            if date_to and (art_date is None or art_date > date_to):
                continue
            results.append(art)

        results.sort(key=lambda a: a.relevance_score, reverse=True)
        return results[:limit]

    def today(self, limit: int = 10) -> list[Article]:
        """返回今日采集的知识条目。

        Args:
            limit: 最多返回条数。

        Returns:
            今日采集的 Article 列表。
        """
        today_date = date.today()
        return self.search(date_from=today_date, date_to=today_date, limit=limit)

    def top(self, n: int = 5) -> list[Article]:
        """返回 relevance_score 最高的 n 条知识条目。

        Args:
            n: 返回条数。

        Returns:
            按 relevance_score 降序排列的 Article 列表。
        """
        return self.search(limit=n)

    def invalidate_cache(self) -> None:
        """清除内部缓存，下次搜索时重新读取文件。"""
        self._cache = None


# ---------------------------------------------------------------------------
# SubscriptionManager
# ---------------------------------------------------------------------------


class SubscriptionManager:
    """用户订阅管理器。

    订阅数据持久化到 `state/subscriptions.json`。
    """

    def __init__(self, storage_path: Path = _SUBSCRIPTIONS_FILE) -> None:
        """初始化订阅管理器。

        Args:
            storage_path: 订阅数据持久化路径。
        """
        self._path = storage_path
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        """从文件加载订阅数据。"""
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("无法加载订阅数据: %s", exc)
            return {}

    def _save(self) -> None:
        """将订阅数据持久化到文件。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def subscribe(self, user_id: str, tags: Optional[list[str]] = None) -> Subscription:
        """添加或更新用户订阅。

        Args:
            user_id: 用户 ID。
            tags: 关注的标签列表，为空表示订阅全部。

        Returns:
            更新后的 Subscription 对象。
        """
        existing = self._data.get(user_id, {})
        sub = Subscription(
            user_id=user_id,
            tags=tags or [],
            created_at=existing.get("created_at", datetime.now(timezone.utc).isoformat()),
        )
        self._data[user_id] = {
            "user_id": sub.user_id,
            "tags": sub.tags,
            "created_at": sub.created_at,
        }
        self._save()
        logger.info("用户 %s 订阅成功，标签: %s", user_id, sub.tags)
        return sub

    def unsubscribe(self, user_id: str) -> bool:
        """取消用户订阅。

        Args:
            user_id: 用户 ID。

        Returns:
            True 表示成功取消，False 表示该用户原本未订阅。
        """
        if user_id not in self._data:
            return False
        del self._data[user_id]
        self._save()
        logger.info("用户 %s 已取消订阅", user_id)
        return True

    def get_subscription(self, user_id: str) -> Optional[Subscription]:
        """查询用户订阅信息。

        Args:
            user_id: 用户 ID。

        Returns:
            Subscription 对象，未订阅则返回 None。
        """
        record = self._data.get(user_id)
        if record is None:
            return None
        return Subscription(
            user_id=record["user_id"],
            tags=record.get("tags", []),
            created_at=record.get("created_at", ""),
        )

    def list_subscribers(self) -> list[Subscription]:
        """返回所有订阅用户列表。

        Returns:
            Subscription 列表。
        """
        return [
            Subscription(
                user_id=r["user_id"],
                tags=r.get("tags", []),
                created_at=r.get("created_at", ""),
            )
            for r in self._data.values()
        ]


# ---------------------------------------------------------------------------
# PermissionManager
# ---------------------------------------------------------------------------


class PermissionManager:
    """三级权限控制管理器（READ < WRITE < DELETE）。

    默认所有用户拥有 READ 权限。WRITE/DELETE 需显式授权。
    权限数据持久化到 `state/permissions.json`。
    """

    def __init__(self, storage_path: Path = _PERMISSIONS_FILE) -> None:
        """初始化权限管理器。

        Args:
            storage_path: 权限数据持久化路径。
        """
        self._path = storage_path
        self._data: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        """从文件加载权限数据（存储为权限级别整数）。"""
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("无法加载权限数据: %s", exc)
            return {}

    def _save(self) -> None:
        """将权限数据持久化到文件。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _level(self, user_id: str) -> int:
        """返回用户当前权限级别数值（默认 READ=1）。"""
        return self._data.get(user_id, Permission.READ.value)

    def has_permission(self, user_id: str, permission: Permission) -> bool:
        """检查用户是否拥有指定权限（包含低级权限的隐含授权）。

        Args:
            user_id: 用户 ID。
            permission: 需要检查的权限级别。

        Returns:
            True 表示有权限，False 表示无权限。
        """
        return self._level(user_id) >= permission.value

    def grant_permission(self, user_id: str, permission: Permission) -> None:
        """授予用户指定权限（低于当前权限时不降级）。

        Args:
            user_id: 用户 ID。
            permission: 要授予的权限级别。
        """
        current = self._level(user_id)
        if permission.value > current:
            self._data[user_id] = permission.value
            self._save()
            logger.info("已授予用户 %s %s 权限", user_id, permission.name)

    def revoke_permission(self, user_id: str, permission: Permission) -> None:
        """将用户权限降至指定级别以下（最低降至 READ）。

        Args:
            user_id: 用户 ID。
            permission: 要撤销的权限级别（降至该级别 -1，最低 READ）。
        """
        new_level = max(Permission.READ.value, permission.value - 1)
        self._data[user_id] = new_level
        self._save()
        logger.info(
            "已撤销用户 %s %s 权限，当前级别: %d", user_id, permission.name, new_level
        )

    def get_permission(self, user_id: str) -> Permission:
        """返回用户当前最高权限。

        Args:
            user_id: 用户 ID。

        Returns:
            Permission 枚举值。
        """
        level = self._level(user_id)
        for perm in sorted(Permission, key=lambda p: p.value, reverse=True):
            if level >= perm.value:
                return perm
        return Permission.READ


# ---------------------------------------------------------------------------
# KnowledgeBot
# ---------------------------------------------------------------------------

_HELP_TEXT = """📚 知识库机器人指令列表

/search <关键词>   — 搜索知识条目
/today             — 查看今日采集内容
/top [n]           — 查看最高分条目（默认 5 条）
/subscribe [标签]  — 订阅更新（可指定逗号分隔标签）
/unsubscribe       — 取消订阅
/help              — 显示本帮助

支持自然语言输入，例如：
  "搜索 LangGraph"、"今日简报"、"热门项目"、"订阅 agent"
"""


def _format_article(art: Article, index: int) -> str:
    """将 Article 格式化为可读文本。

    Args:
        art: 知识条目。
        index: 序号（从 1 开始）。

    Returns:
        格式化后的字符串。
    """
    tags_str = " ".join(f"#{t}" for t in art.tags[:5])
    return (
        f"{index}. [{art.relevance_score}分] {art.title}\n"
        f"   {art.summary}\n"
        f"   {tags_str}\n"
        f"   {art.source_url}"
    )


def format_search_results(results: list[Article], query: str = "") -> str:
    """将搜索结果列表格式化为可读字符串（公开接口）。

    Args:
        results: Article 列表，通常来自 KnowledgeSearchEngine.search()。
        query: 原始查询词，用于生成标题行；为空时标题行省略查询词。

    Returns:
        格式化后的多行字符串；无结果时返回提示语。
    """
    if not results:
        hint = f"「{query}」" if query else "该条件"
        return f"未找到与 {hint} 相关的知识条目。"

    header = f"搜索「{query}」，共找到 {len(results)} 条结果：" if query else f"共找到 {len(results)} 条结果："
    lines = [header, ""]
    lines.extend(_format_article(a, i + 1) for i, a in enumerate(results))
    return "\n\n".join(lines)


class KnowledgeBot:
    """知识库交互机器人主入口。

    整合搜索、订阅、权限三大模块，提供统一消息处理接口。

    Args:
        search_engine: 可选的自定义搜索引擎实例。
        subscription_manager: 可选的自定义订阅管理器实例。
        permission_manager: 可选的自定义权限管理器实例。

    Example:
        bot = KnowledgeBot()
        response = bot.handle_message("user_001", "/search LangGraph")
        print(response)
    """

    def __init__(
        self,
        search_engine: Optional[KnowledgeSearchEngine] = None,
        subscription_manager: Optional[SubscriptionManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> None:
        self._search = search_engine or KnowledgeSearchEngine()
        self._subscriptions = subscription_manager or SubscriptionManager()
        self._permissions = permission_manager or PermissionManager()

    def handle_message(self, user_id: str, text: str) -> str:
        """统一消息处理入口。

        根据 recognize_intent 结果分发到对应处理器，并执行权限检查。

        Args:
            user_id: 发送消息的用户 ID。
            text: 用户输入的原始文本。

        Returns:
            机器人回复的文本字符串。
        """
        if not text or not text.strip():
            return "请输入指令或关键词，输入 /help 查看使用说明。"

        intent, args = recognize_intent(text)
        logger.debug("用户 %s | 意图: %s | 参数: %r", user_id, intent.name, args)

        handlers = {
            Intent.SEARCH: self._handle_search,
            Intent.BROWSE_TODAY: self._handle_today,
            Intent.BROWSE_TOP: self._handle_top,
            Intent.SUBSCRIBE: self._handle_subscribe,
            Intent.UNSUBSCRIBE: self._handle_unsubscribe,
            Intent.HELP: self._handle_help,
            Intent.UNKNOWN: self._handle_unknown,
        }

        handler = handlers[intent]
        return handler(user_id, args)

    # ------------------------------------------------------------------
    # 内部处理器
    # ------------------------------------------------------------------

    def _handle_search(self, user_id: str, query: str) -> str:
        """处理搜索请求（需要 READ 权限）。"""
        if not self._permissions.has_permission(user_id, Permission.READ):
            return "权限不足，无法执行搜索操作。"

        if not query:
            return "请提供搜索关键词，例如：/search LangGraph"

        results = self._search.search(query=query, limit=5)
        if not results:
            return f"未找到与「{query}」相关的知识条目。"

        lines = [f"搜索「{query}」，共找到 {len(results)} 条结果：\n"]
        lines.extend(_format_article(a, i + 1) for i, a in enumerate(results))
        return "\n\n".join(lines)

    def _handle_today(self, user_id: str, _args: str) -> str:
        """处理今日简报请求（需要 READ 权限）。"""
        if not self._permissions.has_permission(user_id, Permission.READ):
            return "权限不足，无法查看今日内容。"

        results = self._search.today(limit=10)
        if not results:
            return f"今日（{date.today()}）暂无新增知识条目。"

        lines = [f"今日（{date.today()}）共采集 {len(results)} 条内容：\n"]
        lines.extend(_format_article(a, i + 1) for i, a in enumerate(results))
        return "\n\n".join(lines)

    def _handle_top(self, user_id: str, args: str) -> str:
        """处理热门排行请求（需要 READ 权限）。"""
        if not self._permissions.has_permission(user_id, Permission.READ):
            return "权限不足，无法查看热门条目。"

        try:
            n = int(args) if args.isdigit() else 5
            n = max(1, min(n, 20))
        except ValueError:
            n = 5

        results = self._search.top(n=n)
        if not results:
            return "暂无知识条目。"

        lines = [f"热门 Top {n} 知识条目：\n"]
        lines.extend(_format_article(a, i + 1) for i, a in enumerate(results))
        return "\n\n".join(lines)

    def _handle_subscribe(self, user_id: str, args: str) -> str:
        """处理订阅请求（需要 WRITE 权限）。"""
        if not self._permissions.has_permission(user_id, Permission.WRITE):
            return "权限不足，订阅功能需要 WRITE 权限，请联系管理员授权。"

        tags = [t.strip() for t in args.split(",") if t.strip()] if args else []
        self._subscriptions.subscribe(user_id, tags)

        tag_info = f"标签：{', '.join(tags)}" if tags else "全部内容"
        return f"订阅成功！您将收到 {tag_info} 的更新推送。"

    def _handle_unsubscribe(self, user_id: str, _args: str) -> str:
        """处理取消订阅请求（需要 WRITE 权限）。"""
        if not self._permissions.has_permission(user_id, Permission.WRITE):
            return "权限不足，取消订阅需要 WRITE 权限。"

        success = self._subscriptions.unsubscribe(user_id)
        if success:
            return "已成功取消订阅，您将不再收到推送。"
        return "您当前未订阅任何内容。"

    def _handle_help(self, user_id: str, _args: str) -> str:
        """返回帮助文本。"""
        perm = self._permissions.get_permission(user_id)
        return _HELP_TEXT + f"\n当前权限级别：{perm.name}"

    def _handle_unknown(self, user_id: str, text: str) -> str:
        """处理无法识别的输入。"""
        return f"未能理解「{text}」，请输入 /help 查看支持的指令。"
