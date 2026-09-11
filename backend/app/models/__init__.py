"""全部实体聚合 —— 导入本模块即完成 SQLModel metadata 注册。

表名使用 SQLModel 默认（类名小写），因此外键字符串与类名一一对应。
"""

from app.models.audit import IngestRun, LlmNodeRun
from app.models.enums import (
    ALLOWED_STATUS_TRANSITIONS,
    DIMENSION_LABELS,
    AlertType,
    AssertionKind,
    CertaintyLevel,
    EventTimeKind,
    EventType,
    FreshnessTag,
    IngestStage,
    InvalidationSeverity,
    NodeRunStatus,
    OpenQuestionStatus,
    OpportunityStatus,
    ParseStatus,
    ReliabilityLevel,
    ScoreDimension,
    ScoreSource,
    SourceType,
    StrategyStatus,
    ThesisType,
    UserActionKind,
)
from app.models.evidence import (
    SOFT_EVIDENCE_LEVELS,
    THESIS_CONFIRMING_LEVELS,
    Evidence,
)
from app.models.events import Event, EventCluster
from app.models.knowledge import (
    Announcement,
    Company,
    FinancialMetric,
    FinancialPeriod,
    News,
    Paragraph,
    Stock,
)
from app.models.opportunity import (
    Alert,
    OpenQuestion,
    Opportunity,
    OpportunityScore,
    OpportunityStatusLog,
    ScoreItem,
    UserAction,
)
from app.models.profile import (
    InvestmentProfile,
    Investor,
    ProfileThesisWeight,
    WatchlistItem,
)
from app.models.thesis import Thesis, ThesisInvalidationRule, ThesisTypeDef

__all__ = [
    # audit
    "IngestRun",
    "LlmNodeRun",
    # evidence
    "Evidence",
    "SOFT_EVIDENCE_LEVELS",
    "THESIS_CONFIRMING_LEVELS",
    # events
    "Event",
    "EventCluster",
    # knowledge
    "Announcement",
    "Company",
    "FinancialMetric",
    "FinancialPeriod",
    "News",
    "Paragraph",
    "Stock",
    # opportunity
    "Alert",
    "OpenQuestion",
    "Opportunity",
    "OpportunityScore",
    "OpportunityStatusLog",
    "ScoreItem",
    "UserAction",
    # profile
    "InvestmentProfile",
    "Investor",
    "ProfileThesisWeight",
    "WatchlistItem",
    # thesis
    "Thesis",
    "ThesisInvalidationRule",
    "ThesisTypeDef",
    # enums
    "ALLOWED_STATUS_TRANSITIONS",
    "DIMENSION_LABELS",
    "AlertType",
    "AssertionKind",
    "CertaintyLevel",
    "EventTimeKind",
    "EventType",
    "FreshnessTag",
    "IngestStage",
    "InvalidationSeverity",
    "NodeRunStatus",
    "OpenQuestionStatus",
    "OpportunityStatus",
    "ParseStatus",
    "ReliabilityLevel",
    "ScoreDimension",
    "ScoreSource",
    "SourceType",
    "StrategyStatus",
    "ThesisType",
    "UserActionKind",
]
