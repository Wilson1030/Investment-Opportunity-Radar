"""``restructuring`` 策略的 Thesis 语句与 Why Now（规格 §21 / §52）。

规格 §21：不要把用户保存成「自选股：ST XXX」，而应保存成
**「因为重组预期，所以关注 ST XXX」**。本模块负责生成那句「因为……」。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.facts import StrategyFacts
from app.models.enums import EventType
from app.strategies.base import StrategyEvaluation

_MIN = datetime.min.replace(tzinfo=timezone.utc)

_EVENT_LABELS: dict[EventType, str] = {
    EventType.RESTRUCTURING: "重大资产重组",
    EventType.ASSET_INJECTION: "资产注入",
    EventType.CONTROL_CHANGE: "控制权变化",
    EventType.BANKRUPTCY_REORGANIZATION: "破产重整",
    EventType.M_AND_A: "并购",
}


def _labels(facts: StrategyFacts) -> list[str]:
    """按出现顺序去重的事件中文标签。"""
    seen: list[str] = []
    for event in sorted(facts.events, key=lambda e: e.event_time or _MIN, reverse=True):
        label = _EVENT_LABELS.get(event.event_type)
        if label and label not in seen:
            seen.append(label)
    return seen


def build_statement(facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
    """生成 Thesis 语句。

    规格示例 A 的原始表述：
    「公司处于 ST 状态，同时出现重大资产重组及控制权变化，因此存在潜在重组预期。」
    """
    labels = _labels(facts)
    distress = "公司处于 ST 状态" if facts.company.is_st else "公司经营承压"

    if labels:
        joined = "及".join(labels[:3])
        return f"{distress}，同时出现{joined}，因此存在潜在重组预期。"
    return f"{distress}，目前尚无重组类事件支撑，暂不构成重组预期。"


def why_now(facts: StrategyFacts) -> dict[str, str]:
    """Why Now 四段（规格 §52 的固定叙事结构）。"""
    ordered = sorted(
        [e for e in facts.events if e.event_time],
        key=lambda e: e.event_time,
        reverse=True,
    )
    latest = ordered[0] if ordered else None
    labels = _labels(facts)
    distress_bits: list[str] = []
    if facts.company.is_st:
        distress_bits.append("ST 状态")
    if facts.financials.loss_years >= 2:
        distress_bits.append(f"连续 {facts.financials.loss_years} 年亏损")
    if facts.financials.ocf_positive is False and facts.financials.loss_years:
        distress_bits.append("现金流承压")

    conclusion = (
        "因此该公司进入「重组预期」机会池。"
        if labels
        else "因此尚未进入「重组预期」机会池。"
    )
    return {
        "past": f"公司长期处于{'、'.join(distress_bits)}" if distress_bits else "公司历史经营数据见财务页",
        "recent": (
            f"近期出现{'、'.join(labels[:2])}" if labels else "近期未见重组类事件"
        ),
        "this_week": (
            f"最新事件：{latest.title or latest.event_type.value}"
            if latest
            else "本周无新增重组类公告"
        ),
        "conclusion": conclusion,
    }


__all__ = ["build_statement", "why_now"]
