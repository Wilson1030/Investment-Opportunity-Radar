"""策略 8 · ``growth`` 成长（docs/06 §10）。

> 寻找行业高速增长、公司收入和订单持续增长，同时具有新业务扩张能力的公司。

★ 与 ``cycle`` / ``turnaround`` 的边界：这三者的条件很像，但**时点不同**：

  ``turnaround``  过去差 → 现在改善（拐点）
  ``cycle``       行业供需拐点（行业变量主导）
  ``growth``      一直在增长，且**增长可持续**（新订单 / 新产能 / 份额）

所以 growth 的条件里没有「过去恶化」，而是要求**
收入连续多季增长 + 新订单 + 新产能**三条同时成立 —— 
只有「这一季涨了」不叫成长股。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.markers import marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.GROWTH).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_GROWTH_EVENTS = (
    EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT, EventType.EARNINGS_TURNAROUND,
    EventType.M_AND_A, EventType.POLICY_CATALYST,
)

_NO_INDUSTRY_DATA = "行业增速数据未采集，仅有公司公告与财务"

#: 新订单增长（C3）
_ORDER_MARKERS = ("中标", "新签订单", "新增订单", "订单增长", "合同", "采购")
#: 新产能 / 新产品（C4）
_CAPACITY_MARKERS = ("投产", "达产", "量产", "扩产", "新增产能", "试生产", "新产品")
#: 份额提升（C5）
_SHARE_MARKERS = ("市占率", "市场份额", "份额提升", "渗透率", "行业第一", "龙头")


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 行业需求快速增长 —— 无行业数据，只能用公司侧间接证据。"""
    fin = facts.financials
    policy = facts.events_of(EventType.POLICY_CATALYST)
    revenue_growing = fin.periods_with_data >= 2 and fin.revenue_improving_quarters >= 2
    if revenue_growing and policy:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.70,
                               f"营收连续 {fin.revenue_improving_quarters} 期增长，"
                               f"且有政策类信息佐证需求（{_NO_INDUSTRY_DATA}）")
    if revenue_growing:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.50,
                               f"营收连续 {fin.revenue_improving_quarters} 期增长"
                               f"（{_NO_INDUSTRY_DATA}）")
    return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                           f"营收未见连续增长；{_NO_INDUSTRY_DATA}")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 收入连续多个季度增长 —— 用财务数据真判。"""
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0,
                               f"财务期数不足（{fin.periods_with_data} 期），无法判断连续增长")
    quarters = fin.revenue_improving_quarters
    if quarters >= 3:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 1.0,
                               f"营收连续 {quarters} 期同比增长")
    if quarters == 2:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.80,
                               "营收连续 2 期同比增长")
    if quarters == 1:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.35,
                               "营收单期同比增长（尚不构成「连续多个季度」）")
    return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0, "营收未见同比增长")


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 新订单增长。"""
    events = facts.events_of(*_GROWTH_EVENTS)
    return marker_condition(_C3, events, (
        (1.00, "存在中标 / 新签订单类公告", _ORDER_MARKERS),
        (0.0, "未见新订单类公告", ()),
    ))


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 新产能投产 / 新产品开始贡献收入。"""
    events = facts.events_of(*_GROWTH_EVENTS)
    return marker_condition(_C4, events, (
        (1.00, "存在投产 / 达产 / 新产品类公告", _CAPACITY_MARKERS),
        (0.0, "未见新产能 / 新产品类公告", ()),
    ))


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 市场份额提升 —— 无第三方份额数据。"""
    events = facts.events_of(*_GROWTH_EVENTS)
    hit = marker_condition(_C5, events, (
        (0.65, "公告提及市占率 / 份额提升", _SHARE_MARKERS),
        (0.0, "未见份额相关公告；第三方份额数据未采集", ()),
    ), note="第三方市占率数据未采集，仅有公司自述")
    return hit


_SPEC = StrategySpec(
    code=ThesisType.GROWTH,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    ladder=(
        LadderRung("单季增长", 40.0, False, ("同比增长", "增长"), _GROWTH_EVENTS),
        LadderRung("连续多季增长", 70.0, False,
                   ("连续增长", "连续两个季度", "连续两季", "持续增长"), _GROWTH_EVENTS),
        LadderRung("订单 + 产能 + 新品齐备", 80.0, False,
                   _ORDER_MARKERS + _CAPACITY_MARKERS, _GROWTH_EVENTS),
        LadderRung("份额提升被确认", 95.0, False, _SHARE_MARKERS, _GROWTH_EVENTS),
    ),
    ladder_event_types=_GROWTH_EVENTS,
    narrative=Narrative(
        subject="成长",
        trigger=lambda f: (
            f"公司营收已连续 {f.financials.revenue_improving_quarters} 期同比增长"
            if f.financials.revenue_improving_quarters >= 1
            else "公司营收暂未出现连续增长"
        ),
        support=lambda f: (
            "同时出现订单 / 产能 / 新产品类公告，扩张有落地证据"
            if any(
                kw in (e.title or "")
                for e in f.events_of(*_GROWTH_EVENTS)
                for kw in _ORDER_MARKERS + _CAPACITY_MARKERS
            )
            else "尚未看到订单或产能层面的扩张证据"
        ),
        caveat="行业增长的持续性、订单转化为收入的比例、新产能利用率与份额数据来源均待确认",
    ),
    markers={
        "行业增长的持续性": ((EventType.POLICY_CATALYST, ("增长", "需求", "规划"))),
        "订单转化为收入的比例": ((EventType.MAJOR_CONTRACT, ("收入", "确认", "交付", "验收"))),
        "新产能的产能利用率": ((EventType.NEW_PRODUCT, _CAPACITY_MARKERS)),
        "新产品收入占比": ((EventType.NEW_PRODUCT, EventType.EARNINGS_TURNAROUND),
                           ("收入占比", "贡献收入", "放量")),
        "客户集中度与份额数据的来源": ((EventType.MAJOR_CONTRACT, ("客户", "集中度", "份额"))),
    },
    unverifiable={
        "客户集中度与份额数据的来源": (
            "需要第三方市占率 / 客户集中度数据，当前数据源只有公司公告"
        ),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
