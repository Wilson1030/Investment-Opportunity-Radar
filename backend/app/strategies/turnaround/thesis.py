"""``turnaround`` 策略的 Thesis 语句与 Why Now（规格 §21 / §52 / 示例 B）。

规格示例 B 明确禁止「业绩大涨，所以看好」这种表述，要求形成
**Thesis 语句**并列出 Supporting Evidence / Uncertainties / Invalidating Events 三段。

★ 本模块的一个额外责任：**把「财务数据的改善」与「公司是不是 ST 分开说**。
示例 J 的教训是标签不决定策略 —— 那么叙事里也不该让标签占到中心位置。
所以这里的措辞一律以**经营数据**为主语。
"""

from __future__ import annotations

from app.facts import StrategyFacts
from app.strategies.base import StrategyEvaluation


def _improvement_bits(facts: StrategyFacts) -> list[str]:
    """改善侧的表述（一律用经营数据做主语）。"""
    fin = facts.financials
    bits: list[str] = []
    if fin.revenue_improving_quarters >= 2:
        bits.append(f"营收连续 {fin.revenue_improving_quarters} 期同比增长")
    elif fin.revenue_improving_quarters == 1:
        bits.append("营收单期同比增长")
    if fin.margin_improving_quarters >= 2:
        bits.append(f"毛利率连续 {fin.margin_improving_quarters} 期改善")
    elif fin.margin_improving_quarters == 1:
        bits.append("毛利率单期改善")
    if fin.ocf_turned_positive:
        bits.append("经营现金流转正")
    elif fin.ocf_improving:
        bits.append("经营现金流同比改善")
    return bits


def _deterioration_bits(facts: StrategyFacts) -> list[str]:
    """恶化侧的表述 —— 反转的前提，不能省略。"""
    fin = facts.financials
    bits: list[str] = []
    if fin.revenue_declining_run_max >= 2:
        bits.append(f"营收曾连续 {fin.revenue_declining_run_max} 期下降")
    if fin.margin_declining_run_max >= 2:
        bits.append(f"毛利率曾连续 {fin.margin_declining_run_max} 期下降")
    if fin.loss_run_max >= 2:
        bits.append(f"曾连续 {fin.loss_run_max} 期亏损")
    return bits


def build_statement(facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
    """生成 Thesis 语句（必须同时包含「先恶化」与「后改善」两面）。"""
    worse = _deterioration_bits(facts)
    better = _improvement_bits(facts)

    if worse and better:
        return (
            f"公司历史经营数据显示{'、'.join(worse[:2])}，"
            f"近期出现{'、'.join(better[:2])}，"
            f"因此存在经营周期拐点的可能，但改善的持续性尚待确认。"
        )
    if better:
        # 没有历史恶化 → 不是「反转」，只能说改善。措辞必须如实。
        return (
            f"公司近期出现{'、'.join(better[:2])}，"
            f"但未发现此前的连续经营恶化，因此更接近「增长」而非「困境反转」。"
        )
    if worse:
        return (
            f"公司历史经营数据显示{'、'.join(worse[:2])}，"
            f"尚未出现改善迹象，因此暂不构成困境反转。"
        )
    return "公司经营数据尚未显示「先恶化、后改善」的模式，暂不构成困境反转。"


def why_now(facts: StrategyFacts) -> dict[str, str]:
    """Why Now 四段（规格 §52 的固定叙事结构）。"""
    worse = _deterioration_bits(facts)
    better = _improvement_bits(facts)
    fin = facts.financials

    latest = None
    ordered = sorted(
        [e for e in facts.events if e.event_time], key=lambda e: e.event_time, reverse=True
    )
    if ordered:
        latest = ordered[0]

    # 一次性因素必须显式提示（规格示例 B 的待确认模板第一条之外的另一半）
    one_off = "（改善是否依赖一次性因素尚待确认）" if better else ""

    return {
        "past": (
            f"历史经营数据显示{'、'.join(worse[:2])}" if worse
            else f"未发现连续经营恶化（已采集 {fin.periods_with_data} 期财务）"
        ),
        "recent": (
            f"近期出现{'、'.join(better[:2])}{one_off}" if better
            else "近期未见经营改善迹象"
        ),
        "this_week": (
            f"最新事件：{latest.title[:40]}" if latest else "本周无新增业绩类公告"
        ),
        "conclusion": (
            "因此该公司进入「困境反转」机会池，但改善的持续性仍需跟踪。"
            if worse and better
            else "因此尚未进入「困境反转」机会池。"
        ),
    }


__all__ = ["build_statement", "why_now"]
