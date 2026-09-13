"""按标题标记分级的条件求值 —— 8 类策略里最常见的条件形状。

例：``product`` 的 C4「签署商业订单」——

    有「订单 / 合同 / 中标」的公告 → 1.00
    只有「框架协议 / 意向」        → 0.45
    都没有                          → 0.00

三档写成一个 ``tiers`` 列表即可，不必每个策略重复一遍「按标记找事件 /
取最优证据等级 / 收集证据 ID」的样板代码。

★ ``note`` 参数不是为了好看：本项目有多条条件的**数据源不存在**
（行业景气、市场份额、研发投入、估值区间…）。
那种情况下必须把「我们没有这个数据」写进 ``detail``，
否则用户会以为 0 分代表「查过了，是没有」，而不是「查不到」。
"""

from __future__ import annotations

from app.facts import EventFact
from app.strategies.base import ConditionResult
from app.strategies.common.evidence import (
    best_level,
    evidence_ids_of,
    satisfaction_for_level,
)

#: ``(满足度, 说明, 标记集合)`` —— 按顺序取第一个命中的档位
Tier = tuple[float, str, tuple[str, ...]]


def title_hits(events: tuple[EventFact, ...], markers: tuple[str, ...]) -> tuple[EventFact, ...]:
    """标题命中任一标记的事件。"""
    return tuple(
        e for e in events if any(m in (e.title or "") for m in markers)
    )


def marker_condition(
    definition,
    events: tuple[EventFact, ...],
    tiers: tuple[Tier, ...],
    *,
    note: str = "",
    level_weighted: bool = False,
) -> ConditionResult:
    """按 ``tiers`` 顺序求值，返回条件结果。

    ``level_weighted=True`` 时，命中档位的满足度还会**按证据等级打折**
    （只有 A 类拿满分）。用于那些「公告质量直接决定可信度」的条件 ——
    例：`event_driven` 的「存在明确的单一事件」，
    一条媒体传闻与一份正式公告绝不该同分。
    """
    suffix = f"（{note}）" if note else ""
    for satisfaction, detail, markers in tiers:
        # ★ 标记为空 = **兜底档**（不筛选事件，无条件命中）。
        #   踩过两次坑：
        #   1) 空标记被当成「不匹配 → 跳过」，于是所有
        #      「未披露 / 无法评估」的兜底档全部失效，分数被静默压低到 0 ——
        #      而 0 的含义是「查过了、没有」，不是「查不到」。
        #   2) 修好 1) 之后又漏了一种情形：**完全没有事件时兜底也不触发**
        #      （``matched`` 是空元组，仍然被 "不匹配" 挡掉）。
        #      但「没有公告」恰恰是最需要走兜底的情形。
        is_fallback = not markers
        matched = events if is_fallback else title_hits(events, markers)
        if satisfaction == 0:
            # 零分档：不检查标记，直接返回
            return ConditionResult(
                definition.key, definition.label, definition.weight, 0.0, detail + suffix,
            )
        if not is_fallback and not matched:
            continue
        if level_weighted and not is_fallback:
            level = best_level(matched)
            scale, level_note = satisfaction_for_level(level)
            return ConditionResult(
                definition.key, definition.label, definition.weight,
                round(satisfaction * scale, 4),
                f"{detail}（{level_note}）{suffix}",
                evidence_ids_of(matched),
            )
        return ConditionResult(
            definition.key, definition.label, definition.weight, satisfaction,
            detail + suffix, evidence_ids_of(matched),
        )
    return ConditionResult(
        definition.key, definition.label, definition.weight, 0.0, "未见相关证据" + suffix,
    )


def event_type_condition(
    definition,
    events: tuple[EventFact, ...],
    *,
    full_if_any: bool = True,
    empty_detail: str = "未见相关事件",
) -> ConditionResult:
    """「存在某类事件即命中」的条件（按证据等级定满足度）。"""
    if not events:
        return ConditionResult(
            definition.key, definition.label, definition.weight, 0.0, empty_detail,
        )
    level = best_level(events)
    satisfaction, level_note = satisfaction_for_level(level)
    return ConditionResult(
        definition.key, definition.label, definition.weight,
        satisfaction if full_if_any else satisfaction * 0.85,
        f"存在 {len(events)} 条相关公告（{level_note}）",
        evidence_ids_of(events),
    )


def financial_condition(
    definition,
    satisfied: bool,
    *,
    yes_detail: str,
    no_detail: str,
    yes: float = 1.0,
    no: float = 0.0,
    unknown: bool = False,
    unknown_detail: str = "财务数据不足，无法判断",
) -> ConditionResult:
    """二值条件（带「数据不足」的第三态）。

    ★ 第三态必须存在：``unknown=True`` 时返回 0 但**说明是数据不足**，
    与「查过了、不满足」区分开。把「不知道」当成「没有」是虚假结论。
    """
    if unknown:
        return ConditionResult(
            definition.key, definition.label, definition.weight, 0.0, unknown_detail,
        )
    return ConditionResult(
        definition.key, definition.label, definition.weight,
        yes if satisfied else no, yes_detail if satisfied else no_detail,
    )


__all__ = ["Tier", "event_type_condition", "financial_condition", "marker_condition",
           "title_hits"]
