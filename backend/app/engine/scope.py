"""候选池筛选（规格 §27 / docs/03 §3，D11）。

本轮 ``scope = st_and_risk_warning``：约 300 只 —— ST/风险警示全量 + 近 90 天有
重组类 / 控制权类公告的公司。改一行配置即可放宽到 ``all_a_shares``。

**为什么要有候选池**：规格 §26 明确「不要让一个 LLM 直接分析全部股票」。
本模块负责第一级漏斗（5000+ → ~300），把 LLM 调用量压到本机模型能跑完的量级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.enums import EventType
from app.models.knowledge import Announcement, Company

#: 缩短到候选池相关的事件类型（D11）
SCOPE_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.RESTRUCTURING,
    EventType.ASSET_INJECTION,
    EventType.CONTROL_CHANGE,
    EventType.BANKRUPTCY_REORGANIZATION,
    EventType.M_AND_A,
)

REASON_ST = "ST / *ST 名单"
REASON_ALL_A = "全 A 股范围"
#: 带天数的理由模板（渲染时格式化）
_TEMPLATE_RESTRUCTURING = "近 {days} 天有重组类公告"
_TEMPLATE_CONTROL_CHANGE = "近 {days} 天有控制权 / 实际控制人变更"

#: 理由分类键（用于统计，把带天数的理由归并为一类）
REASON_KEY_ST = "st_list"
REASON_KEY_RESTRUCTURING = "recent_restructuring"
REASON_KEY_CONTROL_CHANGE = "recent_control_change"
REASON_KEY_ALL_A = "all_a_shares"
REASON_KEYS: dict[str, str] = {
    REASON_ST: REASON_KEY_ST,
    REASON_ALL_A: REASON_KEY_ALL_A,
}


def reason_key(reason: str) -> str:
    """把展示用理由归一化为统计键。"""
    if reason in REASON_KEYS:
        return REASON_KEYS[reason]
    if "重组类公告" in reason:
        return REASON_KEY_RESTRUCTURING
    if "控制权" in reason:
        return REASON_KEY_CONTROL_CHANGE
    return "other"


@dataclass(frozen=True)
class ScopeConfig:
    scope: str = "st_and_risk_warning"
    lookback_days: int = 90

    @property
    def is_all_market(self) -> bool:
        return self.scope == "all_a_shares"


@dataclass(frozen=True)
class ScopeResult:
    company_ids: tuple[int, ...]
    reasons: dict[int, tuple[str, ...]] = field(default_factory=dict)

    @property
    def counts_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reason_list in self.reasons.values():
            for reason in reason_list:
                key = reason_key(reason)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def reason_for(self, company_id: int) -> tuple[str, ...]:
        return self.reasons.get(company_id, ())


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不需要 DB）
# --------------------------------------------------------------------------- #
def classify_scope_reason(
    *,
    is_st: bool,
    is_risk_warning: bool,
    restructuring_hits: int = 0,
    control_change_hits: int = 0,
    lookback_days: int = 90,
    all_market: bool = False,
) -> tuple[str, ...]:
    """返回该公司进入候选池的全部理由（空元组表示不入池）。"""
    if all_market:
        return (REASON_ALL_A,)

    reasons: list[str] = []
    if is_st or is_risk_warning:
        reasons.append(REASON_ST)
    if restructuring_hits > 0:
        reasons.append(_TEMPLATE_RESTRUCTURING.format(days=lookback_days))
    if control_change_hits > 0:
        reasons.append(_TEMPLATE_CONTROL_CHANGE.format(days=lookback_days))
    return tuple(reasons)


# --------------------------------------------------------------------------- #
# DB 版本
# --------------------------------------------------------------------------- #
def _recent_counts(
    session: Session, since: datetime
) -> tuple[dict[int, int], dict[int, int]]:
    rows = session.exec(
        select(Announcement.company_id, Announcement.event_type).where(
            Announcement.publication_time >= since,
            Announcement.event_type.in_(SCOPE_EVENT_TYPES),  # type: ignore[attr-defined]
        )
    ).all()

    restructuring: dict[int, int] = {}
    control: dict[int, int] = {}
    for company_id, event_type in rows:
        if event_type in (EventType.RESTRUCTURING, EventType.ASSET_INJECTION,
                          EventType.BANKRUPTCY_REORGANIZATION, EventType.M_AND_A):
            restructuring[company_id] = restructuring.get(company_id, 0) + 1
        if event_type is EventType.CONTROL_CHANGE:
            control[company_id] = control.get(company_id, 0) + 1
    return restructuring, control


def select_candidates(session: Session, config: ScopeConfig | None = None) -> ScopeResult:
    """从库中选出候选池公司。"""
    config = config or ScopeConfig()
    if config.is_all_market:
        companies = session.exec(select(Company.id)).all()
        ids = tuple(int(c) for c in companies)
        return ScopeResult(ids, {i: (REASON_ALL_A,) for i in ids})

    since = datetime.now(timezone.utc) - timedelta(days=config.lookback_days)
    restructuring, control = _recent_counts(session, since)

    companies = session.exec(
        select(Company.id, Company.is_st, Company.is_risk_warning)
    ).all()

    ids: list[int] = []
    reasons: dict[int, tuple[str, ...]] = {}
    for company_id, is_st, is_risk_warning in companies:
        company_reasons = classify_scope_reason(
            is_st=bool(is_st),
            is_risk_warning=bool(is_risk_warning),
            restructuring_hits=restructuring.get(company_id, 0),
            control_change_hits=control.get(company_id, 0),
            lookback_days=config.lookback_days,
        )
        if company_reasons:
            ids.append(int(company_id))
            reasons[int(company_id)] = company_reasons

    return ScopeResult(tuple(ids), reasons)


__all__ = [
    "REASON_ALL_A",
    "REASON_KEYS",
    "REASON_KEY_ALL_A",
    "REASON_KEY_CONTROL_CHANGE",
    "REASON_KEY_RESTRUCTURING",
    "REASON_KEY_ST",
    "REASON_ST",
    "SCOPE_EVENT_TYPES",
    "ScopeConfig",
    "ScopeResult",
    "classify_scope_reason",
    "reason_key",
    "select_candidates",
]
