"""``restructuring`` 策略的核心条件判定与催化剂阶梯（docs/06 §3）。

**反例警示（规格 §5.6 示例 A / J）—— 直接体现在下面的实现里**

1. ``is_st`` **只**作为 C4「经营困境背景」的部分证据（且 C4 权重仅 0.20、
   单独命中只给 0.35 满足度）。不存在「``is_st=true`` → 命中重组策略」的直接规则（INV-C1）。
2. 支持事件类型覆盖**非 ST 公司**的资产注入与产业整合。
3. C1 的满足度由**证据等级**决定（A 级 1.0 / B 级 0.85 / 媒体 0.5），
   因此「有传闻无公告」拿不到高分。
"""

from __future__ import annotations

from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, ReliabilityLevel, ThesisType
from app.strategies.base import (
    CatalystStage,
    ConditionResult,
    InvalidationHit,
    StrategyEvaluation,
)
from app.strategies.registry import get_def
from app.strategies.restructuring import invalidation, questions, thesis

_LEVEL_RANK = "ABCDE"

#: 重组类公告的四个事件类型（C1 的输入）
_DEAL_EVENTS = (
    EventType.RESTRUCTURING,
    EventType.BANKRUPTCY_REORGANIZATION,
)

#: 催化剂阶梯（关键词 → 阶段分）。**顺序即优先级**，从高到低。
_LADDER: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("监管核准 / 实施完成", 95.0, ("核准", "过户完成", "实施完成", "完成过户", "注册生效")),
    ("股东大会通过", 80.0, ("股东大会决议", "股东大会通过")),
    ("草案 + 评估", 60.0, ("报告书", "草案", "资产评估")),
    ("预案披露", 40.0, ("预案",)),
    ("筹划 / 停牌", 20.0, ("停牌", "筹划")),
)

_TERMINATED = ("终止", "失败", "撤回", "撤销")

_CONDITIONS = get_def(ThesisType.RESTRUCTURING).core_conditions
_DEAL_C1, _DEAL_C2, _DEAL_C3, _DEAL_C4 = (
    _CONDITIONS[0], _CONDITIONS[1], _CONDITIONS[2], _CONDITIONS[3],
)


def _levels(events: tuple[EventFact, ...]) -> list[ReliabilityLevel]:
    return [e.evidence_level for e in events if e.evidence_level is not None]


def _best_level(events: tuple[EventFact, ...]) -> ReliabilityLevel | None:
    levels = _levels(events)
    if not levels:
        return None
    return min(levels, key=lambda lv: _LEVEL_RANK.index(lv.value))


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 出现重大资产重组相关公告（A 类）。"""
    events = facts.events_of(*_DEAL_EVENTS)
    definition = _DEAL_C1
    if not events:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.0,
                               "未发现重组类公告")

    best = _best_level(events)
    if best is ReliabilityLevel.A:
        satisfaction, detail = 1.00, "存在 A 类（正式公告）重组类公告"
    elif best is ReliabilityLevel.B:
        satisfaction, detail = 0.85, "存在 B 类（官方文件）重组类信息"
    elif best is not None:
        satisfaction, detail = 0.50, f"仅存在 {best.value} 类（媒体/观点/讨论）信息，未经证实"
    else:
        satisfaction, detail = 0.60, "存在重组类事件，但证据等级未知"
    return ConditionResult(
        definition.key, definition.label, definition.weight, satisfaction, detail,
        tuple(e.id for e in events),
    )


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 存在控制权 / 实际控制人变化。"""
    definition = _DEAL_C2
    events = facts.events_of(EventType.CONTROL_CHANGE)
    if events:
        return ConditionResult(definition.key, definition.label, definition.weight, 1.0,
                               "披露控制权 / 实际控制人变更", tuple(e.id for e in events))
    sh = facts.shareholder
    if sh.controlling_shareholder_changed or sh.actual_controller_changed:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.80,
                               "股东结构数据显示控制权已变化（无对应公告）")
    return ConditionResult(definition.key, definition.label, definition.weight, 0.0,
                           "未见控制权变化")


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 存在资产注入或资产置换迹象。"""
    definition = _DEAL_C3
    events = facts.events_of(EventType.ASSET_INJECTION)
    if events:
        return ConditionResult(definition.key, definition.label, definition.weight, 1.0,
                               "存在资产注入 / 置换迹象", tuple(e.id for e in events))
    return ConditionResult(definition.key, definition.label, definition.weight, 0.0,
                           "未见资产注入迹象")


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 经营困境背景。

    ★ ``is_st`` 在此**只是背景的一部分**：单独命中 ST 仅给 0.35 满足度，
    不可能凭 ST 标签获得高 coverage（规格 §5.6 示例 J）。
    """
    definition = _DEAL_C4
    is_st = facts.company.is_st
    loss_years = facts.financials.loss_years

    if is_st and loss_years >= 2:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.70,
                               f"ST 状态且连续 {loss_years} 年亏损")
    if loss_years >= 2:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.55,
                               f"连续 {loss_years} 年亏损（非 ST）")
    if is_st:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.35,
                               "ST 状态（无连续亏损数据支撑）")
    if loss_years == 1:
        return ConditionResult(definition.key, definition.label, definition.weight, 0.20,
                               "最近一年亏损")
    return ConditionResult(definition.key, definition.label, definition.weight, 0.0,
                           "未见经营困境迹象")


class RestructuringStrategy:
    """``restructuring`` 实现类（本轮唯一 ``status=implemented`` 的策略）。"""

    code = ThesisType.RESTRUCTURING
    display_name = "重组预期"

    # ---------- 核心条件 ----------

    def evaluate(self, facts: StrategyFacts) -> StrategyEvaluation:
        return StrategyEvaluation(
            conditions=(
                _condition_c1(facts),
                _condition_c2(facts),
                _condition_c3(facts),
                _condition_c4(facts),
            )
        )

    # ---------- 催化剂阶梯 ----------

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:
        events = facts.events_of(*_DEAL_EVENTS)
        if not events:
            return CatalystStage("无重组类事件", 0.0, "未发现重组类公告")

        # 终止 / 失败优先判定（阶梯归零 —— 此时失效检测会接管）
        for event in events:
            if any(kw in event.title for kw in _TERMINATED):
                return CatalystStage("终止 / 失败", 0.0, f"事件标题：{event.title}")

        best: CatalystStage | None = None
        for stage, score, keywords in _LADDER:
            for event in events:
                if any(kw in event.title for kw in keywords):
                    candidate = CatalystStage(stage, score, f"依据：{event.title}")
                    if best is None or candidate.score > best.score:
                        best = candidate
        if best is not None:
            return best
        return CatalystStage("重组类公告（阶段未知）", 20.0, "存在重组类公告但未识别到阶段关键词")

    # ---------- 失效 / 待确认 / 叙事 ----------

    def invalidation_hits(self, facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
        return invalidation.detect(facts)

    def build_statement(self, facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
        return thesis.build_statement(facts, evaluation)

    def open_questions(self, facts: StrategyFacts) -> tuple[str, ...]:
        return questions.open_only(facts)

    def why_now(self, facts: StrategyFacts) -> dict[str, str]:
        return thesis.why_now(facts)


STRATEGY = RestructuringStrategy()

__all__ = [
    "RestructuringStrategy",
    "STRATEGY",
    "_condition_c1",
    "_condition_c2",
    "_condition_c3",
    "_condition_c4",
]
