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
from collections.abc import Callable
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
    #: 最大输出 token 数。**必须设置** —— 否则模型进入重复生成循环时
    #: 会一直输出到上下文上限，单条可跑几十分钟（实测 21 分钟未结束）。
    max_output_tokens: int = 2500
    #: 是否关闭「思考模式」。思考型模型（qwen3 等）的推理 token 也计入输出上限，
    #: 不关的话上限会被推理吃光、正文一个 token 都轮不到（实测输出长度为 0）。
    disable_thinking: bool = True
    #: ★ **结构化输出**用的 JSON Schema。
    #:
    #: 只给 ``format="json"`` 只能保证「是合法 JSON」，**完全不约束字段** ——
    #: 实测小模型在「要求 JSON + 输入是大段 JSON」时会**直接把输入抄回来**
    #: （``analyze`` / ``score_semantic`` 6/6 校验失败，raw_output 里是输入的字段）。
    #: 传入 Schema 后由服务端做语法约束，模型无法输出不符合结构的对象。
    json_schema: dict | None = None


@dataclass(frozen=True)
class LlmResponse:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    #: 是否因**输出长度上限**而截断（Ollama 的 done_reason == "length"）。
    #: ★ 必须暴露：被截断的 JSON 不完整，会表现为「schema 校验失败」，
    #: 若只看 schema 错误会误以为是模型能力问题，实际是上限设小了。
    truncated: bool = False


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
            "options": {
                "temperature": request.temperature,
                # ★ 必须有上限：否则重复生成循环会让单条请求跑几十分钟
                "num_predict": request.max_output_tokens,
            },
        }
        if request.expect_json:
            # 有 Schema 就用结构化输出（强约束），否则退化为「只要是合法 JSON」
            payload["format"] = request.json_schema or "json"
        if request.disable_thinking:
            # Ollama 0.9+ 支持 think 字段；老的版本会忽略未知字段
            payload["think"] = False

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
        content = message.get("content", "") or ""
        # 防御：某些模型/配置会把结果放进 thinking 字段，content 为空时回退读取。
        # 不这么做的话，表现为「空输出 → schema 失败」，很难定位。
        if not content:
            content = message.get("thinking", "") or ""
        return LlmResponse(
            text=content,
            provider=self.name,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            truncated=data.get("done_reason") == "length",
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
            "max_tokens": request.max_output_tokens,
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
# Anthropic（Claude）—— 不是 OpenAI 兼容协议，需要单独的客户端
# --------------------------------------------------------------------------- #
ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class AnthropicProvider:
    """Claude 原生 ``/v1/messages`` 接口。

    Anthropic 没有 OpenAI 的 ``response_format`` 参数，因此「要求 JSON」
    只能靠 prompt 约束 + 解析重试（``NodeRunner`` 已实现）。
    """

    base_url: str = "https://api.anthropic.com"
    api_key: str = ""
    name: str = "anthropic"

    def complete(self, model: str, request: LlmRequest) -> LlmResponse:
        payload: dict = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            payload["system"] = request.system

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        started = time.monotonic()
        try:
            with httpx.Client(timeout=request.timeout_seconds) as client:
                resp = client.post(
                    f"{self.base_url.rstrip('/')}/v1/messages", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LlmError(f"Anthropic 调用失败（{self.base_url}）：{exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        blocks = data.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return LlmResponse(
            text=text,
            provider=self.name,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            truncated=data.get("stop_reason") == "max_tokens",
        )


# --------------------------------------------------------------------------- #
# provider 预设 —— 让 GitHub 用户「只填 key 就能用」
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProviderPreset:
    """一个 provider 的接入所需全部信息（端点 + key 的环境变量名）。"""

    kind: str            # ollama | openai_compat | anthropic
    base_url: str
    api_key_env: str = ""
    note: str = ""


#: 支持的 provider 全表。**换厂商只改 ``*_PROVIDER`` 名，端点自动带上。**
PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    # ---- 本地（默认，零成本、内容不出本机）----
    "ollama": ProviderPreset(
        "ollama", "http://127.0.0.1:11434", "",
        "本机 Ollama；默认 qwen3:4b。零 API 成本，公告不出本机",
    ),
    # ---- 国内 ----
    "deepseek": ProviderPreset(
        "openai_compat", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
        "DeepSeek；deepseek-chat 便宜、deepseek-reasoner 强",
    ),
    "zhipu": ProviderPreset(
        "openai_compat", "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY",
        "智谱 GLM；glm-4-flash 等",
    ),
    "kimi": ProviderPreset(
        "openai_compat", "https://api.moonshot.cn/v1", "KIMI_API_KEY",
        "月之暗面 Kimi / Moonshot",
    ),
    "dashscope": ProviderPreset(
        "openai_compat",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "阿里通义千问（DashScope 兼容模式）",
    ),
    "siliconflow": ProviderPreset(
        "openai_compat", "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY",
        "硅基流动；聚合多种开源模型",
    ),
    "minimax": ProviderPreset(
        "openai_compat", "https://api.minimax.chat/v1", "MINIMAX_API_KEY",
        "MiniMax",
    ),
    # ---- 海外 ----
    "openai": ProviderPreset(
        "openai_compat", "https://api.openai.com/v1", "OPENAI_API_KEY", "OpenAI",
    ),
    "claude": ProviderPreset(
        "anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY",
        "Anthropic Claude（原生 messages 接口）",
    ),
    "openrouter": ProviderPreset(
        "openai_compat", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        "OpenRouter；一个 key 访问多家模型",
    ),
    "groq": ProviderPreset(
        "openai_compat", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
        "Groq；推理速度极快",
    ),
    # ---- 自建 / 中转 ----
    "custom": ProviderPreset(
        "openai_compat", "", "LLM_API_KEY",
        "任意 OpenAI 兼容端点；必须自行提供 base_url",
    ),
    # ---- 测试 ----
    "scripted": ProviderPreset("scripted", "", "", "脚本化假 provider，仅用于测试"),
}

#: 常见别名 → 规范名（用户写错也能用）
PROVIDER_ALIASES: dict[str, str] = {
    "glm": "zhipu",
    "chatglm": "zhipu",
    "bigmodel": "zhipu",
    "moonshot": "kimi",
    "qwen": "dashscope",
    "tongyi": "dashscope",
    "aliyun": "dashscope",
    "anthropic": "claude",
    "gpt": "openai",
    "silicon": "siliconflow",
    "openai_compatible": "custom",
    "openai-compatible": "custom",
    "vllm": "custom",
    "lmstudio": "custom",
}


def resolve_provider_name(provider: str) -> str:
    """把用户写的名字规范化为预设键（支持常见别名）。"""
    key = (provider or "ollama").strip().lower()
    return PROVIDER_ALIASES.get(key, key)


# --------------------------------------------------------------------------- #
# 脚本化 provider（测试 / 离线演示）
# --------------------------------------------------------------------------- #
@dataclass
class ScriptedProvider:
    """确定性 provider：按调用顺序返回预设输出，或按 prompt 内容分派。

    用于单测与 **mock 源的完整链路** —— 让「节点 + 校验 + 重试 + 缓存 +
    证据闸门 + AI 落库」全部可以在不启动 Ollama、不花一分钱的前提下被回归。

    ``responder`` 是更实用的模式：当一条链路上有多个节点（如
    hunt_risk / analyze / score_semantic）时，用 prompt 里的特征词分派，
    不必手工维护「第几次调用该返回什么」。
    """

    responses: list[str] = field(default_factory=list)
    default: str = "{}"
    name: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list)
    responder: "Callable[[str], str] | None" = None

    def complete(self, model: str, request: LlmRequest) -> LlmResponse:
        self.calls.append((model, request.prompt))
        if self.responder is not None:
            text = self.responder(request.prompt)
        else:
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
    """按名字构造 provider。

    **端点与 key 都有默认来源**（来自 :data:`PROVIDER_PRESETS`），因此
    GitHub 用户只需要写 ``*_PROVIDER=deepseek`` + ``DEEPSEEK_API_KEY=sk-...``，
    不必记 base_url。
    """
    key = resolve_provider_name(provider)
    preset = PROVIDER_PRESETS.get(key)
    if preset is None:
        available = " / ".join(sorted(PROVIDER_PRESETS))
        raise LlmError(
            f"未知 provider：{provider!r}。可用：{available}"
        )

    resolved_base = base_url or preset.base_url
    resolved_key = api_key or _env_api_key(preset.api_key_env, key)

    if preset.kind == "ollama":
        return OllamaProvider(base_url=resolved_base or "http://127.0.0.1:11434")
    if preset.kind == "anthropic":
        return AnthropicProvider(base_url=resolved_base, api_key=resolved_key)
    if preset.kind == "scripted":
        return ScriptedProvider()

    if not resolved_base:
        raise LlmError(
            f"provider={key!r} 需要显式提供 base_url（在 .env 里设 *_BASE_URL）"
        )
    return OpenAiCompatProvider(
        base_url=resolved_base, api_key=resolved_key, name=key
    )


def _env_api_key(env_name: str, provider: str) -> str:
    """按预设的环境变量名读 key；再退回 ``<PROVIDER>_API_KEY``。"""
    import os

    if env_name:
        value = os.getenv(env_name)
        if value:
            return value
    return os.getenv(f"{provider.upper()}_API_KEY", "")


def provider_for_layer(layer: str) -> tuple[LlmProvider, str]:
    """按层（extract / analyze）取出 provider 与模型名。"""
    from app.config import settings

    provider_name, model, base_url, api_key = settings.llm_for(layer)
    return build_provider(provider_name, base_url, api_key), model


def status_of(node_status: NodeRunStatus) -> str:
    return node_status.value


__all__ = [
    "ANTHROPIC_VERSION",
    "PROVIDER_ALIASES",
    "PROVIDER_PRESETS",
    "AnthropicProvider",
    "LlmError",
    "LlmProvider",
    "LlmRequest",
    "LlmResponse",
    "OllamaProvider",
    "OpenAiCompatProvider",
    "ScriptedProvider",
    "ProviderPreset",
    "build_provider",
    "provider_for_layer",
    "resolve_provider_name",
]
