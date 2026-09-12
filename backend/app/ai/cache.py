"""节点级缓存（M11-07 / D06）。

缓存键：``(node_name, input_hash, prompt_version)``。

* ``input_hash`` = 规范化 JSON 的 sha256（键序固定 → 同一输入必然同 hash）
* ``prompt_version`` 递增 → 缓存自然失效（无需手工清理）

**缓存命中率直接决定 API 账单** —— 这是选择「纯函数节点 + 函数级缓存」
而不是「图编排」的核心理由之一（D06）。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel
from sqlmodel import Session, select

from app.models.audit import LlmNodeRun
from app.models.enums import NodeRunStatus

#: 视为「可复用」的状态
REUSABLE_STATUSES = {NodeRunStatus.OK, NodeRunStatus.CACHED}


def canonical_json(value: Any) -> str:
    """规范化 JSON：键有序、无多余空白、浮点统一表示。"""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def compute_input_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    id: int | None
    output: dict | None
    status: NodeRunStatus
    model: str
    provider: str
    attempts: int

    @property
    def reusable(self) -> bool:
        return self.status in REUSABLE_STATUSES and self.output is not None


class NodeCache(Protocol):
    def get(self, node_name: str, input_hash: str, prompt_version: str) -> CacheEntry | None: ...

    def put(
        self,
        *,
        node_name: str,
        input_hash: str,
        prompt_version: str,
        provider: str,
        model: str,
        layer: str,
        status: NodeRunStatus,
        output: dict | None = None,
        raw_output: str | None = None,
        error: str | None = None,
        attempt: int = 1,
        latency_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> int | None: ...


class InMemoryNodeCache:
    """测试用缓存（不碰 DB）。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], CacheEntry] = {}
        self._next_id = 1

    def get(self, node_name: str, input_hash: str, prompt_version: str) -> CacheEntry | None:
        return self.rows.get((node_name, input_hash, prompt_version))

    def put(self, *, node_name: str, input_hash: str, prompt_version: str,
            provider: str, model: str, layer: str, status: NodeRunStatus,
            output: dict | None = None, raw_output: str | None = None,
            error: str | None = None, attempt: int = 1,
            latency_ms: int | None = None, prompt_tokens: int | None = None,
            completion_tokens: int | None = None) -> int:
        key = (node_name, input_hash, prompt_version)
        existing = self.rows.get(key)
        entry = CacheEntry(
            id=existing.id if existing else self._next_id,
            output=output,
            status=status,
            model=model,
            provider=provider,
            attempts=attempt,
        )
        if existing is None:
            self._next_id += 1
        self.rows[key] = entry
        self.last_error = error
        return entry.id or 0


class SqlNodeCache:
    """以 ``llm_node_run`` 表为缓存载体（docs/03 §5.2）。

    ★ **线程安全**：抽取阶段可以并发调用 LLM（节点是纯函数），
    但缓存读写有副作用，必须串行化 —— 否则多线程会同时改同一个 Session。
    因此内置一把类级锁：**并发只发生在 LLM 调用上，缓存 IO 串行**。
    """

    #: 类级锁：同一进程内所有缓存实例共享（Session 本身也不是线程安全的）
    _io_lock = threading.Lock()

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, node_name: str, input_hash: str, prompt_version: str) -> CacheEntry | None:
        with self._io_lock:
            return self._get_locked(node_name, input_hash, prompt_version)

    def _get_locked(
        self, node_name: str, input_hash: str, prompt_version: str
    ) -> CacheEntry | None:
        row = self.session.exec(
            select(LlmNodeRun).where(
                LlmNodeRun.node_name == node_name,
                LlmNodeRun.input_hash == input_hash,
                LlmNodeRun.prompt_version == prompt_version,
            )
        ).first()
        if row is None:
            return None
        return CacheEntry(
            id=row.id,
            output=row.output_json,
            status=row.status,
            model=row.model,
            provider=row.provider,
            attempts=row.attempt,
        )

    def put(self, *, node_name: str, input_hash: str, prompt_version: str,
            provider: str, model: str, layer: str, status: NodeRunStatus,
            output: dict | None = None, raw_output: str | None = None,
            error: str | None = None, attempt: int = 1,
            latency_ms: int | None = None, prompt_tokens: int | None = None,
            completion_tokens: int | None = None) -> int:
        with self._io_lock:
            return self._put_locked(
                node_name=node_name, input_hash=input_hash,
                prompt_version=prompt_version, provider=provider, model=model,
                layer=layer, status=status, output=output, raw_output=raw_output,
                error=error, attempt=attempt, latency_ms=latency_ms,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )

    def _put_locked(self, **kwargs) -> int:
        node_name = kwargs["node_name"]
        input_hash = kwargs["input_hash"]
        prompt_version = kwargs["prompt_version"]
        # 从 kwargs 取出全部字段（保持下面的函数体不变）
        provider = kwargs["provider"]
        model = kwargs["model"]
        layer = kwargs["layer"]
        status = kwargs["status"]
        output = kwargs["output"]
        raw_output = kwargs["raw_output"]
        error = kwargs["error"]
        attempt = kwargs["attempt"]
        latency_ms = kwargs["latency_ms"]
        prompt_tokens = kwargs["prompt_tokens"]
        completion_tokens = kwargs["completion_tokens"]
        existing = self._get_locked(node_name, input_hash, prompt_version)
        if existing is not None and existing.id is not None:
            row = self.session.get(LlmNodeRun, existing.id)
            assert row is not None
            row.status = status
            row.attempt = attempt
            row.output_json = output
            row.raw_output = raw_output
            row.error = error
            row.latency_ms = latency_ms
            row.provider = provider
            row.model = model
        else:
            row = LlmNodeRun(
                node_name=node_name,
                input_hash=input_hash,
                prompt_version=prompt_version,
                provider=provider,
                model=model,
                layer=layer,
                status=status,
                attempt=attempt,
                output_json=output,
                raw_output=raw_output,
                error=error,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return int(row.id or 0)


__all__ = [
    "REUSABLE_STATUSES",
    "CacheEntry",
    "InMemoryNodeCache",
    "NodeCache",
    "SqlNodeCache",
    "canonical_json",
    "compute_input_hash",
]
