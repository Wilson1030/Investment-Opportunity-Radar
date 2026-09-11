"""事件层实体：Event / EventCluster（docs/02 §4.8，规格 §10 / §31 / §42）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import CertaintyLevel, EventType, SourceType, ThesisType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventCluster(SQLModel, table=True):
    """新闻聚类结果（规格 §42/§43）—— 用户看到「17 条报道」而不是 17 张重复卡片。"""

    id: int | None = Field(default=None, primary_key=True)
    company_id: int | None = Field(default=None, foreign_key="company.id", index=True)
    label: str
    event_type: EventType | None = None
    member_count: int = 0
    key_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)


class Event(SQLModel, table=True):
    """标准化事件。

    INV-EV1  ``event_time <= discovery_time``（采集层必须校验，规格 §40）。
    INV-EV2  同一 ``(company_id, event_type, event_time, source_url)`` 不得重复入库。
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id", "event_type", "event_time", "source_url", name="uq_event_dedup"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    event_type: EventType = Field(index=True)
    title: str
    summary: str

    # 时间（规格 §40 严格区分）
    event_time: datetime = Field(index=True)
    discovery_time: datetime = Field(default_factory=_utcnow, index=True)

    # 双维评分（M3-04：「重要性高但确定性低」必须可表达）
    importance: float = 0.5
    certainty: float = 0.5
    certainty_level: CertaintyLevel = CertaintyLevel.DISCLOSED

    source_type: SourceType = SourceType.ANNOUNCEMENT
    source_url: str = ""

    affected_thesis: list[ThesisType] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    cluster_id: int | None = Field(default=None, foreign_key="eventcluster.id", index=True)

    attributes: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_invalidating: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
