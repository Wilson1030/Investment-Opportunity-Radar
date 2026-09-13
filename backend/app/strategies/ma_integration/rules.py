"""策略 4 · ``ma_integration`` 并购 / 产业整合（docs/06 §6）。

验收闸门（docs/06 §16）：**验证「催化剂 + 交易质量 + 风险」三维评分**。

这条闸门要的是：并购策略的分数不能只看「有没有并购公告」——
交易质量（价格 / 盈利能力 / 资产质量）必须独立成条件，
否则会变成「公告数量排序」。

★ 诚实边界（写进 detail，不藏）：
  · C2 业务协同、C4 行业集中度都需要**行业数据**，当前数据源没有
  · 只能从公告措辞里找间接证据，找不到就明说「无法从公告判定」
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.evidence import (
    best_level, own_subject_events, satisfaction_for_level,
)
from app.strategies.common.markers import marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.MA_INTEGRATION).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_MA_EVENTS = (EventType.M_AND_A, EventType.ASSET_INJECTION, EventType.CONTROL_CHANGE)

#: 交易质量可评估的证据（C3）
_QUALITY_MARKERS = ("作价", "对价", "评估值", "评估结果", "审计", "盈利预测",
                    "业绩承诺", "溢价率", "交易价格", "净资产")
#: 业务协同的间接证据（C2）
_SYNERGY_MARKERS = ("同业", "产业链", "上下游", "协同", "整合", "横向", "纵向",
                    "同一控制", "同行业")
#: 「已进入上市公司体系」的证据（C5）
_INCLUDED_MARKERS = ("交割", "过户完成", "完成过户", "纳入合并范围", "并表",
                     "完成工商变更", "实施完毕", "股权登记")
#: 仅停留在意向层面（C5 的反面）
_INTENT_ONLY_MARKERS = ("意向协议", "意向书", "框架协议", "拟收购", "筹划", "备忘录")


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 发生并购 / 收购事件（A 类公告）。"""
    events = own_subject_events(facts.events_of(*_MA_EVENTS))
    if not events:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0, "未见并购 / 收购类公告")
    level = best_level(events)
    scale, level_note = satisfaction_for_level(level)
    return ConditionResult(_C1.key, _C1.label, _C1.weight, round(scale, 4),
                           f"存在 {len(events)} 条并购类公告（{level_note}）")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 标的与上市公司存在业务协同。

    ★ 当前数据源没有标的所属行业的数据，所以只能从公告措辞与
    公司已有的 ``industry_chain`` 里找间接证据。找不到就明说。
    """
    events = own_subject_events(facts.events_of(*_MA_EVENTS))
    chain = tuple(facts.company.industry_chain or ())
    hit_chain = tuple(
        e for e in events
        if any(seg and seg in (e.title or "") for seg in chain)
    )
    if hit_chain:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.85,
                               "公告中出现公司产业链环节关键词",
                               tuple(i for e in hit_chain for i in e.evidence_ids))
    return marker_condition(_C2, events, (
        (0.70, "公告措辞显示与现有业务存在协同（同业 / 产业链 / 整合）",
         _SYNERGY_MARKERS),
        (0.20, "协同性无法从公告判定（标的所属行业数据当前未采集）", ()),
    ), note="标的行业数据未采集")


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 交易质量可评估（价格 / 盈利能力 / 资产质量可获取）—— 权重 0.25。

    ★ 这是本策略区别于「有公告就打分」的关键条件：
    没有作价 / 评估 / 业绩承诺的公告，交易质量就是**不可评估**的，
    分数必须低 —— 否则策略退化成「公告数量排序」。
    """
    events = own_subject_events(facts.events_of(*_MA_EVENTS))
    return marker_condition(_C3, events, (
        (1.00, "公告披露了作价 / 评估 / 业绩承诺等可评估信息", _QUALITY_MARKERS),
        (0.15, "仅有意向或框架性表述，交易质量不可评估", ()),
    ))


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 行业集中度正在提升。

    ★ **当前数据源无法判定**（没有行业数据）—— 明确写出来，
    而不是给一个看起来像判断的分数。
    """
    events = own_subject_events(facts.events_of(*_MA_EVENTS))
    hit = marker_condition(_C4, events, (
        (0.55, "公告提及行业整合 / 集中度", ("行业整合", "集中度", "出清", "兼并")),
        (0.0, "行业集中度数据当前未采集，无法判定", ()),
    ))
    return hit


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 新增业务确实进入上市公司体系（非仅意向）。"""
    events = own_subject_events(facts.events_of(*_MA_EVENTS))
    included = marker_condition(_C5, events, ((1.0, "", _INCLUDED_MARKERS), (0.0, "", ())))
    intent = marker_condition(_C5, events, ((1.0, "", _INTENT_ONLY_MARKERS), (0.0, "", ())))
    if included.satisfaction > 0:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 1.0,
                               "已交割 / 过户 / 纳入合并范围", included.evidence_ids)
    if intent.satisfaction > 0:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.20,
                               "仍停留在意向 / 框架协议阶段", intent.evidence_ids)
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.45,
                           "有并购公告，但未看到标的进入合并体系的证据")


_SPEC = StrategySpec(
    code=ThesisType.MA_INTEGRATION,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    ladder=(
        LadderRung("意向协议", 30.0, False, ("意向协议", "意向书", "框架协议", "备忘录")),
        LadderRung("正式方案", 55.0, False, ("预案", "报告书", "正式方案", "作价", "评估结果")),
        LadderRung("过会 / 交割", 80.0, False,
                   ("审核通过", "无条件通过", "核准", "交割", "过户", "注册生效")),
        LadderRung("整合见效（需 ≥1 年）", 95.0, False,
                   ("并表", "纳入合并", "整合完成", "协同效应", "业绩贡献")),
    ),
    ladder_event_types=_MA_EVENTS,
    narrative=Narrative(
        subject="并购 / 产业整合",
        trigger=lambda f: (
            f"公司披露 {len(own_subject_events(f.events_of(*_MA_EVENTS)))} 条"
            f"并购 / 收购类公告"
        ),
        support=lambda f: (
            "交易已披露作价与评估信息，质量可评估"
            if any(
                kw in (e.title or "")
                for e in own_subject_events(f.events_of(*_MA_EVENTS))
                for kw in _QUALITY_MARKERS
            )
            else "交易仍处于方案披露阶段，质量信息有限"
        ),
        caveat="收购价格是否合理、商誉风险、业绩承诺可达成性与整合难度尚待确认",
    ),
    markers={
        "收购价格": ((_MA_EVENTS, ("作价", "对价", "价格", "评估值"))),
        "商誉风险": ((_MA_EVENTS, ("商誉", "溢价"))),
        "业绩承诺条款": ((_MA_EVENTS, ("业绩承诺", "补偿", "对赌"))),
        "并购标的盈利能力": ((_MA_EVENTS, ("净利润", "盈利", "营业收入"))),
        "标的资产质量": ((_MA_EVENTS, ("资产质量", "审计", "评估"))),
        "融资方式": ((_MA_EVENTS, ("发行股份", "募集配套资金", "自有资金", "并购贷款"))),
        "整合难度": ((_MA_EVENTS, ("整合", "管理层", "人员"))),
        "上市公司历史并购表现": ((_MA_EVENTS, ("前次", "历史", "过往"))),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
