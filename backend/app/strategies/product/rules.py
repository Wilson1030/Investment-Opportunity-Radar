"""策略 9 · ``product`` 技术 / 新产品突破（docs/06 §11）。

> 寻找研发成果从实验室/概念逐步进入商业化的公司。

★ 本策略的价值在于**阶段细分**：研发成果 → 产品认证 → 客户验证 →
商业订单 → 收入贡献。规格把这五步做成五个条件，
是因为「拿到认证」和「拿到订单」之间的距离极远 ——
把它们混成一个「有技术突破」会严重高估确定性。

所以这里 C1（研发投入）刻意只给 0.10，而 C4（商业订单）+ C5（收入）
合计 0.50 —— **分数重心压在「能不能变成钱」上**。

★ 诚实边界：我们**没有研发费用数据**，C1 只能靠公告自述（专利 / 研发公告）。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.markers import marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.PRODUCT).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_PRODUCT_EVENTS = (EventType.NEW_PRODUCT, EventType.MAJOR_CONTRACT, EventType.M_AND_A)

#: 研发投入 / 成果（C1/C2）
_RND_MARKERS = ("研发", "专利", "技术突破", "中试", "样机", "试验")
#: 产品认证（C2）
_CERT_MARKERS = ("认证", "获批", "注册证", "许可", "型式试验", "检测通过",
                 "批准上市", "取得批件", "通过评审")
#: 客户验证（C3）
_VALIDATION_MARKERS = ("客户验证", "送样", "测试通过", "定点", "验证通过",
                       "小批量", "试用", "客户认可")
#: 商业订单（C4）
_ORDER_MARKERS = ("订单", "合同", "中标", "采购协议", "供货", "批量供货")
#: 收入贡献（C5）
_REVENUE_MARKERS = ("收入", "贡献", "放量", "量产", "营业收入", "业绩贡献")


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 长期研发投入（历史证据）—— 权重最低（0.10）。

    ★ 我们没有研发费用数据。这条只作为**背景**，不该主导评分 ——
    规格给它 0.10 也正是这个意思。
    """
    events = facts.events_of(*_PRODUCT_EVENTS)
    return marker_condition(_C1, events, (
        (0.70, "公告中出现研发 / 专利 / 技术类信息", _RND_MARKERS),
        (0.0, "未见研发类信息；研发费用数据未采集", ()),
    ), note="研发费用数据未采集，无法验证投入的持续性")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 研发成果 / 产品认证取得。"""
    events = facts.events_of(*_PRODUCT_EVENTS)
    return marker_condition(_C2, events, (
        (1.00, "已取得产品认证 / 注册证 / 批准", _CERT_MARKERS),
        (0.35, "仅有研发成果，尚未取得认证", _RND_MARKERS),
        (0.0, "未见研发成果或认证信息", ()),
    ))


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 客户验证通过。"""
    events = facts.events_of(*_PRODUCT_EVENTS)
    return marker_condition(_C3, events, (
        (1.00, "客户验证 / 送样 / 测试已通过", _VALIDATION_MARKERS),
        (0.20, "尚未看到客户验证信息", ()),
    ))


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 签署商业订单 —— 权重 0.25。

    ★ 这是「技术」变成「生意」的分水岭。没有它，前面的认证与验证
    都只是**可能性**，不该给出高覆盖率。
    """
    events = facts.events_of(*_PRODUCT_EVENTS)
    return marker_condition(_C4, events, (
        (1.00, "已签署商业订单 / 中标 / 批量供货", _ORDER_MARKERS),
        (0.15, "尚未看到商业订单 —— 技术尚未转化为生意", ()),
    ))


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 新产品开始贡献收入 —— 权重 0.25。"""
    events = facts.events_of(*_PRODUCT_EVENTS)
    hit = marker_condition(_C5, events, (
        (1.00, "公告显示新产品已贡献收入 / 放量", _REVENUE_MARKERS),
        (0.0, "尚未看到新产品收入贡献", ()),
    ))
    if hit.satisfaction > 0:
        return hit
    fin = facts.financials
    if fin.periods_with_data >= 2 and fin.revenue_improving_quarters >= 2:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.40,
                               f"无新品收入公告，但营收已连续 {fin.revenue_improving_quarters} 期增长")
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                           "尚未看到新产品收入贡献")


_SPEC = StrategySpec(
    code=ThesisType.PRODUCT,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    ladder=(
        LadderRung("研发成果", 20.0, True, _RND_MARKERS, _PRODUCT_EVENTS),
        LadderRung("产品认证", 45.0, False, _CERT_MARKERS, _PRODUCT_EVENTS),
        LadderRung("首批客户", 65.0, False, _VALIDATION_MARKERS, _PRODUCT_EVENTS),
        LadderRung("商业订单", 85.0, False, _ORDER_MARKERS, _PRODUCT_EVENTS),
        LadderRung("收入贡献", 95.0, False, _REVENUE_MARKERS, _PRODUCT_EVENTS),
    ),
    ladder_event_types=_PRODUCT_EVENTS,
    narrative=Narrative(
        subject="技术 / 新产品突破",
        trigger=lambda f: (
            "公司出现研发 / 新产品类公告"
            if f.events_of(*_PRODUCT_EVENTS)
            else "公司暂无研发 / 新产品类公告"
        ),
        support=lambda f: (
            "已进入商业订单 / 收入贡献阶段"
            if any(
                kw in (e.title or "")
                for e in f.events_of(*_PRODUCT_EVENTS)
                for kw in _ORDER_MARKERS + _REVENUE_MARKERS
            )
            else "仍停留在研发 / 认证 / 验证阶段，尚未变成订单"
        ),
        caveat="认证与客户验证的实际进度、订单可执行性、收入确认时点与技术路线竞争格局尚待确认",
    ),
    markers={
        "认证与客户验证的实际进展": ((EventType.NEW_PRODUCT, _CERT_MARKERS + _VALIDATION_MARKERS)),
        "订单的可执行性与交付条件": ((EventType.MAJOR_CONTRACT, ("交付", "条件", "预付", "验收"))),
        "新产品收入确认的时点与规模": ((EventType.NEW_PRODUCT, _REVENUE_MARKERS)),
        "技术路线的竞争格局": ((EventType.NEW_PRODUCT, ("替代", "竞争", "路线", "壁垒"))),
        "研发投入对当期业绩的拖累": ((EventType.EARNINGS_TURNAROUND, ("研发费用", "费用", "亏损"))),
    },
    unverifiable={
        "研发投入对当期业绩的拖累": (
            "需要研发费用明细，当前采集的财务指标里没有研发费用科目"
        ),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
