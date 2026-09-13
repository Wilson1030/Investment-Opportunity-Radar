"""策略 7 · ``cycle`` 行业周期反转（docs/06 §9）。

与 ``turnaround``（公司自身经营反转）的区别：
``turnaround`` 看的是**公司**的财务拐点；``cycle`` 看的是**行业**的供需拐点
（供给收缩 → 价格反弹 → 产能利用率提升）。

★ 诚实边界（这条策略的边界最大，必须写清楚）：
C2 供给收缩、C3 产品价格、C4 产能利用率**都需要行业数据**。
当前数据源只有公司公告与财务，所以：

  · 这些条件的证据只能来自**公告自述**（「公司公告称产品涨价」）
  · 拿不到行业数据时**不给假装中性的分数**，而是明说「行业数据未采集」
  · C1（过去长期下行）与 C5（成本优势）用公司自己的财务数据判 —— 这两条能真判

这样做的结果：cycle 在只有公告数据时覆盖率天然偏低。
**这是对的** —— 一个行业周期判断本来就不该由一家公司的公告单独支撑。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.markers import marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.CYCLE).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_CYCLE_EVENTS = (EventType.OTHER, EventType.MAJOR_CONTRACT, EventType.EARNINGS_TURNAROUND)

_NO_INDUSTRY_DATA = "行业供需数据当前未采集，仅有公司公告自述"

#: 供给收缩的公告自述（C2）
_SUPPLY_MARKERS = ("减产", "停产", "限产", "去产能", "落后产能退出", "关停",
                   "检修", "供给收缩", "产能出清")
#: 产品价格反弹（C3）
_PRICE_UP_MARKERS = ("涨价", "提价", "价格上调", "价格反弹", "售价提升", "价格上涨")
#: 产能利用率提升（C4）
_UTILIZATION_MARKERS = ("开工率", "产能利用率", "满产", "达产", "负荷提升")


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 过去经历长期下行 —— 用**公司财务**判，这条能真判。"""
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                               f"财务期数不足（{fin.periods_with_data} 期），无法判断长期下行")
    signals = []
    if fin.revenue_declining_run_max >= 2:
        signals.append(f"营收曾连续 {fin.revenue_declining_run_max} 期下降")
    if fin.margin_declining_run_max >= 2:
        signals.append(f"毛利率曾连续 {fin.margin_declining_run_max} 期下降")
    if len(signals) >= 2:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 1.0, "；".join(signals))
    if len(signals) == 1:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.60, signals[0])
    return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                           "未见营收 / 毛利率的连续下行")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 最近出现供给收缩 —— 权重最高（0.25）。

    只有**公告自述**可依据（如「公司公告减产」），且必须说明这一点。
    """
    events = facts.events_of(*_CYCLE_EVENTS)
    return marker_condition(_C2, events, (
        (0.75, "公告提及减产 / 限产 / 去产能", _SUPPLY_MARKERS),
        (0.0, "未见供给收缩的公告证据；行业层面供给数据未采集", ()),
    ), note=_NO_INDUSTRY_DATA)


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 产品价格反弹。

    ★ 价格是行业变量，公司公告只能作为**间接**证据（公司自己的提价公告），
    而且公司提价 ≠ 行业价格反弹（可能是成本推动或个体定价权）。
    所以上限只给 0.70，并把区别写进 detail。
    """
    events = facts.events_of(*_CYCLE_EVENTS)
    hit = marker_condition(_C3, events, (
        (0.70, "公司公告提及提价 / 价格上调", _PRICE_UP_MARKERS),
        (0.0, "未见价格相关公告；行业价格数据未采集", ()),
    ), note="公司提价不等于行业价格反弹")
    return hit


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 行业产能利用率提升 —— 同样只有公告自述。"""
    events = facts.events_of(*_CYCLE_EVENTS)
    return marker_condition(_C4, events, (
        (0.60, "公告提及开工率 / 产能利用率提升", _UTILIZATION_MARKERS),
        (0.0, "未见产能利用率相关公告；行业开工率数据未采集", ()),
    ), note=_NO_INDUSTRY_DATA)


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 公司成本低于行业平均。

    ★ 没有行业平均成本数据，所以**无法直接比较**。
    能用的代理是「毛利率处于较高水平且未恶化」——
    这是「成本竞争力」的必要条件而非充分条件，必须写清楚。
    """
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                               "财务数据不足，无法评估成本水平")
    improving = fin.margin_improving_quarters >= 1
    if not fin.debt_ratio_rising and improving:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.60,
                               "毛利率改善且负债率未上升（代理指标；"
                               "行业平均成本数据未采集，无法直接比较）")
    if not fin.debt_ratio_rising:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.35,
                               "负债率未上升，但毛利率未见改善（成本优势未显现）")
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                           "资产负债率上升，成本 / 财务压力增加")


_SPEC = StrategySpec(
    code=ThesisType.CYCLE,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    ladder=(
        LadderRung("价格止跌", 30.0, False,
                   ("价格企稳", "价格止跌", "跌幅收窄", "触底"), _CYCLE_EVENTS),
        LadderRung("库存下降 + 开工率提升", 55.0, False,
                   ("库存下降", "去库存", "开工率提升", "产能利用率提升"), _CYCLE_EVENTS),
        LadderRung("价格反弹 + 行业盈利改善", 80.0, False,
                   _PRICE_UP_MARKERS, _CYCLE_EVENTS),
        LadderRung("持续多季验证", 95.0, False,
                   ("连续两个季度", "连续两季", "持续改善", "多季"), _CYCLE_EVENTS),
    ),
    ladder_event_types=_CYCLE_EVENTS,
    narrative=Narrative(
        subject="行业周期反转",
        trigger=lambda f: (
            f"公司财务显示{'曾连续下行' if f.financials.revenue_declining_run_max >= 2 else '下行证据有限'}"
            f"（营收最长连降 {f.financials.revenue_declining_run_max} 期）"
        ),
        support=lambda f: (
            "公告中出现供给收缩 / 价格 / 开工率相关的改善迹象"
            if any(
                kw in (e.title or "")
                for e in f.events_of(*_CYCLE_EVENTS)
                for kw in _SUPPLY_MARKERS + _PRICE_UP_MARKERS + _UTILIZATION_MARKERS
            )
            else "尚未看到供给 / 价格层面的改善迹象"
        ),
        caveat="供给收缩能否持续、需求端是否真恢复、行业价格反弹的持续性均尚无行业数据可验证",
    ),
    markers={
        "供给收缩是否可持续": ((_CYCLE_EVENTS, _SUPPLY_MARKERS)),
        "需求端是否真正恢复": ((_CYCLE_EVENTS, ("需求恢复", "订单", "销量增长"))),
        "产品价格反弹的持续性": ((_CYCLE_EVENTS, _PRICE_UP_MARKERS)),
        "行业新增产能计划": ((_CYCLE_EVENTS, ("扩产", "新增产能", "投产计划"))),
        "公司成本优势的来源与可持续性": ((_CYCLE_EVENTS, ("成本", "降本", "毛利率"))),
    },
    unverifiable={
        "供给收缩是否可持续": "需要行业产能与开工率数据，当前数据源只有公司公告",
        "需求端是否真正恢复": "需要行业需求侧数据（下游开工 / 订单指数），当前未接入",
        "产品价格反弹的持续性": "需要行业价格指数，当前未接入；公司提价公告不能替代",
        "行业新增产能计划": "需要行业在建产能数据，当前未接入",
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
