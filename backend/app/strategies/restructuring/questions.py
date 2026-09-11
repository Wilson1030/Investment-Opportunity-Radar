"""``restructuring`` 策略的「待确认」事项（规格 §24 / M7-02）。

规格 §24 明确：「待确认」不能只是一个标签，它应该明确告诉用户 **还有什么没有确认**。
因此本模块把注册表里的模板变成**可判定的问题** —— 每个问题都有确认依据
（某类事件 + 标题标记），一旦依据出现即从 ``open`` 变为 ``confirmed``。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.base import StrategyEvaluation
from app.strategies.registry import get_def


@dataclass(frozen=True)
class QuestionSeed:
    question: str
    status: str                                # open / confirmed
    confirmed_by_event_id: int | None = None
    detail: str = ""


#: 问题 → 确认依据（事件类型集合，标题标记集合）
#: 标题标记为空表示「该事件类型本身即为确认」
QUESTION_MARKERS: dict[str, tuple[tuple[EventType, ...], tuple[str, ...]]] = {
    "交易标的": (
        (EventType.RESTRUCTURING, EventType.M_AND_A, EventType.ASSET_INJECTION),
        ("标的", "购买资产", "收购"),
    ),
    "交易价格": (
        (EventType.RESTRUCTURING, EventType.M_AND_A, EventType.ASSET_INJECTION),
        ("价格", "对价", "作价"),
    ),
    "重组方案": (
        (EventType.RESTRUCTURING,),
        ("报告书", "正式方案", "重组方案"),
    ),
    "资产评估结果": (
        (EventType.RESTRUCTURING, EventType.ASSET_INJECTION),
        ("评估", "评估结果"),
    ),
    "监管审核结果": (
        (EventType.REGULATORY_RISK, EventType.RESTRUCTURING),
        ("核准", "审核通过", "无条件通过", "注册生效"),
    ),
    "股东大会决议": (
        (EventType.RESTRUCTURING, EventType.CONTROL_CHANGE),
        ("股东大会决议", "股东大会通过"),
    ),
}


def templates() -> tuple[str, ...]:
    return get_def(ThesisType.RESTRUCTURING).open_question_templates


def _confirming_event(facts: StrategyFacts, question: str):
    spec = QUESTION_MARKERS.get(question)
    if spec is None:
        return None
    event_types, markers = spec
    for event in facts.events:
        if event.event_type not in event_types:
            continue
        if not markers or any(m in event.title for m in markers):
            return event
    return None


def evaluate(facts: StrategyFacts) -> tuple[QuestionSeed, ...]:
    """返回全部待确认事项及其状态。"""
    seeds: list[QuestionSeed] = []
    for question in templates():
        event = _confirming_event(facts, question)
        if event is None:
            seeds.append(QuestionSeed(question=question, status="open"))
        else:
            seeds.append(
                QuestionSeed(
                    question=question,
                    status="confirmed",
                    confirmed_by_event_id=event.id,
                    detail=f"依据：{event.title or event.event_type.value}",
                )
            )
    return tuple(seeds)


def open_only(facts: StrategyFacts) -> tuple[str, ...]:
    return tuple(s.question for s in evaluate(facts) if s.status == "open")


def open_count(facts: StrategyFacts) -> int:
    return len(open_only(facts))


def confirmed_facts(facts: StrategyFacts, evaluation: StrategyEvaluation) -> tuple[str, ...]:
    """「已确认」一侧 —— 来自命中的核心条件（规格 §24 的 ✓ 清单）。"""
    return tuple(c.label for c in evaluation.hits)


__all__ = [
    "QUESTION_MARKERS",
    "QuestionSeed",
    "confirmed_facts",
    "evaluate",
    "open_count",
    "open_only",
    "templates",
]
