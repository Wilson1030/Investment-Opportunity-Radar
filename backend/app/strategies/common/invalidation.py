"""泛化的失效检测 —— 驱动 registry 的失效规则，适配任意策略。

与 ``restructuring`` / ``turnaround`` 的差异通过两个扩展点表达：

  · ``extra_hits`` —— 策略特有的**非事件**失效判定。
    例：``turnaround`` 要能从财务数据判「营收与毛利率同时重新转弱」，
    因为反转逻辑常死于「悄悄又亏了一个季度」，而不是死于某条公告。
  · ``confirmed_improvement`` —— 判断「改善是否已被正式确认」，
    用于 ``early_only`` 规则（对已被年报确认的改善，费用类公告才是真风险）。

**为什么 ``early_only`` 规则在这里被跳过**：它们是有条件的 ——
问询函对早期待确认的机会是终止前兆，对已披露草案的机会是常规流程。
事件层（``event_writer._mark_invalidating``）无从判断阶段，
所以那类规则不能烤进 ``Event.is_invalidating``，只能在这里按事实判定。
"""

from __future__ import annotations

from typing import Callable

from app.facts import EventFact, StrategyFacts
from app.models.enums import InvalidationSeverity, ThesisType
from app.strategies.base import InvalidationHit
from app.strategies.common.matcher import match_rule
from app.strategies.registry import STRATEGIES

#: 「改善已被正式确认」的证据词 —— 供 ``early_only`` 规则使用
CONFIRMED_IMPROVEMENT_KEYWORDS: tuple[str, ...] = (
    "年报", "年度报告", "审计报告", "业绩快报", "扭亏为盈", "净利润为正",
)


def rules_for(thesis_type: ThesisType | str) -> tuple:
    """该策略的失效规则（来自 registry，单一事实来源）。"""
    return STRATEGIES[ThesisType(thesis_type)].invalidating_events


def has_confirmed_improvement(facts: StrategyFacts) -> bool:
    return any(
        any(kw in (event.title or "") for kw in CONFIRMED_IMPROVEMENT_KEYWORDS)
        for event in facts.events
    )


def detect(
    facts: StrategyFacts,
    thesis_type: ThesisType | str,
    *,
    extra_hits: Callable[[StrategyFacts], list[InvalidationHit]] | None = None,
    skip_early_only: bool = False,
) -> tuple[InvalidationHit, ...]:
    """扫描全部事件与规则，返回失效命中。

    ``skip_early_only=True`` 时跳过条件规则 —— 事件层用它做粗粒度标记
    （事件层看不到公司阶段，无法判断 ``early_only`` 的前提）。
    """
    hits: list[InvalidationHit] = []
    confirmed = has_confirmed_improvement(facts)

    for event in facts.events:
        for definition in rules_for(thesis_type):
            if getattr(definition, "early_only", False):
                if skip_early_only or confirmed:
                    continue
            ok, reason = match_rule(event, definition)
            if ok:
                hits.append(InvalidationHit(
                    event_id=event.id,
                    event_type=event.event_type,
                    severity=definition.severity.value,
                    rule_description=definition.description,
                    reason=reason,
                ))

    if extra_hits is not None:
        hits.extend(extra_hits(facts))
    return tuple(hits)


def is_terminal(hits: tuple[InvalidationHit, ...]) -> bool:
    return any(h.severity == InvalidationSeverity.TERMINAL.value for h in hits)


def warning_hits(hits: tuple[InvalidationHit, ...]) -> tuple[InvalidationHit, ...]:
    """仅「预警」级别的命中 —— 只提醒，不改状态。

    规格 §23 的 severity 分级正为此存在：监管关注该提醒，但不等于是失败。
    """
    return tuple(h for h in hits if h.severity == InvalidationSeverity.WARNING.value)


def should_invalidate(hits: tuple[InvalidationHit, ...]) -> bool:
    """只有 ``terminal`` / ``severe`` 触发状态迁移。

    规格 §20：出现事件**直接破坏**原投资逻辑时才进入失效。
    """
    return any(
        h.severity in (InvalidationSeverity.TERMINAL.value, InvalidationSeverity.SEVERE.value)
        for h in hits
    )


def mark_invalidating(
    thesis_type: ThesisType | str, event_type, title: str
) -> bool:
    """给定 `(事件类型, 标题)`，是否构成本策略的失效信号。

    这是**事件层**的粗粒度标记入口（展示用），不依赖公司阶段，
    因此跳过 ``early_only`` 规则与财务侧判定。
    """
    from app.facts import EventFact as _EventFact

    probe = _EventFact(id=0, event_type=event_type, title=title)
    for definition in rules_for(thesis_type):
        if getattr(definition, "early_only", False):
            continue
        ok, _ = match_rule(probe, definition)
        if ok:
            return True
    return False


__all__ = [
    "CONFIRMED_IMPROVEMENT_KEYWORDS",
    "detect",
    "has_confirmed_improvement",
    "is_terminal",
    "mark_invalidating",
    "rules_for",
    "should_invalidate",
    "warning_hits",
]
