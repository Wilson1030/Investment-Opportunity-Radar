"""采集与 AI 审计实体（docs/02 §4.11）。

* :class:`IngestRun`  —— 让「解析失败率」等质量指标**可观测**（M2-13，风险 R2 的缓解）。
* :class:`LlmNodeRun` —— 每条 LLM 结果记录 ``model`` + ``prompt_version``（M11-06），
  并且**该表本身即是节点级缓存**（M11-07）：唯一键
  ``(node_name, input_hash, prompt_version)`` 命中即直接返回 ``output_json``，不再消耗 token。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import NodeRunStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngestRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    adapter: str
    scope: str
    dry_run: bool = True
    stage: str = "incremental"                    # full / incremental / rescore

    started_at: datetime = Field(default_factory=_utcnow, index=True)
    finished_at: datetime | None = None

    items_found: int = 0
    items_new: int = 0
    items_skipped: int = 0
    items_failed: int = 0

    parse_failed: int = 0
    parse_needs_ocr: int = 0
    parse_failure_rate: float | None = None
    field_missing_rate: float | None = None

    #: 漏斗计数（回答「为什么今天只有 3 张卡」）
    funnel: dict = Field(default_factory=dict, sa_column=Column(JSON))
    #: 质量指标（回答「数据质量如何 / 4B 模型能否胜任」）
    quality: dict = Field(default_factory=dict, sa_column=Column(JSON))

    errors: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    error_summary: str | None = None


class LlmNodeRun(SQLModel, table=True):
    """节点执行审计 + 节点级缓存。"""

    __table_args__ = (
        UniqueConstraint("node_name", "input_hash", "prompt_version", name="uq_llm_cache_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    node_name: str = Field(index=True)
    input_hash: str = Field(index=True)
    prompt_version: str = Field(index=True)

    provider: str = "ollama"
    model: str = Field(default="qwen3:4b", index=True)
    layer: str = "extract"                        # extract / analyze

    status: NodeRunStatus = NodeRunStatus.OK
    attempt: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None

    output_json: dict | None = Field(default=None, sa_column=Column(JSON))
    raw_output: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, index=True)
