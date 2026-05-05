"""推送模块：将格式化后的知识条目并发发布到各渠道。

依赖 aiohttp 进行异步 HTTP 请求；格式化由 distribution.formatter 负责。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from distribution.card_generator import generate_card
from distribution.formatter import generate_daily_digest

logger = logging.getLogger(__name__)

_FEISHU_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ── 数据类 ────────────────────────────────────────────────────────────────────


@dataclass
class PublishResult:
    """单次发布操作的结果记录。

    Attributes:
        channel: 渠道名称，如 "feishu"。
        success: 是否发布成功。
        message_id: 平台返回的消息 ID（不支持时为 None）。
        error: 失败时的错误描述（成功时为 None）。
    """

    channel: str
    success: bool
    message_id: str | None = field(default=None)
    error: str | None = field(default=None)


# ── 抽象基类 ──────────────────────────────────────────────────────────────────


class BasePublisher(ABC):
    """发布者抽象基类，定义渠道无关的发送接口。"""

    @abstractmethod
    async def send_message(self, content: Any) -> PublishResult:
        """发送单条消息。

        Args:
            content: 渠道原生消息体（格式由子类决定）。

        Returns:
            本次发送的 PublishResult。
        """

    @abstractmethod
    async def send_digest(self, digest: dict[str, Any]) -> list[PublishResult]:
        """从每日简报 dict 中提取本渠道内容并发送。

        Args:
            digest: generate_daily_digest() 返回的 dict。

        Returns:
            每条消息对应一个 PublishResult 的列表。
        """


# ── 飞书实现 ──────────────────────────────────────────────────────────────────


class FeishuPublisher(BasePublisher):
    """通过飞书自定义机器人 Webhook 发送 interactive 卡片消息。

    Webhook URL 优先从构造参数读取，其次从环境变量 FEISHU_WEBHOOK_URL 读取。
    若配置了签名密钥（FEISHU_WEBHOOK_SECRET），每条消息自动附加 timestamp 和
    sign 字段，对应飞书机器人「签名校验」安全模式。

    Args:
        webhook_url: 飞书 Webhook 地址；为 None 时读取 FEISHU_WEBHOOK_URL。
        secret: 签名密钥；为 None 时读取 FEISHU_WEBHOOK_SECRET，仍为空则不签名。

    Raises:
        ValueError: 若 Webhook URL 为空。
    """

    _CHANNEL = "feishu"

    def __init__(
        self,
        webhook_url: str | None = None,
        secret: str | None = None,
    ) -> None:
        url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL", "")
        if not url:
            raise ValueError(
                "FEISHU_WEBHOOK_URL 未设置，请传入 webhook_url 参数或设置环境变量"
            )
        self._webhook_url = url
        self._secret: str | None = secret or os.environ.get("FEISHU_WEBHOOK_SECRET") or None

    def _sign(self, timestamp: str) -> str:
        """计算飞书签名校验所需的 sign 值。

        算法：base64( HMAC-SHA256( key="{timestamp}\\n{secret}" ) )

        Args:
            timestamp: Unix 秒级时间戳字符串。

        Returns:
            Base64 编码的签名字符串。
        """
        key = f"{timestamp}\n{self._secret}".encode("utf-8")
        mac = hmac.new(key, digestmod=hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _signed_payload(self, content: dict[str, Any]) -> dict[str, Any]:
        """若已配置密钥，返回注入了 timestamp/sign 的新 payload；否则原样返回。

        Args:
            content: 原始飞书卡片 dict。

        Returns:
            注入签名字段后的 dict（不修改原始对象）。
        """
        if not self._secret:
            return content
        ts = str(int(time.time()))
        return {**content, "timestamp": ts, "sign": self._sign(ts)}

    async def _post(
        self, session: aiohttp.ClientSession, content: dict[str, Any]
    ) -> PublishResult:
        """使用已有 session 向 Webhook 发送单条消息。

        Args:
            session: 共享的 aiohttp.ClientSession。
            content: 飞书 interactive 卡片 dict。

        Returns:
            PublishResult，携带平台返回的错误信息或成功标记。
        """
        payload = self._signed_payload(content)
        try:
            async with session.post(self._webhook_url, json=payload) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("飞书 HTTP 请求失败: %s", exc)
            return PublishResult(channel=self._CHANNEL, success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("飞书发送异常")
            return PublishResult(channel=self._CHANNEL, success=False, error=str(exc))

        if data.get("code", -1) != 0:
            err = data.get("msg", "未知错误")
            logger.warning("飞书返回业务错误: %s", err)
            return PublishResult(channel=self._CHANNEL, success=False, error=err)

        return PublishResult(channel=self._CHANNEL, success=True)

    async def send_message(self, content: dict[str, Any]) -> PublishResult:
        """发送单张飞书 interactive 卡片。

        Args:
            content: 飞书 interactive 卡片 dict（msg_type=interactive）。

        Returns:
            本次发送的 PublishResult。
        """
        async with aiohttp.ClientSession(timeout=_FEISHU_TIMEOUT) as session:
            return await self._post(session, content)

    async def send_digest(self, digest: dict[str, Any]) -> list[PublishResult]:
        """发送日报汇总卡片（单条消息）。

        Args:
            digest: generate_daily_digest() 返回的 dict，需含 "feishu" 键。

        Returns:
            含单个 PublishResult 的列表；feishu 键缺失时返回空列表。
        """
        card: dict[str, Any] | None = digest.get("feishu")
        if not card:
            return []
        return [await self.send_message(card)]


# ── 小红书草稿实现 ────────────────────────────────────────────────────────────


class XiaohongshuPublisher(BasePublisher):
    """将评分最高的文章改写为小红书笔记草稿，并生成图文卡片，保存至本地。

    文字草稿和图片均写入 {drafts_dir}/{date}/ 目录，不发任何网络请求（LLM 除外）。
    LLM 接入使用与采集流水线相同的环境变量：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。

    Args:
        drafts_dir: 草稿根目录，默认 "drafts"。
    """

    _CHANNEL = "xiaohongshu"

    _SYSTEM_PROMPT = (
        "你是一位小红书内容创作者，擅长将 AI 技术资讯改写为吸引普通用户的口语化内容。"
    )
    _USER_PROMPT_TMPL = """\
请将以下 AI 技术资讯改写为一篇小红书笔记。

要求：
1. 标题：20 字以内，带 emoji，口语化，吸引眼球
2. 正文：300-500 字，口语化，emoji 穿插，分点说明对普通人/开发者的价值
3. 话题标签：5 个，格式 #标签名
4. 严格按如下格式输出，不要输出其他内容：

【标题】
xxx

【正文】
xxx

【标签】
#xxx #xxx #xxx #xxx #xxx

---
文章标题：{title}
摘要：{summary}
技术亮点：
{highlights}"""

    def __init__(self, drafts_dir: str = "drafts") -> None:
        self._drafts_dir = Path(drafts_dir)

    def _llm_rewrite(self, article: dict[str, Any]) -> str:
        """调用 LLM 将文章改写为小红书风格文本。

        Args:
            article: 知识条目 dict。

        Returns:
            改写后的小红书笔记文本。
        """
        from workflows.model_client import chat

        highlights = article.get("analysis", {}).get("tech_highlights", [])
        prompt = self._USER_PROMPT_TMPL.format(
            title=article.get("title", ""),
            summary=article.get("summary", ""),
            highlights="\n".join(f"- {h}" for h in highlights),
        )
        text, _ = chat(
            prompt,
            system=self._SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=1000,
            node_name="xiaohongshu",
        )
        return text

    async def send_message(self, content: Any) -> PublishResult:
        """未使用；小红书草稿通过 send_digest() 生成。"""
        return PublishResult(
            channel=self._CHANNEL, success=False, error="请使用 send_digest()"
        )

    async def send_digest(self, digest: dict[str, Any]) -> list[PublishResult]:
        """取评分最高的文章，生成小红书文字草稿和图片卡片，写入本地。

        Args:
            digest: generate_daily_digest() 返回的 dict，需含 "articles" 和 "date" 键。

        Returns:
            含单个 PublishResult 的列表。
        """
        articles: list[dict[str, Any]] = digest.get("articles", [])
        if not articles:
            return [PublishResult(channel=self._CHANNEL, success=False,
                                  error="digest 中无文章")]

        article = articles[0]  # 已按 relevance_score 降序，取 Top 1
        date_str: str = digest.get("date", "unknown")
        out_dir = self._drafts_dir / date_str

        try:
            # LLM 改写（同步调用放入线程池）
            text = await asyncio.to_thread(self._llm_rewrite, article)

            # 保存文字草稿
            txt_path = out_dir / "xiaohongshu.txt"
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            txt_path.write_text(text, encoding="utf-8")
            logger.info("小红书文字草稿: %s", txt_path)

            # 生成图片卡片（CPU 密集，放入线程池）
            img_path = out_dir / "xiaohongshu.png"
            await asyncio.to_thread(generate_card, article, img_path)

        except Exception as exc:
            logger.exception("小红书草稿生成失败")
            return [PublishResult(channel=self._CHANNEL, success=False,
                                  error=str(exc))]

        return [PublishResult(channel=self._CHANNEL, success=True)]


# ── 统一异步入口 ──────────────────────────────────────────────────────────────


def _build_publishers() -> list[BasePublisher]:
    """根据环境变量自动构建可用的 publisher 列表。

    Returns:
        已配置的 BasePublisher 实例列表；若无任何渠道配置则返回空列表。
    """
    publishers: list[BasePublisher] = []
    if os.environ.get("FEISHU_WEBHOOK_URL"):
        publishers.append(FeishuPublisher())  # secret 由 __init__ 从环境变量自动读取
    if os.environ.get("LLM_API_KEY"):
        publishers.append(XiaohongshuPublisher())
    return publishers


async def publish_daily_digest(
    knowledge_dir: str = "knowledge/articles",
    date: str | None = None,
    top_n: int = 5,
) -> list[PublishResult]:
    """生成每日简报并并发发布到所有已配置渠道。

    渠道由环境变量自动检测：设置了 FEISHU_WEBHOOK_URL 即启用飞书推送。
    各渠道的 send_digest() 通过 asyncio.gather() 并发执行。

    Args:
        knowledge_dir: 知识条目目录路径，传递给 generate_daily_digest()。
        date: 目标日期（YYYY-MM-DD）；None 时默认今天。
        top_n: 每日简报最多展示条目数。

    Returns:
        所有渠道所有消息的 PublishResult 列表（扁平化）；
        当日无文章或无可用渠道时返回空列表。
    """
    publishers = _build_publishers()
    if not publishers:
        logger.warning("未配置任何发布渠道，跳过推送")
        return []

    digest = generate_daily_digest(
        knowledge_dir=knowledge_dir, date=date, top_n=top_n
    )

    if isinstance(digest, str):
        logger.info(digest)
        return []

    tasks = [publisher.send_digest(digest) for publisher in publishers]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[PublishResult] = []
    for publisher, outcome in zip(publishers, outcomes):
        if isinstance(outcome, Exception):
            logger.error(
                "%s 发布时发生未捕获异常: %s",
                type(publisher).__name__,
                outcome,
            )
            results.append(
                PublishResult(
                    channel=type(publisher).__name__,
                    success=False,
                    error=str(outcome),
                )
            )
        else:
            results.extend(outcome)

    return results
