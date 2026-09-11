"""``restructuring`` 策略的失效条件判定（规格 §23 / M5-03）。

**这是「逻辑失效」状态迁移的唯一依据。** 判定全部由规则完成，
LLM 只负责产出事件，不得直接改状态（docs/03 §2.2）。
"""

from __future__ import annotations

from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, InvalidationSeverity, ThesisType
from app.strategies.base import InvalidationHit
from app.strategies.registry import STRATEGIES


def rules() -> tuple:
    """本策略的失效条件定义（来自注册表，单一事实来源）。"""
    return STRATEGIES[ThesisType.RESTRUCTURING].invalidating_events


def _matches(event: EventFact, definition) -> tuple[bool, str]:
    """事件是否命中某条失效条件。"""
    if event.event_type != definition.event_type:
        return False, ""

    # 标题关键词（若定义了）
    if definition.title_contains:
        hit_kw = [kw for kw in definition.title_contains if kw in event.title]
        if not hit_kw:
            return False, ""
        return True, f"标题包含 {'/'.join(hit_kw)}"

    # 金额阈值（若定义了）
    if definition.amount_ratio_gt is not None:
        if event.amount_ratio > definition.amount_ratio_gt:
            return True, f"金额占比 {event.amount_ratio:.2f} > {definition.amount_ratio_gt:.2f}"
        return False, ""

    # 只按事件类型匹配
    return True, "事件类型匹配"


def detect(facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
    hits: list[InvalidationHit] = []
    for event in facts.events:
        for definition in rules():
            ok, reason = _matches(event, definition)
            if ok:
                hits.append(
                    InvalidationHit(
                        event_id=event.id,
                        event_type=event.event_type,
                        severity=definition.severity.value,
                        rule_description=definition.description,
                        reason=reason,
                    )
                )
    return tuple(hits)


def is_terminal(hits: tuple[InvalidationHit, ...]) -> bool:
    return any(h.severity == InvalidationSeverity.TERMINAL.value for h in hits)


def should_invalidate(hits: tuple[InvalidationHit, ...]) -> bool:
    """是否应把机会迁移到 ``invalidated``。

    规格 §20：出现事件**直接破坏**原投资逻辑时进入失效。
    因此只有 ``terminal`` 与 ``severe`` 触发迁移，``warning`` 只记 Alert。
    """
    return any(
        h.severity in (InvalidationSeverity.TERMINAL.value, InvalidationSeverity.SEVERE.value)
        for h in hits
    )


__all__ = ["detect", "is_terminal", "rules", "should_invalidate"]
