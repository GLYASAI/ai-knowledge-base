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
from typing import Any

import aiohttp

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


# ── 统一异步入口 ──────────────────────────────────────────────────────────────


def _build_publishers() -> list[BasePublisher]:
    """根据环境变量自动构建可用的 publisher 列表。

    Returns:
        已配置的 BasePublisher 实例列表；若无任何渠道配置则返回空列表。
    """
    publishers: list[BasePublisher] = []
    if os.environ.get("FEISHU_WEBHOOK_URL"):
        publishers.append(FeishuPublisher())  # secret 由 __init__ 从环境变量自动读取
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
