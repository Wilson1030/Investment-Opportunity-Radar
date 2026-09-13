"""``turnaround`` 策略的「待确认」事项（规格 §24 / 示例 B）。

规格示例 B 直接给出了模板：

    ? 改善是否具有持续性
    ? 新订单是否能够转化为收入
    ? 行业景气是否能够持续
    ? 改善是否依赖一次性因素（资产处置 / 减值转回 / 政府补助）

§24 的要求是：「待确认不能只是一个标签，它应该明确告诉用户**还有什么没有确认**」。
所以每个问题都要有**可判定的确认依据** —— 依据出现即从 ``open`` 变 ``confirmed``。

★ 这里有一处必须诚实：``行业景气是否能够持续`` 在当前数据源下
**无法判定**（没有行业数据）。它必须永远保持 ``open`` 并说明原因，
而不是某天「自动确认」—— 那会是假确认。
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
    status: str                                # open / confirmed / unverifiable
    confirmed_by_event_id: int | None = None
    detail: str = ""


#: 无法用当前数据源判定的事项 —— 必须显式标出来，不能默认「没问题」
UNVERIFIABLE: dict[str, str] = {
    "行业景气是否能够持续": "当前数据源无行业数据（只有公司公告与财务），无法判定",
}

#: 问题 → (事件类型集合, 标题标记集合)。标记为空表示该事件类型本身即为确认。
QUESTION_MARKERS: dict[str, tuple[tuple[EventType, ...], tuple[str, ...]]] = {
    "改善是否具有持续性": (
        (EventType.EARNINGS_TURNAROUND,),
        ("年报", "年度报告", "业绩快报", "连续", "第二季度", "三季度"),
    ),
    "新订单是否能够转化为收入": (
        (EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT),
        ("收入", "确认收入", "交付", "验收", "量产"),
    ),
    "改善是否依赖一次性因素（资产处置 / 减值转回 / 政府补助）": (
        (EventType.EARNINGS_TURNAROUND, EventType.M_AND_A, EventType.OTHER),
        ("一次性", "非经常性", "资产处置", "减值转回", "政府补助", "与主营业务无关"),
    ),
}


def templates() -> tuple[str, ...]:
    return get_def(ThesisType.TURNAROUND).open_question_templates


def _confirming_event(facts: StrategyFacts, question: str):
    spec = QUESTION_MARKERS.get(question)
    if spec is None:
        return None
    event_types, markers = spec
    for event in facts.events:
        if event.event_type not in event_types:
            continue
        if not markers or any(m in (event.title or "") for m in markers):
            return event
    return None


def evaluate(facts: StrategyFacts) -> tuple[QuestionSeed, ...]:
    seeds: list[QuestionSeed] = []
    for question in templates():
        if question in UNVERIFIABLE:
            seeds.append(QuestionSeed(
                question=question, status="unverifiable", detail=UNVERIFIABLE[question],
            ))
            continue
        event = _confirming_event(facts, question)
        if event is None:
            seeds.append(QuestionSeed(question=question, status="open"))
        else:
            seeds.append(QuestionSeed(
                question=question, status="confirmed",
                confirmed_by_event_id=event.id,
                detail=f"依据：{event.title or event.event_type.value}",
            ))
    return tuple(seeds)


def open_only(facts: StrategyFacts) -> tuple[str, ...]:
    """未确认的事项 —— ``unverifiable`` 也算「未确认」（只是原因不同）。

    ★ 不能把「无法判定」当成「已确认」：那等于用沉默冒充证据。
    """
    return tuple(s.question for s in evaluate(facts) if s.status != "confirmed")


def open_count(facts: StrategyFacts) -> int:
    return len(open_only(facts))


def confirmed_facts(facts: StrategyFacts, evaluation: StrategyEvaluation) -> tuple[str, ...]:
    return tuple(c.label for c in evaluation.hits)


__all__ = [
    "QUESTION_MARKERS",
    "UNVERIFIABLE",
    "QuestionSeed",
    "confirmed_facts",
    "evaluate",
    "open_count",
    "open_only",
    "templates",
]
