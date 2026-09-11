"""节点输入 / 输出 Schema（docs/03 §4 的节点契约）。

**所有 LLM 输出必须通过这里的 Pydantic 校验才能落库** —— 校验失败即视为失败，
绝不把不合规的数据写进数据库（docs/03 §6.2）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AssertionKind,
    CertaintyLevel,
    EventType,
    ReliabilityLevel,
    ThesisType,
)

_STRICT = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# 共用
# --------------------------------------------------------------------------- #
class EvidenceSliceModel(BaseModel):
    """LLM 指出的原文出处。``relevant_text`` 必须是原文片段（INV-E1）。"""

    model_config = _STRICT

    page: int = Field(ge=1)
    para_index: int = Field(ge=1)
    relevant_text: str
    evidence_id: int | None = None


class CompanyInput(BaseModel):
    model_config = _STRICT

    name: str = ""
    code: str = ""
    is_st: bool = False
    industry: str | None = None


class AnnouncementInput(BaseModel):
    model_config = _STRICT

    document_id: str = ""
    title: str = ""
    announcement_type: str | None = None
    publication_time: datetime | None = None


class ParagraphInput(BaseModel):
    model_config = _STRICT

    page: int
    para_index: int
    text: str


# --------------------------------------------------------------------------- #
# 1) extract_event
# --------------------------------------------------------------------------- #
class ExtractEventInput(BaseModel):
    model_config = _STRICT

    company: CompanyInput
    announcement: AnnouncementInput
    paragraphs: list[ParagraphInput] = Field(default_factory=list)


class ExtractedFact(BaseModel):
    model_config = _STRICT

    statement: str
    assertion_kind: AssertionKind = AssertionKind.FACT


class ExtractEventOutput(BaseModel):
    model_config = _STRICT

    event_type: EventType
    title: str
    summary: str
    #: 未披露时为 None —— **不得猜测**（规格 §40）
    event_time: datetime | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    certainty: float = Field(default=0.5, ge=0.0, le=1.0)
    certainty_level: CertaintyLevel = CertaintyLevel.DISCLOSED
    affected_thesis: list[ThesisType] = Field(default_factory=list)
    extracted_facts: list[ExtractedFact] = Field(default_factory=list)
    evidence_slices: list[EvidenceSliceModel] = Field(default_factory=list)
    #: 明确「公告中未提及」的信息（防止模型补全 —— M4-06）
    not_mentioned: list[str] = Field(default_factory=list)
    counterparty_known: bool = False
    amount_ratio: float = Field(default=0.0, ge=0.0)


# --------------------------------------------------------------------------- #
# 2) classify_thesis
# --------------------------------------------------------------------------- #
class ThesisDefBrief(BaseModel):
    model_config = _STRICT

    thesis_type: ThesisType
    display_name: str
    user_goal: str = ""
    support_event_types: list[EventType] = Field(default_factory=list)


class ClassifyThesisInput(BaseModel):
    model_config = _STRICT

    event: ExtractEventOutput
    company_facts: dict = Field(default_factory=dict)
    candidate_thesis_defs: list[ThesisDefBrief] = Field(default_factory=list)


class ThesisCandidate(BaseModel):
    model_config = _STRICT

    thesis_type: ThesisType
    rationale: str
    hit_evidence_slices: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ClassifyThesisOutput(BaseModel):
    model_config = _STRICT

    candidates: list[ThesisCandidate] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 3) hunt_risk ★ 反证（规格 §51）
# --------------------------------------------------------------------------- #
class EvidenceBrief(BaseModel):
    model_config = _STRICT

    id: int
    reliability_level: ReliabilityLevel
    relevant_text: str = ""
    publication_time: datetime | None = None


class HuntRiskInput(BaseModel):
    model_config = _STRICT

    thesis_type: ThesisType
    thesis_statement: str = ""
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    company_history: dict = Field(default_factory=dict)


class ContradictoryEvidence(BaseModel):
    model_config = _STRICT

    kind: str                      # history_failure / regulatory / financial / execution
    description: str
    evidence_ids: list[int] = Field(default_factory=list)


class HuntRiskOutput(BaseModel):
    model_config = _STRICT

    #: 字段必须存在（可为空列表）——不得省略（规格 §51 硬约束）
    contradictory_evidence: list[ContradictoryEvidence] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    #: 若确实无已知反证，必须显式说明，而不是省略字段
    no_contradiction_statement: str | None = None
    confidence_assessment: str = ""


# --------------------------------------------------------------------------- #
# 4) analyze（研究卡叙事字段，规格 §25）
# --------------------------------------------------------------------------- #
class AnalyzeInput(BaseModel):
    model_config = _STRICT

    thesis_type: ThesisType
    thesis_statement: str = ""
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    company_facts: dict = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)


class AnalyzeOutput(BaseModel):
    model_config = _STRICT

    summary: str
    #: Why Now 四段（规格 §52）
    why_now: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_events_to_watch: list[str] = Field(default_factory=list)
    assertion_kinds: dict[str, AssertionKind] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 5) score_semantic（D08 辅分）
# --------------------------------------------------------------------------- #
class RuleItemBrief(BaseModel):
    model_config = _STRICT

    dimension: str
    delta: float
    reason: str


class ScoreSemanticInput(BaseModel):
    model_config = _STRICT

    thesis_type: ThesisType
    rule_score: float
    rule_score_items: list[RuleItemBrief] = Field(default_factory=list)
    thesis_statement: str = ""
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    company_facts: dict = Field(default_factory=dict)


class SemanticFactor(BaseModel):
    model_config = _STRICT

    direction: str                 # positive / negative
    factor: str
    rationale: str = ""
    evidence_ids: list[int] = Field(default_factory=list)


class ScoreSemanticOutput(BaseModel):
    model_config = _STRICT

    semantic_score: float = Field(ge=0.0, le=100.0)
    factors: list[SemanticFactor] = Field(default_factory=list)
    #: 模型自述哪些因素已被规则覆盖（防同一因素计两次）
    rules_already_covered: list[str] = Field(default_factory=list)


__all__ = [
    "AnalyzeInput",
    "AnalyzeOutput",
    "AnnouncementInput",
    "ClassifyThesisInput",
    "ClassifyThesisOutput",
    "CompanyInput",
    "ContradictoryEvidence",
    "EvidenceBrief",
    "EvidenceSliceModel",
    "ExtractEventInput",
    "ExtractEventOutput",
    "ExtractedFact",
    "HuntRiskInput",
    "HuntRiskOutput",
    "ParagraphInput",
    "RuleItemBrief",
    "ScoreSemanticInput",
    "ScoreSemanticOutput",
    "SemanticFactor",
    "ThesisCandidate",
    "ThesisDefBrief",
]
