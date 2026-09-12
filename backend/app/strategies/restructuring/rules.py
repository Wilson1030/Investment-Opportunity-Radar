"""``restructuring`` 策略的核心条件判定与催化剂阶梯（docs/06 §3）。

**反例警示（规格 §5.6 示例 A / J）—— 直接体现在下面的实现里**

1. ``is_st`` **只**作为 C4「经营困境背景」的部分证据（且 C4 权重仅 0.20、
   单独命中只给 0.35 满足度）。不存在「``is_st=true`` → 命中重组策略」的直接规则（INV-C1）。
2. 支持事件类型覆盖**非 ST 公司**的资产注入与产业整合。
3. C1 的满足度由**证据等级**决定（A 级 1.0 / B 级 0.85 / 媒体 0.5），
   因此「有传闻无公告」拿不到高分。
"""

from __future__ import annotations

from app.engine import classifier
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

#: **C1 的输入**：只认「重大资产重组 / 破产重整」这一受《重组管理办法》约束的类别。
#: M&A（非重大收购）不进 C1 —— 那属于 ma_integration 策略（P10 第 4 位）。
_DEAL_EVENTS = (
    EventType.RESTRUCTURING,
    EventType.BANKRUPTCY_REORGANIZATION,
)

#: **催化剂阶梯的输入**：比 C1 宽 —— 收购意向、协议转让等早期苗头也要能定阶段，
#: 否则会出现「有 M&A 事件却显示『无重组类事件』」这种自相矛盾的标签。
_LADDER_EVENTS = (
    EventType.RESTRUCTURING,
    EventType.BANKRUPTCY_REORGANIZATION,
    EventType.M_AND_A,
    EventType.ASSET_INJECTION,
    EventType.CONTROL_CHANGE,
)

#: 催化剂阶梯（关键词 → 阶段分）。
#:
#: ★ **早期阶段是刻意保留的**：用户明确要求「还不太确定但有苗头」的也要找，
#: 以便提前布局（预重整 / 债权人申请 / 法院受理 / 筹划停牌 / 意向协议）。
#: 低分表示「离价值兑现远、确定性低」，不表示不重要 ——
#: 因此这类机会必须由 ``early_signal()`` 标出来，卡片上显式提示。
_LADDER: tuple[tuple[str, float, bool, tuple[str, ...]], ...] = (
    # (阶段名, 分数, 是否早期, 关键词)
    # 存量（召回保留，但不冒充新催化）—— 分数低但**不是早期**
    ("存量｜重组已完成（限售解禁 / 后续手续）", 5.0, False,
     ("限售股", "限售股份", "解除限售", "上市流通", "限售期", "持续督导")),
    # ---- 早期苗头：用户明确要求「还不太确定但有苗头的也要找」 ----
    ("早期｜筹划 / 停牌 / 意向协议", 10.0, True,
     ("筹划", "停牌", "意向协议", "意向书", "投资意向", "拟筹划")),
    ("早期｜预重整 / 重整申请", 15.0, True,
     ("预重整", "重整申请", "申请重整", "破产申请", "债权人申请")),
    ("早期｜法院受理 / 指定管理人", 28.0, True,
     ("法院受理", "裁定受理", "受理重整", "指定管理人", "重整程序")),
    # ---- 进展 ----
    ("进展｜预案披露", 40.0, False, ("预案",)),
    ("进展｜草案 + 评估", 60.0, False, ("报告书", "草案", "资产评估", "评估结果")),
    ("进展｜获批复 / 审核通过", 72.0, False,
     ("批复", "审核通过", "无条件通过", "审核意见")),
    ("进展｜股东大会通过", 80.0, False, ("股东大会决议", "股东大会通过")),
    # ---- 完成 ----
    ("完成｜监管核准 / 实施完成", 95.0, False,
     ("核准", "过户完成", "实施完成", "完成过户", "注册生效")),
)

# 注意：这里**不再**维护失效关键词表。催化强度归零的唯一依据是
# invalidation.detect()（registry 里的失效规则），避免两份关键词漂移。

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


def _evidence_of(events: tuple[EventFact, ...]) -> tuple[int, ...]:
    """★ 展开事件所绑定的**证据 ID**（去重保序）。

    踩过的坑：这里原来写的是 tuple(e.id for e in events) ——
    EventFact.id 是**事件 ID**，不是证据 ID。两张表各自自增、数值范围重叠，
    于是 Opportunity.supporting_evidence_ids 指到了**别的公司**的公告，
    前端「查看证据 / 跳转原文」逐家错位一格（用户反馈的正是这个）。
    """
    seen: list[int] = []
    for event in events:
        for evidence_id in event.evidence_ids:
            if evidence_id not in seen:
                seen.append(evidence_id)
    return tuple(seen)


def _own_subject_events(events: tuple[EventFact, ...]) -> tuple[EventFact, ...]:
    """只保留**主体是上市公司本身**的事件。

    ★ 实测「重整」抽样 15 条里 7 条（47%）是子公司 / 孙公司 / 控股股东 /
    前控股股东的重整 —— 那不是母公司的重组预期。
    不区分会让 C1 与催化剂阶梯被第三方事件灌满。
    """
    return tuple(e for e in events if not classifier.subject_is_third_party(e.title))


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 出现重大资产重组相关公告（A 类）。

    只认**主体是本公司**的公告：子公司重整 ≠ 母公司重组预期。
    """
    events = _own_subject_events(facts.events_of(*_DEAL_EVENTS))
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
        _evidence_of(events),
    )


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 存在控制权 / 实际控制人变化。"""
    definition = _DEAL_C2
    events = facts.events_of(EventType.CONTROL_CHANGE)
    if events:
        return ConditionResult(definition.key, definition.label, definition.weight, 1.0,
                               "披露控制权 / 实际控制人变更", _evidence_of(events))
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
                               "存在资产注入 / 置换迹象", _evidence_of(events))
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

    def is_early_signal(self, facts: StrategyFacts) -> bool:
        """当前阶段是否属于「早期苗头」（催化强度 ≤ early_stage_max_score）。

        ★ 必须在卡片上显式标注：用户要的是「提前布局」，
        但把苗头当确定的事会误导决策（规格 §24 / §38）。
        """
        return self.catalyst_strength(facts).early

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:
        # 主体错位的事件不参与阶梯：子公司重整不该给母公司定阶段
        events = _own_subject_events(facts.events_of(*_LADDER_EVENTS))
        if not events:
            return CatalystStage("无重组 / 重整类事件", 0.0, "未发现重组、重整或收购类公告")

        # ★ 终止 / 失败优先判定：**直接问失效引擎**，不再维护第二份关键词表。
        #
        # 踩过的坑：原来这里有一份独立的 _TERMINATED 关键词，与 registry 的
        # 失效规则各写一份，结果「法院不予受理重整申请」被判为失效（应该终止）
        # 却仍然拿着 15 分的催化强度 —— 两张表漂移了。
        # 现在只认一个权威来源：invalidation.detect()。
        hits = invalidation.detect(facts)
        if invalidation.should_invalidate(hits):
            return CatalystStage(
                "终止 / 失败", 0.0,
                f"命中失效条件：{hits[0].rule_description}",
                early=False,
            )

        best: CatalystStage | None = None
        for stage, score, early, keywords in _LADDER:
            for event in events:
                if any(kw in event.title for kw in keywords):
                    candidate = CatalystStage(stage, score, f"依据：{event.title}", early=early)
                    if best is None or candidate.score > best.score:
                        best = candidate
        if best is not None:
            return best
        # 无法识别阶段时按**偏低**处理（20 分，落在早期区间）：
        # 不能因为「不知道进展」就默认它已经推进得很深。
        return CatalystStage(
            "早期｜阶段未知（存在重组 / 重整类公告，未识别到阶段关键词）", 20.0,
            "保守处理：无法确认进展时不计入后期阶段",
            early=True,
        )

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
