"""策略 3 · ``event_driven`` 事件驱动（docs/06 §5）。

验收闸门（docs/06 §16）：**不依赖行情数据也能产出机会**。

这条闸门对本项目特别有意义：东方财富接口在本环境不可达，
所以行情数据本来就拿不到。事件驱动策略若依赖行情，
它就永远出不了机会 —— 闸门要求的是「只靠公告与财务就能成立」。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.evidence import best_level, evidence_ids_of, own_subject_events, satisfaction_for_level
from app.strategies.common.markers import event_type_condition, marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.EVENT_DRIVEN).core_conditions
_C1, _C2, _C3, _C4 = _C

#: 「单一事件」的候选事件类型（registry 的 support_event_types）
_EVENT_TYPES = (
    EventType.M_AND_A, EventType.BUYBACK, EventType.SHAREHOLDER_BUY,
    EventType.SHAREHOLDER_SELL, EventType.POLICY_CATALYST, EventType.MAJOR_CONTRACT,
    EventType.NEW_PRODUCT, EventType.ASSET_INJECTION, EventType.MANAGEMENT_CHANGE,
    EventType.DIVIDEND_POLICY, EventType.LITIGATION, EventType.REGULATORY_RISK,
)

#: 模糊表述 —— 出现它们说明事件方向**不明确**（规格要求 C4 排掉这类）
_VAGUE_MARKERS = ("可能", "拟筹划", "筹划", "传闻", "意向", "正在研究", "不排除")
#: 明确表述 —— 与模糊标记同时出现时以明确为准
_EXPLICIT_MARKERS = ("已签署", "签署", "协议", "合同", "中标", "已完成", "获得", "批复")

#: 后续节点标记（C3）
_FOLLOWUP_MARKERS = (
    "进展", "审议", "股东大会", "批复", "核准", "交割", "过户", "实施",
    "评估", "审计", "问询", "反馈", "说明会", "完成",
)


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 存在明确的单一事件（A/B 类证据）—— 权重最高（0.35）。

    「单一」是关键字：事件驱动的前提是**能指出是哪一件事**在驱动。
    一堆杂七杂八的事件凑不出一个可跟踪的催化，所以这里检查
    「最强事件类型是否占据了主要证据」。
    """
    events = own_subject_events(facts.events_of(*_EVENT_TYPES))
    if not events:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0, "未见可跟踪的单一事件")

    level = best_level(events)
    scale, level_note = satisfaction_for_level(level)

    counts: dict[EventType, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    top_type, top_count = max(counts.items(), key=lambda kv: kv[1])
    concentration = top_count / len(events)

    detail = f"主要事件类型 {top_type.value}（{top_count}/{len(events)} 条，{level_note}）"
    if concentration >= 0.6 or len(events) == 1:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, round(scale, 4),
                               f"{detail}，事件集中", evidence_ids_of(events))
    # 事件过于分散 → 说不上「单一事件」，打折
    return ConditionResult(_C1.key, _C1.label, _C1.weight, round(scale * 0.55, 4),
                           f"{detail}，但事件较分散（难以归因）", evidence_ids_of(events))


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 事件金额 / 规模对公司具备显著影响。

    ★ 诚实边界：``amount_ratio`` 只在公告披露了金额时才有值。
    未披露时**不能**按 0 处理（0 表示「金额很小」，
    而真实情况是「我们不知道金额」），所以单独给一档并说明。
    """
    events = own_subject_events(facts.events_of(*_EVENT_TYPES))
    if not events:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0, "无事件可评估规模")

    ratios = [e.amount_ratio for e in events if e.amount_ratio > 0]
    if not ratios:
        return ConditionResult(
            _C2.key, _C2.label, _C2.weight, 0.25,
            "公告未披露金额 / 规模，无法评估显著性（注意：未披露不等于金额小）",
        )
    peak = max(ratios)
    scaled = min(1.0, peak / 0.30)      # 占营收/净资产 30% 以上视为显著
    return ConditionResult(
        _C2.key, _C2.label, _C2.weight, round(scaled, 4),
        f"最大金额占比 {peak:.1%}（占营收 / 净资产口径）",
    )


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 事件有可预期的后续节点（进展可跟踪）。"""
    events = own_subject_events(facts.events_of(*_EVENT_TYPES))
    return marker_condition(_C3, events, (
        (1.00, "公告已披露后续节点（审议 / 批复 / 交割等）",
         _FOLLOWUP_MARKERS),
        (0.35, "尚未看到后续节点披露", ()),
    ))


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 事件方向明确（非「可能筹划」等模糊表述）。

    规格点名要排掉「可能筹划」——那种表述既不能证伪也不能跟踪，
    作为「事件驱动」的驱动源没有意义。
    """
    events = own_subject_events(facts.events_of(*_EVENT_TYPES))
    if not events:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.0, "无事件可判断方向")

    vague = marker_condition(_C4, events, ((1.0, "", _VAGUE_MARKERS), (0.0, "", ())))
    explicit = marker_condition(_C4, events, ((1.0, "", _EXPLICIT_MARKERS), (0.0, "", ())))

    if explicit.satisfaction > 0 and not vague.satisfaction > 0:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 1.0,
                               "事件表述明确（已签署 / 已完成等）",
                               explicit.evidence_ids)
    if explicit.satisfaction > 0 and vague.satisfaction > 0:
        # 既有明确也有模糊 → 以明确的部分为准，但打折
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.70,
                               "以明确表述为主，但同一批次公告中含「筹划 / 意向」措辞",
                               explicit.evidence_ids)
    if vague.satisfaction > 0:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.15,
                               "仅有「可能 / 筹划 / 意向」类模糊表述，方向不明",
                               vague.evidence_ids)
    return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.40,
                           "事件存在但未见明确的方向性措辞")


_SPEC = StrategySpec(
    code=ThesisType.EVENT_DRIVEN,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4),
    ladder=(
        LadderRung("市场传闻", 20.0, False, ("传闻", "媒体报道", "股价异动", "异常波动")),
        LadderRung("正式公告", 40.0, False,
                   ("公告", "签署", "协议", "合同", "中标", "预案", "获得")),
        LadderRung("进展公告", 60.0, False,
                   ("进展", "审议", "批复", "核准", "问询", "反馈", "评估")),
        LadderRung("实施完成", 85.0, False,
                   ("实施完成", "交割", "过户完成", "完成过户", "已实施")),
        LadderRung("效果确认", 95.0, False,
                   ("收入确认", "贡献收入", "达产", "业绩贡献", "效果")),
    ),
    ladder_event_types=_EVENT_TYPES,
    narrative=Narrative(
        subject="事件驱动",
        trigger=lambda f: (
            f"公司近期出现 {len(own_subject_events(f.events_of(*_EVENT_TYPES)))} 条"
            f"可跟踪的重大事件公告"
        ),
        support=lambda f: (
            "事件类型集中且证据等级可核对"
            if best_level(own_subject_events(f.events_of(*_EVENT_TYPES))) is not None
            else "事件证据等级待补充"
        ),
        caveat="事件的最终规模、执行时点与落到收入 / 利润的实际影响尚待确认",
    ),
    markers={
        "事件的最终规模与执行时点": (
            (EventType.M_AND_A, EventType.MAJOR_CONTRACT, EventType.ASSET_INJECTION),
            ("价格", "对价", "作价", "金额", "实施", "交割"),
        ),
        "事件对公司收入 / 利润的实际影响": (
            (EventType.EARNINGS_TURNAROUND, EventType.MAJOR_CONTRACT),
            ("收入", "利润", "贡献", "并表", "纳入合并"),
        ),
        "事件的后续节点与时间表": (
            _EVENT_TYPES, ("时间表", "预计", "计划", "股东大会", "批复", "交割"),
        ),
        "事件是否附带前置条件": (
            _EVENT_TYPES, ("前提条件", "先决条件", "尚需", "需经", "待批"),
        ),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
