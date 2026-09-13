"""全部枚举（docs/02-领域模型与数据模型 §3）。

约定：统一使用 ``StrEnum`` —— 数据库里存字符串而非整数，便于直接读库排查。
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """事件类型（规格 §10，16 类 + OTHER + 分红政策）。"""

    M_AND_A = "M&A"
    RESTRUCTURING = "RESTRUCTURING"
    ASSET_INJECTION = "ASSET_INJECTION"
    CONTROL_CHANGE = "CONTROL_CHANGE"
    SHAREHOLDER_BUY = "SHAREHOLDER_BUY"
    SHAREHOLDER_SELL = "SHAREHOLDER_SELL"
    BUYBACK = "BUYBACK"
    BANKRUPTCY_REORGANIZATION = "BANKRUPTCY_REORGANIZATION"
    EARNINGS_TURNAROUND = "EARNINGS_TURNAROUND"
    POLICY_CATALYST = "POLICY_CATALYST"
    MAJOR_CONTRACT = "MAJOR_CONTRACT"
    NEW_PRODUCT = "NEW_PRODUCT"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    REGULATORY_RISK = "REGULATORY_RISK"
    LITIGATION = "LITIGATION"
    DIVIDEND_POLICY = "DIVIDEND_POLICY"
    OTHER = "OTHER"


class ThesisType(StrEnum):
    """10 类投资逻辑（规格 §5.6 的 10 个示例，docs/06）。"""

    RESTRUCTURING = "restructuring"
    TURNAROUND = "turnaround"
    VALUE = "value"
    GROWTH = "growth"
    EVENT_DRIVEN = "event_driven"
    POLICY = "policy"
    CYCLE = "cycle"
    PRODUCT = "product"
    SHAREHOLDER_ACTION = "shareholder_action"
    MA_INTEGRATION = "ma_integration"


class OpportunityStatus(StrEnum):
    """机会生命周期（规格 §20）。"""

    DISCOVERED = "discovered"
    PENDING_CONFIRMATION = "pending_confirmation"
    TRACKING = "tracking"
    THESIS_CONFIRMED = "thesis_confirmed"
    OBSERVING = "observing"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class ReliabilityLevel(StrEnum):
    """证据可信度（规格 §16）。"""

    A = "A"  # 公司正式公告 / 交易所披露
    B = "B"  # 公司财报 / 官方文件
    C = "C"  # 高可信媒体
    D = "D"  # 机构 / 研究观点
    E = "E"  # 社交媒体 / 市场讨论


class AssertionKind(StrEnum):
    """区分事实 / 推断 / 假设 / 市场讨论（规格第 60 节第 6 条）。"""

    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    MARKET_DISCUSSION = "market_discussion"


class FreshnessTag(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    BREAKING = "breaking"
    STALE = "stale"


class SourceType(StrEnum):
    ANNOUNCEMENT = "announcement"
    FINANCIAL_REPORT = "financial_report"
    NEWS = "news"
    POLICY = "policy"
    PRICE = "price"
    OTHER = "other"


class EventTimeKind(StrEnum):
    """事件时间与「发生 / 计划 / 推断」的关系。

    ★ 为什么需要它：公告经常**预告未来事件**（股东大会召开日、限售股上市流通日、
    资产交割日）。若一律要求 ``event_time <= discovery_time``（INV-EV1 的字面理解），
    这些公告的事件会被整条丢弃 —— 实测 15 条里有 2 条（13%）因此丢失。

    规格 §40 的原意是「区分事件发生时间与系统发现时间，避免时间顺序错误」，
    而不是禁止未来日期。因此显式区分：

    ``OCCURRED``  已发生（须满足 ``event_time <= discovery_time``）
    ``PLANNED``   计划中（允许未来日期；时效衰减以**公告发布时间**为准）
    ``INFERRED``  未披露，由公告发布时间回退推断
    """

    OCCURRED = "occurred"
    PLANNED = "planned"
    INFERRED = "inferred"


class CertaintyLevel(StrEnum):
    DISCLOSED = "disclosed"
    PARTIALLY_DISCLOSED = "partially_disclosed"
    MEDIA_REPORTED = "media_reported"
    MARKET_RUMOR = "market_rumor"


class ScoreSource(StrEnum):
    RULE = "rule"
    LLM = "llm"


class ScoreDimension(StrEnum):
    """评分维度（规格 §12 / §14）。"""

    THESIS_MATCH = "thesis_match"
    EVENT_CATALYST = "event_catalyst"
    CATALYST_STRENGTH = "catalyst_strength"
    CERTAINTY = "certainty"
    FUNDAMENTALS = "fundamentals"
    SHAREHOLDER_STRUCTURE = "shareholder_structure"
    MARKET_ATTENTION = "market_attention"
    HISTORY_CASE = "history_case"
    RISK = "risk"


DIMENSION_LABELS: dict[ScoreDimension, str] = {
    ScoreDimension.THESIS_MATCH: "逻辑匹配",
    ScoreDimension.EVENT_CATALYST: "事件催化",
    ScoreDimension.CATALYST_STRENGTH: "催化剂强度",
    ScoreDimension.CERTAINTY: "确定性",
    ScoreDimension.FUNDAMENTALS: "基本面",
    ScoreDimension.SHAREHOLDER_STRUCTURE: "股东结构变化",
    ScoreDimension.MARKET_ATTENTION: "市场关注",
    ScoreDimension.HISTORY_CASE: "历史相似案例",
    ScoreDimension.RISK: "风险",
}


class StrategyStatus(StrEnum):
    IMPLEMENTED = "implemented"
    DESIGNED = "designed"
    PLANNED = "planned"


class NodeRunStatus(StrEnum):
    OK = "ok"
    CACHED = "cached"
    SCHEMA_ERROR = "schema_error"
    LLM_ERROR = "llm_error"
    BANNED_WORD = "banned_word"


class ParseStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_OCR = "needs_ocr"


class InvalidationSeverity(StrEnum):
    TERMINAL = "terminal"
    SEVERE = "severe"
    WARNING = "warning"


class OpenQuestionStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    OBSOLETE = "obsolete"


class IngestStage(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    RESCORE = "rescore"


class UserActionKind(StrEnum):
    VIEWED = "viewed"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"
    TRACKED = "tracked"


class AlertType(StrEnum):
    THESIS_INVALIDATED = "thesis_invalidated"
    THESIS_STRENGTHENED = "thesis_strengthened"
    NEW_EVIDENCE = "new_evidence"
    DIVERGENCE_FLAGGED = "divergence_flagged"


#: 状态机合法迁移（规格 §20 / docs/02 §3.3）
ALLOWED_STATUS_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.DISCOVERED: {
        OpportunityStatus.PENDING_CONFIRMATION,
        OpportunityStatus.ARCHIVED,
    },
    OpportunityStatus.PENDING_CONFIRMATION: {
        OpportunityStatus.TRACKING,
        OpportunityStatus.INVALIDATED,
        OpportunityStatus.ARCHIVED,
    },
    OpportunityStatus.TRACKING: {
        OpportunityStatus.THESIS_CONFIRMED,
        OpportunityStatus.INVALIDATED,
        OpportunityStatus.ARCHIVED,
    },
    OpportunityStatus.THESIS_CONFIRMED: {
        OpportunityStatus.OBSERVING,
        OpportunityStatus.INVALIDATED,
    },
    OpportunityStatus.OBSERVING: {
        OpportunityStatus.INVALIDATED,
        OpportunityStatus.ARCHIVED,
    },
    # ★ 失效**不是**绝对的单向门：误判必须能被系统纠正。
    #   实测：规则修正后东兴/信达两张卡的分数从 21 升到 43.5、
    #   阶段从「终止/失败」变成「完成」，状态却永远卡在 invalidated。
    #   允许回到「待确认 / 跟踪」，但**只在失效条件不再成立时** ——
    #   该条件在 opportunity_builder 里判定（状态机是纯函数，看不到事实）。
    #   并且要求连续 2 次判定一致（防抖动，见 recovery_streak）。
    OpportunityStatus.INVALIDATED: {
        OpportunityStatus.ARCHIVED,
        OpportunityStatus.PENDING_CONFIRMATION,
        OpportunityStatus.TRACKING,
    },
    OpportunityStatus.ARCHIVED: set(),
}
