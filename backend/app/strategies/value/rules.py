"""策略 10 · ``value`` 价值发现 / 高股息（docs/06 §12）。

> 寻找盈利稳定、现金流健康、持续分红且估值合理的公司。

★ 本策略的两个特殊之处：

**一、C5 估值（权重 0.20）需要估值数据。**
本项目已接入百度股市通估值源（``app/ingest/valuation.py``），
提供总市值 / 市盈率 / 市净率的历史序列 ——
所以 C5 **能真判**：用当前 PE / PB 在近三年序列里的分位判断「是否处于历史较低区间」。
拿不到数据时**不给假装中性的分数**，而是明说「估值数据未采集」。

**二、C6「低市场关注度**（权重 0.05）是本策略唯一的反向项 ——
高股息策略的有效性部分来自「没人看」。
统计上它只是个加分项，所以权重最低，且**不参与门控**。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import ConditionResult
from app.strategies.common.markers import marker_condition
from app.strategies.common.narrative import Narrative
from app.strategies.common.spec import LadderRung, RuleBasedStrategy, StrategySpec
from app.strategies.registry import get_def

_C = get_def(ThesisType.VALUE).core_conditions
_C1, _C2, _C3, _C4, _C5, _C6 = _C

_VALUE_EVENTS = (
    EventType.DIVIDEND_POLICY, EventType.BUYBACK,
    EventType.EARNINGS_TURNAROUND, EventType.SHAREHOLDER_BUY,
)

#: 分红类公告
_DIVIDEND_MARKERS = ("分红", "利润分配", "派息", "现金分红", "股东回报", "分配方案")
#: 提高分红的证据（C4）
_RAISE_MARKERS = ("提高", "提升", "增加", "上调", "特别分红", "中期分红",
                  "首次分红", "回报规划")
#: 估值「较低」的阈值：近三年分位 ≤ 30%
_CHEAP_PERCENTILE = 0.30
#: 估值「偏高」的阈值：≥ 70%
_EXPENSIVE_PERCENTILE = 0.70


def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 连续多年盈利。"""
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                               f"财务期数不足（{fin.periods_with_data} 期）")
    years = fin.profitable_years
    if years >= 4:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 1.0,
                               f"近年 {years} 期为盈利")
    if years >= 2:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.70,
                               f"近年 {years} 期为盈利（不足 4 期）")
    return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0,
                           f"盈利期数仅 {years} 期，不满足「连续多年盈利」")


def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 自由现金流稳定 / 经营现金流长期为正。"""
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0,
                               "财务期数不足，无法判断现金流稳定性")
    if fin.ocf_positive and not fin.debt_ratio_rising:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 1.0,
                               "经营现金流为正且资产负债率未上升")
    if fin.ocf_positive:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.60,
                               "经营现金流为正，但资产负债率在上升")
    return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0,
                           "经营现金流不为正，不满足价值策略的现金流前提")


def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 连续多年分红。

    ★ 诚实边界：我们的公告采集窗口有限，无法真的回看「多年」。
    所以这里数的是**采集到的不同年份**的分红公告数，并在 detail 里说明 —— 
    不能把「我们没采到」说成「公司没分红」。
    """
    events = facts.events_of(EventType.DIVIDEND_POLICY)
    hits = marker_condition(_C3, events, (
        (1.0, "", _DIVIDEND_MARKERS),
        (0.0, "", ()),
    ))
    if hits.satisfaction == 0:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.0,
                               "采集窗口内未见分红类公告（注意：没采到不等于公司未分红）")
    years = {e.event_time.year for e in events if e.event_time}
    if len(years) >= 2:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 1.0,
                               f"采集窗口内 {len(years)} 个年度有分红公告",
                               hits.evidence_ids)
    return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.45,
                           "采集窗口内仅 1 个年度有分红公告（历史分红记录未采集）",
                           hits.evidence_ids)


def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 最近提高分红比例，或发布回购计划 —— 权重最高（0.25）。

    为什么它比「一直在分红」更重要：**边际变化**才是催化。
    「连续分红」是存量状态，「提高分红比例」才是新的股东回报意愿表达。
    """
    dividend = facts.events_of(EventType.DIVIDEND_POLICY)
    buyback = facts.events_of(EventType.BUYBACK)
    raised = marker_condition(_C4, dividend, ((1.0, "", _RAISE_MARKERS), (0.0, "", ())))
    if raised.satisfaction > 0:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 1.0,
                               "已公告提高分红比例 / 特别分红", raised.evidence_ids)
    if buyback:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.80,
                               "已公告回购计划",
                               tuple(i for e in buyback for i in e.evidence_ids))
    if dividend:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.25,
                               "有常规分红公告，但未见提高比例或回购")
    return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.0,
                           "未见分红比例提升或回购计划")


def _condition_c5(facts: StrategyFacts) -> ConditionResult:
    """C5 估值处于历史较低区间 —— 用真实估值分位判断。

    数据来自百度股市通估值序列（近三年），分位由
    :mod:`app.ingest.valuation` 计算。
    """
    percentile = facts.valuation_percentile
    if percentile is None:
        return ConditionResult(
            _C5.key, _C5.label, _C5.weight, 0.0,
            "估值数据未采集，无法判断是否处于历史低位"
            "（注意：没有数据不等于估值合理）",
        )
    if percentile <= _CHEAP_PERCENTILE:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 1.0,
                               f"估值处于近三年 {percentile:.0%} 分位（偏低）")
    if percentile >= _EXPENSIVE_PERCENTILE:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                               f"估值处于近三年 {percentile:.0%} 分位（偏高）")
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.45,
                           f"估值处于近三年 {percentile:.0%} 分位（中性）")


def _condition_c6(facts: StrategyFacts) -> ConditionResult:
    """C6 低市场关注度（可选加分项，权重 0.05）。

    ★ 反向逻辑：对高股息 / 价值策略，「没人讨论」通常是好事
    （超额收益往往来自被忽视的标的）。所以这一条**关注度低才得分**。
    权重最低，且不参与任何门控。
    """
    clusters = facts.market.news_cluster_count
    buzz = facts.market.social_buzz
    if clusters == 0 and not buzz:
        return ConditionResult(_C6.key, _C6.label, _C6.weight, 1.0,
                               "无新闻聚类、无社交讨论（关注度低）")
    if clusters <= 2 and not buzz:
        return ConditionResult(_C6.key, _C6.label, _C6.weight, 0.60,
                               f"关注度较低（{clusters} 个新闻聚类）")
    return ConditionResult(_C6.key, _C6.label, _C6.weight, 0.0,
                           f"已有 {clusters} 个新闻聚类"
                           + ("，且存在社交讨论" if buzz else "") + "（关注度不低）")


_SPEC = StrategySpec(
    code=ThesisType.VALUE,
    conditions=(_condition_c1, _condition_c2, _condition_c3, _condition_c4,
                _condition_c5, _condition_c6),
    ladder=(
        LadderRung("分红政策变化", 40.0, False, _DIVIDEND_MARKERS + _RAISE_MARKERS,
                   (EventType.DIVIDEND_POLICY,)),
        LadderRung("提高分红比例", 70.0, False, _RAISE_MARKERS,
                   (EventType.DIVIDEND_POLICY,)),
        LadderRung("分红 + 回购组合", 85.0, False,
                   ("分红", "回购"), (EventType.DIVIDEND_POLICY, EventType.BUYBACK)),
        LadderRung("连续多年稳定回报", 95.0, False,
                   ("股东回报规划", "三年", "连续"), (EventType.DIVIDEND_POLICY,)),
    ),
    ladder_event_types=(EventType.DIVIDEND_POLICY, EventType.BUYBACK),
    narrative=Narrative(
        subject="价值发现 / 高股息",
        trigger=lambda f: (
            f"公司近年 {f.financials.profitable_years} 期盈利，"
            f"经营现金流{'为正' if f.financials.ocf_positive else '不为正'}"
        ),
        support=lambda f: (
            "已公告提高分红比例或回购计划"
            if any(
                kw in (e.title or "")
                for e in f.events_of(EventType.DIVIDEND_POLICY)
                for kw in _RAISE_MARKERS
            ) or f.events_of(EventType.BUYBACK)
            else "尚无提高分红比例或回购的公告"
        ),
        caveat="分红政策的可持续性、自由现金流稳定性、以及估值低是否源于价值陷阱尚待确认",
    ),
    markers={
        "分红政策的可持续性": ((EventType.DIVIDEND_POLICY, ("可持续", "规划", "稳定", "承诺"))),
        "自由现金流的稳定性": ((EventType.EARNINGS_TURNAROUND, ("现金流", "经营活动"))),
        "估值低的原因（是否存在价值陷阱）": ((_VALUE_EVENTS, ("商誉", "减值", "诉讼", "立案"))),
        "行业是否处于长期衰退": ((_VALUE_EVENTS, ("行业", "衰退", "下滑", "需求下降"))),
        "公司治理与股东回报意愿": (
            (EventType.DIVIDEND_POLICY, EventType.BUYBACK),
            ("回报", "回购", "增持", "承诺"),
        ),
    },
    unverifiable={
        "行业是否处于长期衰退": (
            "需要行业景气数据，当前数据源只有公司公告与财务"
        ),
    },
)

STRATEGY = RuleBasedStrategy(_SPEC)

__all__ = ["STRATEGY"]
