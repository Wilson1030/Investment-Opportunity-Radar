"""策略 5 · ``shareholder_action`` 股东行为（docs/06 §7）。

> 关注公司管理层或主要股东用**真金白银**表达信心的情况。

★ 本策略最容易犯的错：把「公告了增持计划」当成「已经增持」。
「拟增持不超过 2%」和「已增持 2%」是完全不同的事 ——
所以 C1 按**计划 / 实施**分档，阶梯也把两者分开。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.markers import financial_condition, marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.SHAREHOLDER_ACTION).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_BUY_EVENTS = (EventType.SHAREHOLDER_BUY, EventType.BUYBACK, EventType.MANAGEMENT_CHANGE)

#: 已实施 / 进行中的证据（与「仅是计划」区分）
#: 已实施 / 完成。
#:
#: ★ 「增持计划**实施完成**」是最常见的表述之一 ——
#: 只列「增持完成」会漏掉它，从而把一份已完成公告当成「仅计划」，
#: 分数与阶段双双偏低（实测踩到）。
_IMPLEMENTED_MARKERS = (
    "已增持", "增持完成", "完成增持", "累计增持", "已买入",
    "已回购", "回购完成", "累计回购", "首次回购",
    "实施进展", "实施完成", "实施完毕", "已完成实施",
)
#: 计划类证据
_PLAN_MARKERS = ("增持计划", "拟增持", "回购方案", "拟回购", "回购计划", "不低于")
#: 规模显著性的描述
_SCALE_MARKERS = ("不低于", "不超过", "亿元", "万元", "占", "比例", "%")
#: 资金来源（C3）
_FUNDING_MARKERS = ("自有资金", "自筹资金", "专项贷款", "回购贷款", "自有或自筹")


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 控股股东 / 管理层增持，或公司回购 —— 权重最高（0.30）。"""
    events = facts.events_of(*_BUY_EVENTS)
    sh = facts.shareholder
    if not events and not (sh.insider_buy or sh.buyback):
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                               "未见增持 / 回购类公告")

    implemented = marker_condition(_C1, events, ((1.0, "", _IMPLEMENTED_MARKERS), (0.0, "", ())))
    planned = marker_condition(_C1, events, ((1.0, "", _PLAN_MARKERS), (0.0, "", ())))

    if implemented.satisfaction > 0:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 1.0,
                               "增持 / 回购已在实施或完成", implemented.evidence_ids)
    if planned.satisfaction > 0:
        return ConditionResult(
            _C1.key, _C1.label, _C1.weight, 0.55,
            "已公告增持 / 回购计划，但尚未看到实施进展"
            "（计划不等于已买入）",
            planned.evidence_ids,
        )
    return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.40,
                           "存在股东行为类事件，但未见明确金额或比例")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 规模显著（相对市值 / 流通股比例）。

    ★ 诚实边界：我们没有市值数据，所以无法算「占市值比例」。
    能拿到的只有公告自述的比例 / 金额，以及 ``amount_ratio``。
    """
    events = facts.events_of(*_BUY_EVENTS)
    scaled = marker_condition(_C2, events, (
        (1.00, "公告披露了金额或比例", _SCALE_MARKERS),
        (0.30, "公告未披露规模，无法评估显著性", ()),
    ))
    if scaled.satisfaction >= 1.0:
        peak = max((e.amount_ratio for e in events), default=0.0)
        if peak >= 0.02:
            return ConditionResult(_C2.key, _C2.label, _C2.weight, 1.0,
                                   f"披露规模，金额占比 {peak:.2%}", scaled.evidence_ids)
    if facts.shareholder.buyback_scale_significant:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 1.0,
                               "回购规模被判定为显著", scaled.evidence_ids)
    return ConditionResult(
        _C2.key, _C2.label, _C2.weight, scaled.satisfaction, scaled.detail,
        scaled.evidence_ids,
    )


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 资金来源可评估。

    为什么这项重要：用**专项贷款**增持与用**自有资金**增持，
    传递的信心强度不同（前者有杠杆与到期压力）。
    未披露来源时不能默认「自有资金」。
    """
    events = facts.events_of(*_BUY_EVENTS)
    return marker_condition(_C3, events, (
        (1.00, "公告说明了资金来源", _FUNDING_MARKERS),
        (0.25, "未披露资金来源，无法评估", ()),
    ))


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 公司现金流稳定、负债可控。"""
    fin = facts.financials
    stable = fin.ocf_positive and not fin.debt_ratio_rising
    return financial_condition(
        _C4, stable,
        yes_detail="经营现金流为正且资产负债率未上升",
        no_detail=(
            f"经营现金流{'为正' if fin.ocf_positive else '不为正'}"
            f"，资产负债率{'在上升' if fin.debt_ratio_rising else '未上升'}"
        ),
        yes=1.0, no=0.20,
        unknown=fin.periods_with_data < 2,
        unknown_detail="财务数据不足，无法判断现金流与负债",
    )


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 无大股东质押风险。"""
    return financial_condition(
        _C5, not facts.shareholder.high_pledge,
        yes_detail="未见高质押信号",
        no_detail="存在高质押信号 —— 增持的可持续性存疑",
        yes=1.0, no=0.0,
    )


_SPEC = StrategySpec(
    code=ThesisType.SHAREHOLDER_ACTION,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    ladder=(
        LadderRung("计划公告", 40.0, False, _PLAN_MARKERS, _BUY_EVENTS),
        LadderRung("开始实施", 60.0, False, ("首次回购", "实施进展", "首次增持"), _BUY_EVENTS),
        LadderRung("实施完成", 70.0, False,
                   ("增持完成", "回购完成", "实施完毕", "累计回购"), _BUY_EVENTS),
        LadderRung("后续无减持（观察期内）", 95.0, False,
                   ("不减持", "承诺不减持", "锁定"), _BUY_EVENTS),
    ),
    ladder_event_types=_BUY_EVENTS,
    narrative=Narrative(
        subject="股东行为（真金白银的信心表达）",
        trigger=lambda f: (
            f"公司出现 {len(f.events_of(*_BUY_EVENTS))} 条增持 / 回购类公告"
            + ("，其中包含已实施记录"
               if any(kw in (e.title or "")
                      for e in f.events_of(*_BUY_EVENTS) for kw in _IMPLEMENTED_MARKERS)
               else "，目前仅为计划")
        ),
        support=lambda f: (
            f"经营现金流{'为正' if f.financials.ocf_positive else '不为正'}"
            f"，{'未见' if not f.shareholder.high_pledge else '存在'}高质押信号"
        ),
        caveat="增持 / 回购的实际执行进度、资金来源与后续是否出现减持尚待确认",
    ),
    markers={
        "增持资金来源": ((_BUY_EVENTS, _FUNDING_MARKERS)),
        "增持规模": ((_BUY_EVENTS, _SCALE_MARKERS)),
        "增持价格区间": ((_BUY_EVENTS, ("价格区间", "价格上限", "不超过", "元/股"))),
        "历史增持后的表现": ((_BUY_EVENTS, ("前次", "历史", "上次"))),
        "公司现金流": ((EventType.EARNINGS_TURNAROUND, ("现金流", "经营活动"))),
        "是否存在高负债": ((EventType.EARNINGS_TURNAROUND, ("负债", "资产负债率"))),
        "是否存在大股东质押": ((EventType.OTHER, ("质押", "解押"))),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
