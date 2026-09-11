"""LLM provider 抽象（D07）。

**provider 全部预留，切换只改 `.env`，不改业务代码。**
开发期默认本机 Ollama（零 API 成本，公告全文不出本机）。

已实现的 provider::

    ollama     本机 Ollama /api/chat（默认，开发期使用）
    openai     任意 OpenAI 兼容端点（DeepSeek / OpenAI / Kimi / GLM 均可）
    scripted   脚本化假 provider —— 仅用于测试与离线演示（确定性输出）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from app.models.enums import NodeRunStatus


@dataclass(frozen=True)
class LlmRequest:
    prompt: str
    system: str = ""
    temperature: float = 0.0
    expect_json: bool = True
    timeout_seconds: float = 300.0


@dataclass(frozen=True)
class LlmResponse:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LlmError(RuntimeError):
    """provider 调用失败（网络 / 服务未启动 / 超时）。"""


@runtime_checkable
class LlmProvider(Protocol):
    name: str

    def complete(self, model: str, request: LlmRequest) -> LlmResponse: ...


# --------------------------------------------------------------------------- #
# Ollama（本机）
# --------------------------------------------------------------------------- #
@dataclass
class OllamaProvider:
    base_url: str = "http://127.0.0.1:11434"
    name: str = "ollama"

    def complete(self, model: str, request: LlmRequest) -> LlmResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        if request.expect_json:
            payload["format"] = "json"

        started = time.monotonic()
        try:
            with httpx.Client(timeout=request.timeout_seconds) as client:
                resp = client.post(f"{self.base_url.rstrip('/')}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:  # 连接失败 / 超时 / 非 2xx
            raise LlmError(f"Ollama 调用失败（{self.base_url}）：{exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        message = data.get("message") or {}
        return LlmResponse(
            text=message.get("content", "") or "",
            provider=self.name,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )


# --------------------------------------------------------------------------- #
# OpenAI 兼容端点（DeepSeek / OpenAI / Kimi / GLM）
# --------------------------------------------------------------------------- #
@dataclass
class OpenAiCompatProvider:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    name: str = "openai"

    def complete(self, model: str, request: LlmRequest) -> LlmResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.expect_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started = time.monotonic()
        try:
            with httpx.Client(timeout=request.timeout_seconds) as client:
                resp = client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LlmError(f"{self.name} 调用失败（{self.base_url}）：{exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        choices = data.get("choices") or [{}]
        usage = data.get("usage") or {}
        return LlmResponse(
            text=(choices[0].get("message") or {}).get("content", "") or "",
            provider=self.name,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


# --------------------------------------------------------------------------- #
# 脚本化 provider（测试 / 离线演示）
# --------------------------------------------------------------------------- #
@dataclass
class ScriptedProvider:
    """按调用顺序返回预设输出；未配置时返回 ``default``。

    用于单测 —— 让「节点 + 校验 + 重试 + 缓存 + 证据闸门」这条链路
    可以在不启动 Ollama、不花一分钱的前提下被完整验证。
    """

    responses: list[str] = field(default_factory=list)
    default: str = "{}"
    name: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, model: str, request: LlmRequest) -> LlmResponse:
        self.calls.append((model, request.prompt))
        index = len(self.calls) - 1
        text = self.responses[index] if index < len(self.responses) else self.default
        return LlmResponse(text=text, provider=self.name, model=model, latency_ms=1)

    def queue_json(self, payload: dict | list) -> None:
        self.responses.append(json.dumps(payload, ensure_ascii=False))

    def queue_raw(self, text: str) -> None:
        self.responses.append(text)


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
def build_provider(provider: str, base_url: str = "", api_key: str = "") -> LlmProvider:
    key = (provider or "ollama").lower()
    if key == "ollama":
        return OllamaProvider(base_url=base_url or "http://127.0.0.1:11434")
    if key in {"openai", "deepseek", "kimi", "moonshot", "glm", "zhipu"}:
        return OpenAiCompatProvider(
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key,
            name=key,
        )
    if key == "scripted":
        return ScriptedProvider()
    raise LlmError(f"未知 provider：{provider!r}（可用：ollama / openai 兼容端点 / scripted）")


def provider_for_layer(layer: str) -> tuple[LlmProvider, str]:
    """按层（extract / analyze）取出 provider 与模型名。"""
    from app.config import settings

    provider_name, model, base_url, api_key = settings.llm_for(layer)
    return build_provider(provider_name, base_url, api_key), model


def status_of(node_status: NodeRunStatus) -> str:
    return node_status.value


__all__ = [
    "LlmError",
    "LlmProvider",
    "LlmRequest",
    "LlmResponse",
    "OllamaProvider",
    "OpenAiCompatProvider",
    "ScriptedProvider",
    "build_provider",
    "provider_for_layer",
]
