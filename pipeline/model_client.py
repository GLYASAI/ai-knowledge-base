"""统一 LLM 调用客户端，支持 DeepSeek / Qwen / OpenAI 三种提供商。

通过环境变量切换：
    LLM_PROVIDER  — deepseek (默认) | qwen | openai
    DEEPSEEK_API_KEY / QWEN_API_KEY / OPENAI_API_KEY — 对应密钥

所有提供商均走 OpenAI 兼容 API，使用 httpx 直接调用。
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Usage:
    """Token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """统一的 LLM 返回结构。"""

    content: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""


# ---------------------------------------------------------------------------
# 提供商配置
# ---------------------------------------------------------------------------

PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "api_key_env": "QWEN_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
    },
}

# 每 1K token 的 USD 价格 (prompt / completion)
PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.0014, 0.0028),
    "deepseek-reasoner": (0.0055, 0.0219),
    "qwen-plus": (0.0008, 0.002),
    "qwen-turbo": (0.0003, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
}

# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """LLM 提供商接口。"""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """发送对话请求并返回结果。"""


# ---------------------------------------------------------------------------
# OpenAI 兼容实现
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(LLMProvider):
    """通过 OpenAI 兼容 API 调用 LLM。"""

    def __init__(self, provider_name: str, api_key: str | None = None) -> None:
        if provider_name not in PROVIDER_CONFIG:
            raise ValueError(
                f"不支持的提供商: {provider_name}，"
                f"可选: {', '.join(PROVIDER_CONFIG)}"
            )
        cfg = PROVIDER_CONFIG[provider_name]
        self.provider_name = provider_name
        self.base_url: str = cfg["base_url"]
        self.default_model: str = cfg["default_model"]
        self.api_key = api_key or os.environ.get(cfg["api_key_env"], "")
        if not self.api_key:
            raise ValueError(
                f"未设置 API Key，请通过参数或环境变量 "
                f"{cfg['api_key_env']} 提供"
            )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """调用 chat/completions 端点。"""
        model = model or self.default_model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = httpx.post(url, json=payload, headers=headers, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()

        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        content = data["choices"][0]["message"]["content"]

        return LLMResponse(
            content=content,
            usage=usage,
            model=model,
            provider=self.provider_name,
        )


# ---------------------------------------------------------------------------
# 重试包装
# ---------------------------------------------------------------------------


def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    max_retries: int = 3,
) -> LLMResponse:
    """带指数退避重试的 chat 调用。"""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return provider.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "LLM 调用失败 (第 %d/%d 次)，%ds 后重试: %s",
                    attempt,
                    max_retries,
                    wait,
                    exc,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "LLM 调用失败，已耗尽 %d 次重试: %s",
                    max_retries,
                    exc,
                )
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Token 消耗估算 & 成本计算
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数（按 1 token ≈ 1.3 个中文字符 / 4 个英文字符）。"""
    cn_count = sum(1 for ch in text if "一" <= ch <= "鿿")
    en_count = len(text) - cn_count
    return int(cn_count / 1.3 + en_count / 4)


def calculate_cost(usage: Usage, model: str) -> float:
    """根据用量和模型计算成本（USD）。"""
    prompt_price, completion_price = PRICING.get(model, (0.0, 0.0))
    return (
        usage.prompt_tokens * prompt_price / 1000
        + usage.completion_tokens * completion_price / 1000
    )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def get_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
) -> OpenAICompatibleProvider:
    """根据环境变量或参数创建提供商实例。"""
    name = provider_name or os.environ.get("LLM_PROVIDER") or "deepseek"
    return OpenAICompatibleProvider(name, api_key=api_key)


def quick_chat(
    prompt: str,
    *,
    system: str = "你是一个有帮助的 AI 助手。",
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """一句话调用 LLM，返回文本内容。"""
    provider = get_provider(provider_name)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    resp = chat_with_retry(
        provider,
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    logger.info(
        "quick_chat 完成 — model=%s, tokens=%d, cost=$%.6f",
        resp.model,
        resp.usage.total_tokens,
        calculate_cost(resp.usage, resp.model),
    )
    return resp.content


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    provider = get_provider()
    logger.info("使用提供商: %s, 模型: %s", provider.provider_name, provider.default_model)

    test_messages = [
        {"role": "system", "content": "你是一个有帮助的 AI 助手。"},
        {"role": "user", "content": "用一句话介绍 LangGraph 是什么。"},
    ]

    logger.info("--- chat_with_retry 测试 ---")
    response = chat_with_retry(provider, test_messages)
    logger.info("回复: %s", response.content)
    logger.info("用量: %s", response.usage)
    logger.info("估算成本: $%.6f", calculate_cost(response.usage, response.model))

    logger.info("--- quick_chat 测试 ---")
    answer = quick_chat("用一句话解释什么是 AI Agent。")
    logger.info("回复: %s", answer)

    sample = "LangGraph 是一个用于构建多 Agent 工作流的框架。"
    logger.info("--- Token 估算测试 ---")
    logger.info("文本: %s", sample)
    logger.info("估算 token 数: %d", estimate_tokens(sample))
