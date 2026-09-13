"""``restructuring`` 策略的失效条件判定（规格 §23 / M5-03）。

**这是「逻辑失效」状态迁移的唯一依据。** 判定全部由规则完成，
LLM 只负责产出事件，不得直接改状态（docs/03 §2.2）。
"""

from __future__ import annotations

from app.engine.classifier import is_completion_driven_delisting
from app.strategies.common.matcher import match_rule
from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, InvalidationSeverity, ThesisType
from app.strategies.base import InvalidationHit
from app.strategies.registry import STRATEGIES


def rules() -> tuple:
    """本策略的失效条件定义（来自注册表，单一事实来源）。"""
    return STRATEGIES[ThesisType.RESTRUCTURING].invalidating_events


#: 「已进入实质推进」的证据词 —— 出现它们说明不是早期苗头
_PROGRESS_KEYWORDS = (
    "预案", "报告书", "草案", "批复", "股东大会", "核准", "过户",
    "审核通过", "无条件通过",
)


def has_progress_evidence(facts: StrategyFacts) -> bool:
    """是否已经出现「进入实质推进」的证据。

    用于 ``early_only`` 规则：对已经推进到预案/草案的机会，
    收到问询函是常规流程，不该报警；对刚起步的苗头，监管关注往往是终止前兆。
    """
    return any(
        any(kw in (event.title or "") for kw in _PROGRESS_KEYWORDS)
        for event in facts.events
    )


def _matches(event: EventFact, definition) -> tuple[bool, str]:
    """单条规则匹配 —— **委托共享实现**（``common.matcher.match_rule``）。

    ★ 为什么这里不再自己写一份：本项目已经因为「同一个判定有两份实现」
    出过两次事故（催化阶梯 vs 失效规则的关键词漂移；
    主体判定在正向信号与失效判定之间不对称）。
    所以规则匹配只保留一个执行点。
    """
    return match_rule(event, definition)


def detect(facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
    hits: list[InvalidationHit] = []
    early = not has_progress_evidence(facts)
    for event in facts.events:
        for definition in rules():
            # early_only 规则只对「尚无进展证据」的机会生效
            if getattr(definition, "early_only", False) and not early:
                continue
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


def warning_hits(hits: tuple[InvalidationHit, ...]) -> tuple[InvalidationHit, ...]:
    """仅「预警」级别的命中 —— 只提醒，不改状态。

    ★ 早期待确认阶段收到监管关注时应该提醒，但不该判死：
    问询函不等于交易失败（规格 §23 的 severity 分级正为此存在）。
    """
    return tuple(h for h in hits if h.severity == InvalidationSeverity.WARNING.value)


def should_invalidate(hits: tuple[InvalidationHit, ...]) -> bool:
    """是否应把机会迁移到 ``invalidated``。

    规格 §20：出现事件**直接破坏**原投资逻辑时进入失效。
    因此只有 ``terminal`` 与 ``severe`` 触发迁移，``warning`` 只记 Alert。
    """
    return any(
        h.severity in (InvalidationSeverity.TERMINAL.value, InvalidationSeverity.SEVERE.value)
        for h in hits
    )


__all__ = ["detect", "has_progress_evidence", "is_completion_driven_delisting",
           "is_terminal", "rules", "should_invalidate", "warning_hits"]
