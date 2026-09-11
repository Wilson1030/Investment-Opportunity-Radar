"""信息新鲜度与时效衰减（规格 §40 / §41，M2-08 / M2-09）。

**只对 ``EVENT_CATALYST`` 与 ``MARKET_ATTENTION`` 两个维度施加衰减。**
不施加于 ``CERTAINTY`` / ``FUNDAMENTALS`` / ``SHAREHOLDER_STRUCTURE``：
一条三个月前披露的控股权变更**依然是事实**，不应因时间久远而降低确定性。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import FreshnessTag

#: 相对时间标签的阈值（小时）
BREAKING_HOURS = 4.0
NEW_HOURS = 24.0
UPDATED_HOURS = 24.0 * 7


@dataclass(frozen=True)
class DecayStep:
    max_days: float
    factor: float


#: 衰减阶梯（docs/04 §5），顺序即优先级
DECAY_STEPS: tuple[DecayStep, ...] = (
    DecayStep(1.0, 1.00),
    DecayStep(3.0, 0.85),
    DecayStep(7.0, 0.70),
    DecayStep(30.0, 0.50),
)
STALE_FACTOR = 0.30  # > 30 天


def decay_factor(age_days: float) -> float:
    """时效衰减系数 ∈ (0, 1]。"""
    if age_days is None or age_days < 0:
        age_days = 0.0
    for step in DECAY_STEPS:
        if age_days <= step.max_days:
            return step.factor
    return STALE_FACTOR


def freshness_tag(age_days: float) -> FreshnessTag:
    hours = max(0.0, age_days) * 24.0
    if hours <= BREAKING_HOURS:
        return FreshnessTag.BREAKING
    if hours <= NEW_HOURS:
        return FreshnessTag.NEW
    if hours <= UPDATED_HOURS:
        return FreshnessTag.UPDATED
    return FreshnessTag.STALE


def relative_time(age_days: float) -> str:
    """中文相对时间（规格 §41：「2小时前 / 1天前 / 3天前 / 7天前」）。"""
    minutes = max(0.0, age_days) * 24 * 60
    if minutes < 60:
        return f"{int(minutes)}分钟前" if minutes >= 1 else "刚刚"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}小时前"
    days = hours / 24
    if days < 30:
        return f"{int(days)}天前"
    months = days / 30
    if months < 12:
        return f"{int(months)}个月前"
    return f"{int(months / 12)}年前"


def apply_decay(raw_value: float, age_days: float) -> float:
    return raw_value * decay_factor(age_days)


def is_decay_exempt(dimension: str) -> bool:
    """该维度是否豁免时效衰减。"""
    return str(dimension) not in {"event_catalyst", "market_attention"}


__all__ = [
    "BREAKING_HOURS",
    "DECAY_STEPS",
    "NEW_HOURS",
    "STALE_FACTOR",
    "UPDATED_HOURS",
    "DecayStep",
    "apply_decay",
    "decay_factor",
    "freshness_tag",
    "is_decay_exempt",
    "relative_time",
]
