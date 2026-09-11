"""Evidence —— 证据链一等公民（docs/02 §4.7，规格 §15 / §16 / §32）。

不变量
------
INV-E1 ``relevant_text`` 必须是原文片段，禁止 LLM 改写（否则用户无法核对）。
INV-E2 ``reliability_level`` 为 C/D/E 的证据，不得单独支撑 ``thesis_confirmed``。
INV-E3 ``source_type=announcement`` 时 ``announcement_id`` 不得为空。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.enums import AssertionKind, ReliabilityLevel, SourceType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    # ---------- 来源 ----------
    source_type: SourceType = Field(index=True)
    source_name: str
    source_url: str
    publication_time: datetime = Field(index=True)
    reliability_level: ReliabilityLevel = Field(index=True)

    # ---------- 溯源：段落级（D10 / INV-E1）----------
    announcement_id: int | None = Field(default=None, foreign_key="announcement.id", index=True)
    paragraph_id: int | None = Field(default=None, foreign_key="paragraph.id")
    document_id: str | None = None
    relevant_text: str                            # 原文段落摘录，**不得改写**
    page: int | None = None
    para_index: int | None = None

    # ---------- 语义 ----------
    assertion_kind: AssertionKind = AssertionKind.FACT
    extracted_facts: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    confidence: float = 1.0                       # 对「抽取本身正确」的信心
    produced_by_node_run_id: int | None = Field(
        default=None, foreign_key="llmnoderun.id"
    )
    created_at: datetime = Field(default_factory=_utcnow)


#: 只有 A / B 类证据可以单独支撑 thesis_confirmed（INV-E2 / 规格 §16）
THESIS_CONFIRMING_LEVELS = {ReliabilityLevel.A, ReliabilityLevel.B}

#: C / D / E 类证据唯一合法的评分去处是 MARKET_ATTENTION（docs/04 §4.7）
SOFT_EVIDENCE_LEVELS = {ReliabilityLevel.C, ReliabilityLevel.D, ReliabilityLevel.E}
