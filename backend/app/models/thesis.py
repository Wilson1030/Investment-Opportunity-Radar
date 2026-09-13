"""Thesis 层实体（docs/02 §4.9，规格 §21 / §23）。

设计全量、实现逐个（D13）：:class:`ThesisTypeDef` 为 10 类策略全部建好定义，
``status`` 区分 ``implemented`` / ``designed``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import (
    EventType,
    InvalidationSeverity,
    StrategyStatus,
    ThesisType,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Thesis(SQLModel, table=True):
    """投资逻辑 —— 用户关注的不是「自选股 Y」，而是「因为 X 逻辑，所以关注 Y」（M5-01）。"""

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    thesis_type: ThesisType = Field(index=True)
    statement: str

    # Why Now（M5-04 固定字段，规格 §52）
    why_now_past: str | None = None
    why_now_recent: str | None = None
    why_now_this_week: str | None = None
    why_now_conclusion: str = ""

    supporting_evidence_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    contradictory_evidence_ids: list[int] = Field(  # 反证（M5-06，规格 §51）
        default_factory=list, sa_column=Column(JSON)
    )

    invalidating_event_types: list[EventType] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    open_question_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
