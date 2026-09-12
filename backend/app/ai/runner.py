"""节点执行器：把「LLM 调用 / 缓存 / 重试 / 审计 / 校验」全部放在节点**之外**。

这是 D06 可逆性的关键设计：节点保持 ``f(input) -> output`` 的纯函数形态，
所有副作用集中在本模块，因此将来要迁移到 LangGraph，只需写一个 ``graph.py``
把现有节点函数包成图节点，**一天可迁移**（docs/03 §10）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.ai.cache import CacheEntry, NodeCache, compute_input_hash
from app.ai.provider import LlmError, LlmProvider, LlmRequest
from app.engine import guard
from app.models.enums import NodeRunStatus

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class Node(Protocol):
    name: str
    prompt_version: str
    layer: str                  # extract | analyze
    Input: type[BaseModel]
    Output: type[BaseModel]

    def system_prompt(self) -> str: ...
    def user_prompt(self, payload: BaseModel) -> str: ...


@dataclass
class NodeRunResult:
    node_name: str
    status: NodeRunStatus
    output: BaseModel | None = None
    from_cache: bool = False
    attempts: int = 0
    run_id: int | None = None
    raw_text: str = ""
    error: str | None = None
    banned_words: tuple[str, ...] = ()
    latency_ms: int = 0
    model: str = ""
    provider: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (NodeRunStatus.OK, NodeRunStatus.CACHED) and self.output is not None


def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON。

    处理三种情况：纯 JSON / ```` ```json fenced ```` / 前后有解释性文字。
    **不做任何「修复性猜测」**（例如补全缺失括号）—— 那会把不合规输出洗成看似合规。
    """
    if not text:
        raise ValueError("空输出")
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    for match in _FENCE.findall(text):
        candidates.append(match.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    start, end = stripped.find("["), stripped.rfind("]")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise ValueError(f"无法解析为 JSON：{last_error}")


@dataclass
class NodeRunner:
    provider: LlmProvider
    cache: NodeCache
    model: str
    temperature: float = 0.0
    max_attempts: int = 3
    timeout_seconds: float = 300.0
    ban_word_retries: int = 2
    #: 单次生成的最大输出 token 数（防重复生成循环，见 provider.LlmRequest）
    max_output_tokens: int = 900
    #: 执行日志（供 pipeline 汇总与测试断言）
    log: list[NodeRunResult] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def run(self, node: Node, payload: BaseModel, *, use_cache: bool = True) -> NodeRunResult:
        input_hash = compute_input_hash(payload)

        if use_cache:
            entry = self.cache.get(node.name, input_hash, node.prompt_version)
            if entry is not None and entry.reusable:
                return NodeRunResult(
                    node_name=node.name,
                    status=NodeRunStatus.CACHED,
                    output=node.Output.model_validate(entry.output),
                    from_cache=True,
                    attempts=entry.attempts,
                    run_id=entry.id,
                    model=entry.model,
                    provider=entry.provider,
                )

        system = node.system_prompt()
        prompt = node.user_prompt(payload)
        request = LlmRequest(
            prompt=prompt,
            system=system,
            temperature=self.temperature,
            expect_json=True,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        last_status = NodeRunStatus.LLM_ERROR
        last_error: str | None = None
        last_raw = ""
        latency = 0

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.provider.complete(self.model, request)
            except LlmError as exc:
                last_status, last_error = NodeRunStatus.LLM_ERROR, str(exc)
                continue

            last_raw = response.text
            latency = response.latency_ms

            # ---- 禁用词自检（M11-02）----
            banned = guard.find_banned_words(response.text)
            if banned.hit:
                last_status = NodeRunStatus.BANNED_WORD
                last_error = f"输出包含禁用词：{'/'.join(banned.words)}"
                if attempt <= self.ban_word_retries:
                    request = LlmRequest(
                        prompt=(
                            f"{prompt}\n\n【上一次输出被拒绝】原因：使用了确定性语言"
                            f"（{'、'.join(banned.words)}）。\n"
                            "请改用「存在……／可能……／目前证据支持……／值得进一步确认……」"
                            "这类表述重新输出。"
                        ),
                        system=system,
                        temperature=self.temperature,
                        expect_json=True,
            max_output_tokens=self.max_output_tokens,
                        timeout_seconds=self.timeout_seconds,
                    )
                    continue
                break

            # ---- JSON 解析 + Schema 校验 ----
            try:
                data = extract_json(response.text)
                output = node.Output.model_validate(data)
            except (ValueError, ValidationError) as exc:
                last_status = NodeRunStatus.SCHEMA_ERROR
                last_error = f"{type(exc).__name__}: {exc}"
                # 把错误回灌给模型 —— 这是让本机 4B 模型可用的关键手段
                request = LlmRequest(
                    prompt=(
                        f"{prompt}\n\n【上一次输出不合规】错误：{str(exc)[:600]}\n"
                        "请严格按 JSON Schema 重新输出，只输出 JSON，不要任何解释文字。"
                    ),
                    system=system,
                    temperature=self.temperature,
                    expect_json=True,
            max_output_tokens=self.max_output_tokens,
                    timeout_seconds=self.timeout_seconds,
                )
                continue

            run_id = self.cache.put(
                node_name=node.name,
                input_hash=input_hash,
                prompt_version=node.prompt_version,
                provider=response.provider,
                model=response.model,
                layer=node.layer,
                status=NodeRunStatus.OK,
                output=output.model_dump(mode="json"),
                raw_output=response.text,
                attempt=attempt,
                latency_ms=response.latency_ms,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            result = NodeRunResult(
                node_name=node.name,
                status=NodeRunStatus.OK,
                output=output,
                attempts=attempt,
                run_id=run_id,
                raw_text=response.text,
                latency_ms=response.latency_ms,
                model=response.model,
                provider=response.provider,
            )
            self.log.append(result)
            return result

        run_id = self.cache.put(
            node_name=node.name,
            input_hash=input_hash,
            prompt_version=node.prompt_version,
            provider=getattr(self.provider, "name", "unknown"),
            model=self.model,
            layer=node.layer,
            status=last_status,
            output=None,
            raw_output=last_raw,
            error=last_error,
            attempt=self.max_attempts,
            latency_ms=latency,
        )
        result = NodeRunResult(
            node_name=node.name,
            status=last_status,
            output=None,
            attempts=self.max_attempts,
            run_id=run_id,
            raw_text=last_raw,
            error=last_error,
            latency_ms=latency,
            model=self.model,
            provider=getattr(self.provider, "name", "unknown"),
        )
        self.log.append(result)
        return result

    # ------------------------------------------------------------------ #
    @property
    def stats(self) -> dict[str, int]:
        total = len(self.log)
        cached = sum(1 for r in self.log if r.status is NodeRunStatus.CACHED)
        ok = sum(1 for r in self.log if r.status is NodeRunStatus.OK)
        schema = sum(1 for r in self.log if r.status is NodeRunStatus.SCHEMA_ERROR)
        banned = sum(1 for r in self.log if r.status is NodeRunStatus.BANNED_WORD)
        llm_err = sum(1 for r in self.log if r.status is NodeRunStatus.LLM_ERROR)
        calls = total - cached
        return {
            "total": total,
            "ok": ok,
            "cached": cached,
            "schema_error": schema,
            "banned_word": banned,
            "llm_error": llm_err,
            "calls": calls,
            "schema_failure_rate": (schema / calls) if calls else 0.0,
            "cached_rate": (cached / total) if total else 0.0,
        }


__all__ = ["Node", "NodeRunResult", "NodeRunner", "extract_json"]
