"""策略 6 · ``policy`` 政策驱动（docs/06 §8）。

验收闸门（docs/06 §16）：**验证 Macro → Industry → Company 下行链路**。

这条闸门要的是：政策不能直接跳到「利好某公司」——
必须先识别受影响行业、再拆到产业链环节、最后才落到公司主营。
所以条件 C1→C5 就是这条链路的每一跳，**顺序门控**也用在这里
（docs/06 §14.2：缺业务验证时压到 0.45）。

★ 诚实边界：我们**没有行业数据**。所以 C2（识别行业）只能靠公司自报的
``industry`` / ``industry_chain``，C3（拆到环节）靠公告措辞。
C5（已有订单 / 产能 / 收入验证）反而是**能真判**的 —— 这正是门控的意义：
政策链条走得再漂亮，没有业务验证就不给高分。
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

_C = get_def(ThesisType.POLICY).core_conditions
_C1, _C2, _C3, _C4, _C5 = _C

_POLICY_EVENTS = (EventType.POLICY_CATALYST, EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT)

#: 官方来源标记（C1）—— 政策必须来自官方文件 / 部委发布
_OFFICIAL_MARKERS = ("国务院", "发改委", "工信部", "财政部", "能源局", "央行",
                     "部委", "通知", "意见", "规划", "办法", "条例", "公告",
                     "试点", "实施方案", "若干措施")
#: 笼统表述（C3 的反面）——「相关行业」不是产业链拆解
_VAGUE_INDUSTRY = ("相关行业", "相关企业", "相关公司", "部分企业", "有关行业")
#: 产业链环节标记（C3）
_CHAIN_MARKERS = ("上游", "下游", "中游", "原材料", "零部件", "设备", "整机",
                  "运营商", "工程", "EPC", "配套", "环节", "细分")
#: 主营相关标记（C4）
_RELEVANCE_MARKERS = ("主营业务", "主营", "核心业务", "主要产品", "产能", "生产线")

#: 缺业务验证时的覆盖率上限（docs/06 §14.2 明确点名）
BUSINESS_VERIFICATION_CAP = 0.45


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 政策发生重大变化（官方文件 / 部委发布）。"""
    events = own_subject_events(facts.events_of(EventType.POLICY_CATALYST))
    if not events:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                               "未见政策类公告")
    level = best_level(events)
    scale, level_note = satisfaction_for_level(level)
    official = marker_condition(_C1, events, ((1.0, "", _OFFICIAL_MARKERS), (0.0, "", ())))
    if official.satisfaction > 0:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, round(scale, 4),
                               f"政策来自官方文件 / 部委发布（{level_note}）",
                               official.evidence_ids)
    return ConditionResult(_C1.key, _C1.label, _C1.weight, round(scale * 0.5, 4),
                           f"存在政策类信息，但未见官方来源标识（{level_note}）")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 识别出受影响行业。

    ★ 我们没有行业数据库，能用的只有公司自报的 ``industry`` 与
    ``industry_chain``。所以这里只能确认「公司所属行业可识别」，
    而不能确认「该行业真的受益」—— 后者需要行业数据，**写进 detail**。
    """
    has_industry = bool(facts.company.industry)
    has_chain = bool(facts.company.industry_chain)
    if has_industry and has_chain:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.75,
                               f"公司行业（{facts.company.industry}）与产业链环节可识别"
                               "（行业受益程度当前无行业数据可验证）")
    if has_industry:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.45,
                               f"公司行业为 {facts.company.industry}，但产业链环节未采集")
    return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0,
                           "公司所属行业未知，无法判断受益关系")


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 产业链拆解到具体环节（非笼统「相关行业」）。

    规格明确否定「相关行业」这种表述 —— 它无法变成可跟踪的标的。
    """
    events = own_subject_events(facts.events_of(*_POLICY_EVENTS))
    chain = marker_condition(_C3, events, ((1.0, "", _CHAIN_MARKERS), (0.0, "", ())))
    vague = marker_condition(_C3, events, ((1.0, "", _VAGUE_INDUSTRY), (0.0, "", ())))
    if chain.satisfaction > 0 and vague.satisfaction == 0:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 1.0,
                               "公告拆解到具体产业链环节", chain.evidence_ids)
    if chain.satisfaction > 0:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.70,
                               "既提到具体环节，也出现「相关行业」类笼统表述",
                               chain.evidence_ids)
    if vague.satisfaction > 0:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.15,
                               "仅有「相关行业 / 相关企业」类笼统表述，未拆到环节",
                               vague.evidence_ids)
    return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.25,
                           "未见产业链环节信息（公司产业链数据未采集）")


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 公司主营业务高度相关（非「属于该行业」）。"""
    events = own_subject_events(facts.events_of(*_POLICY_EVENTS))
    return marker_condition(_C4, events, (
        (1.00, "公告直接点名公司主营业务 / 产能与政策的对应关系", _RELEVANCE_MARKERS),
        (0.30, "仅能确认公司属于该行业，主营业务相关性需人工核对", ()),
    ), note="主营构成数据未采集")


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 已有实际订单 / 产能 / 收入验证 —— 权重最高（0.25）。

    ★ 这条是**顺序门控的锚**：政策链条走得再漂亮，没有业务验证就是空中楼阁。
    规格 docs/06 §14.2 点名 policy 缺业务验证时把覆盖率压到 0.45。
    """
    contract_events = own_subject_events(
        facts.events_of(EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT)
    )
    hit = marker_condition(_C5, contract_events, (
        (1.00, "已有订单 / 中标 / 产能落地公告", ("中标", "订单", "合同", "投产", "达产")),
        (0.0, "未见订单 / 产能 / 收入验证", ()),
    ))
    if hit.satisfaction > 0:
        return hit
    fin = facts.financials
    if fin.periods_with_data >= 2 and fin.revenue_improving_quarters >= 1:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.45,
                               "虽无订单公告，但营收已出现同比改善")
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                           "未见订单 / 产能 / 收入验证 —— 政策尚未落到业务上")


def _coverage_cap(conditions: dict[str, ConditionResult]) -> float | None:
    """顺序门控（docs/06 §14.2）：缺业务验证时压到 0.45。

    ★ 为什么必须存在：宏观政策对**整个行业**成立，对**某一家公司**不一定成立。
    没有 C5 的业务验证，一条宏观利好可以被套到几十家公司头上 ——
    那不是发现机会，是拿着同一个故事到处贴。
    """
    if conditions["C5"].satisfaction == 0.0:
        return BUSINESS_VERIFICATION_CAP
    return None


_SPEC = StrategySpec(
    code=ThesisType.POLICY,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4, _condition_c5),
    coverage_cap=_coverage_cap,
    ladder=(
        LadderRung("政策发布", 30.0, False, _OFFICIAL_MARKERS, (EventType.POLICY_CATALYST,)),
        LadderRung("细则出台", 55.0, False,
                   ("实施细则", "细则", "配套措施", "管理办法", "试点方案"),
                   (EventType.POLICY_CATALYST,)),
        LadderRung("公司订单落地", 80.0, False,
                   ("中标", "订单", "合同"), (EventType.MAJOR_CONTRACT,)),
        LadderRung("收入体现", 95.0, False,
                   ("收入确认", "贡献收入", "投产", "达产", "量产"),
                   (EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT)),
    ),
    ladder_event_types=_POLICY_EVENTS,
    narrative=Narrative(
        subject="政策驱动",
        trigger=lambda f: (
            f"出现 {len(own_subject_events(f.events_of(EventType.POLICY_CATALYST)))} 条"
            f"政策类信息，公司所属行业为 {f.company.industry or '未知'}"
        ),
        support=lambda f: (
            "已看到订单 / 产能层面的落地证据"
            if any(
                kw in (e.title or "")
                for e in own_subject_events(f.events_of(EventType.MAJOR_CONTRACT))
                for kw in ("中标", "订单", "合同")
            )
            else "尚无订单 / 产能层面的落地证据（政策链条尚未走到公司）"
        ),
        caveat="政策细则与实施时点、产业链受益环节的分配、以及兑现周期尚待确认",
    ),
    markers={
        "政策细则与实施时点": ((EventType.POLICY_CATALYST, ("细则", "实施", "时间", "自…起"))),
        "公司所在产业链环节的受益程度": ((_POLICY_EVENTS, _CHAIN_MARKERS)),
        "公司是否已有实际订单 / 收入": (
            (EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT),
            ("订单", "中标", "收入"),
        ),
        "政策兑现的时间周期": ((EventType.POLICY_CATALYST, ("周期", "年内", "目标", "规划期"))),
    },
    unverifiable={
        # ★ 行业数据我们没有，说清楚而不是假装能判
        "公司所在产业链环节的受益程度": (
            "受益程度需要行业层面的量价数据，当前数据源只有公司公告与财务，"
            "因此本条只能靠公告自述，无法独立验证"
        ),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["BUSINESS_VERIFICATION_CAP", "STRATEGY"]
