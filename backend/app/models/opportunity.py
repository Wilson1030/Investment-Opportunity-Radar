"""机会层实体（docs/02 §4.10）。

核心：**Opportunity = Company × Profile × Thesis**（规格 §5.7）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import (
    OpportunityStatus,
    ScoreDimension,
    ScoreSource,
    ThesisType,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Opportunity(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("company_id", "profile_id", "thesis_id", name="uq_opportunity_triple"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # ---------- 三元组 ----------
    company_id: int = Field(foreign_key="company.id", index=True)
    profile_id: int = Field(foreign_key="investmentprofile.id", index=True)
    thesis_id: int = Field(foreign_key="thesis.id", index=True)
    user_id: int | None = Field(default=None, foreign_key="investor.id", index=True)  # D02 预留

    # ---------- 状态 ----------
    status: OpportunityStatus = Field(default=OpportunityStatus.DISCOVERED, index=True)

    # ---------- 阶段（规格 §20 的生命周期之外的另一维度）----------
    #: 催化剂阶段（如「早期｜法院受理 / 指定管理人」）。
    #: ★ 必须落库并展示：否则用户会把早期苗头当成确定的事（§24 / §38）。
    catalyst_stage: str = Field(default="", index=True)
    #: 是否为早期信号（催化强度 ≤ 策略定义的 early_stage_max_score）
    is_early_signal: bool = Field(default=False, index=True)

    # ---------- 分数（D08 双分制）----------
    #: 匹配度：该机会与该用户画像的匹配度（规格 §4.3 / §5.8），可解释、可复算
    match_score: float | None = Field(default=None, index=True)
    rule_score: float | None = Field(default=None, index=True)   # 主分，参与排序
    semantic_score: float | None = None                          # 辅分，仅详情页
    divergence: float | None = None
    risk_score: float | None = None

    # ---------- 叙事字段（规格 §25）----------
    summary: str | None = None
    #: ★ 规格 §17「为什么进入你的关注池」= 命中的核心条件（有证据支撑的事实），
    #: 与 why_now（过去/最近/本周/因此 的叙事结构）是**两件事**
    why_in_radar: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    why_now: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    supporting_evidence_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    contradictory_evidence_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    uncertainties: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    risks: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    next_events_to_watch: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # ---------- 时间（规格 §40 四个时间字段独立）----------
    first_discovered_at: datetime = Field(default_factory=_utcnow, index=True)
    last_updated_at: datetime = Field(default_factory=_utcnow)
    score_version: str = ""


class OpportunityScore(SQLModel, table=True):
    """维度分（一级展开：规格 §14 形态）。"""

    __table_args__ = (
        UniqueConstraint("opportunity_id", "dimension", "source", name="uq_score_dim"),
    )

    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    dimension: ScoreDimension = Field(index=True)
    raw_value: float                              # 0~100
    weight: float
    weighted_value: float                         # 风险维度为负
    source: ScoreSource = ScoreSource.RULE
    computed_at: datetime = Field(default_factory=_utcnow)


class ScoreItem(SQLModel, table=True):
    """逐项加减分（二级展开：规格 §13 形态）。

    ``rule_id`` 可追溯到规则源码；``evidence_ids`` 可追溯到原文段落 —— 二者缺一不可（M6-03）。
    """

    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    source: ScoreSource = ScoreSource.RULE
    dimension: ScoreDimension = Field(index=True)
    delta: float                                  # +25 / -10
    reason: str
    rule_id: str | None = None
    evidence_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class OpenQuestion(SQLModel, table=True):
    """「待确认」必须具体（M7-02 / 规格 §24）—— 不是「待确认」这个标签本身。"""

    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    question: str
    status: str = "open"                          # open / confirmed / obsolete
    confirmed_evidence_id: int | None = Field(default=None, foreign_key="evidence.id")
    resolved_at: datetime | None = None


class OpportunityStatusLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    from_status: OpportunityStatus | None = None
    to_status: OpportunityStatus
    reason: str = ""
    triggered_by_event_id: int | None = Field(default=None, foreign_key="event.id")
    score_before: float | None = None
    score_after: float | None = None
    changed_at: datetime = Field(default_factory=_utcnow)


class Alert(SQLModel, table=True):
    """Thesis 监控提醒（M7-04 / 规格 §22）—— 推的是「你的逻辑变了」，不是「公司发公告了」。"""

    id: int | None = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    alert_type: str
    title: str
    message: str
    suggestion: str | None = None
    score_before: float | None = None
    score_after: float | None = None
    triggered_by_event_id: int | None = Field(default=None, foreign_key="event.id")
    is_read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class UserAction(SQLModel, table=True):
    """反馈闭环（规格 §35 / §36）。"""

    id: int | None = Field(default=None, primary_key=True)
    investor_id: int = Field(foreign_key="investor.id", index=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    action: str                                   # viewed / confirmed / ignored / tracked
    reason_thesis_types: list[ThesisType] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    created_at: datetime = Field(default_factory=_utcnow, index=True)
