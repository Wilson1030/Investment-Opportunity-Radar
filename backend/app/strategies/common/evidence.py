"""策略层共用的证据处理。

三件事，每件都对应一次踩过的坑：

  · ``satisfaction_for_level`` —— 证据等级 → 满足度。
    不能让 D/E 类（媒体 / 讨论）拿高分：「可能筹划」不能等同于 A 类公告。
  · ``evidence_ids_of`` —— 收集可追溯的证据 ID（规格承诺「每个判断可追溯」）。
  · ``own_subject_events`` —— **主体过滤**。这是本项目最有价值的一条共享判定：
    实测「重整」抽样 15 条里 7 条（47%）讲的是子公司 / 控股股东，
    不是上市公司自己。不排除就会持续制造幻影机会。
"""

from __future__ import annotations

from app.engine import classifier
from app.facts import EventFact
from app.models.enums import ReliabilityLevel

#: 证据等级 → 满足度。**只对 A 类给满分**。
_LEVEL_SATISFACTION: dict[ReliabilityLevel, tuple[float, str]] = {
    ReliabilityLevel.A: (1.00, "A 类（正式公告）"),
    ReliabilityLevel.B: (0.85, "B 类（官方文件）"),
    ReliabilityLevel.C: (0.50, "C 类（媒体报道，未经证实）"),
    ReliabilityLevel.D: (0.30, "D 类（观点 / 分析）"),
    ReliabilityLevel.E: (0.15, "E 类（讨论 / 传闻）"),
}

#: 证据等级从强到弱（用于取最优）
_ORDER: tuple[ReliabilityLevel, ...] = (
    ReliabilityLevel.A,
    ReliabilityLevel.B,
    ReliabilityLevel.C,
    ReliabilityLevel.D,
    ReliabilityLevel.E,
)


def satisfaction_for_level(level: ReliabilityLevel | None) -> tuple[float, str]:
    """证据等级 → ``(满足度, 说明)``。等级未知时给一个**保守偏低**的值。"""
    if level is None:
        return 0.60, "证据等级未知"
    return _LEVEL_SATISFACTION.get(level, (0.60, "证据等级未知"))


def best_level(events: tuple[EventFact, ...]) -> ReliabilityLevel | None:
    """一组事件里最强的证据等级。"""
    levels = {e.evidence_level for e in events if e.evidence_level is not None}
    for level in _ORDER:
        if level in levels:
            return level
    return None


def evidence_ids_of(events: tuple[EventFact, ...]) -> tuple[int, ...]:
    """按出现顺序去重收集证据 ID。"""
    seen: list[int] = []
    for event in events:
        for evidence_id in event.evidence_ids:
            if evidence_id not in seen:
                seen.append(evidence_id)
    return tuple(seen)


def own_subject_events(events: tuple[EventFact, ...]) -> tuple[EventFact, ...]:
    """只保留**主体是上市公司本身**的事件。

    ★ 为什么这是共享层而不是各策略自己写：这条判定一旦有策略漏掉，
    它就会把子公司的重整 / 控股股东的被诉当成母公司的机会 ——
    而且是**静默**的（分数看着正常，只是讲的是别人家的事）。
    """
    return tuple(e for e in events if not classifier.subject_is_third_party(e.title))


def relevant_events(
    facts, event_types, *, own_subject: bool = True
) -> tuple[EventFact, ...]:
    """取指定类型的事件，默认做主体过滤。"""
    events = facts.events_of(*event_types)
    return own_subject_events(events) if own_subject else events


__all__ = [
    "best_level",
    "evidence_ids_of",
    "own_subject_events",
    "relevant_events",
    "satisfaction_for_level",
]
