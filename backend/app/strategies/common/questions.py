"""泛化的「待确认」事项（规格 §24）。

§24 的要求：待确认不能只是一个标签，它应该明确告诉用户**还有什么没有确认**。
所以每个问题都要有**可判定的确认依据** —— 依据出现即从 ``open`` 变 ``confirmed``。

三种状态，缺一不可：

  ``open``          有明确依据但尚未出现 → 继续等
  ``confirmed``     依据已出现 → 从「待确认」移除
  ``unverifiable``  **当前数据源无法判定** → 永远保持未确认，并说明原因

★ 为什么必须有 ``unverifiable``：如果只允许 open / confirmed，
那么「没有数据」的事项要么永远 open（看起来像「暂时还没消息」，
暗示再等等就会有），要么被人「顺手标成 confirmed」——
后者是用沉默冒充证据。显式标出「我们没有这个数据源」才是诚实的。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.facts import StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies.registry import get_def


@dataclass(frozen=True)
class QuestionSeed:
    question: str
    status: str                                # open / confirmed / unverifiable
    confirmed_by_event_id: int | None = None
    detail: str = ""


#: 问题 → ``(事件类型集合, 标题标记集合)``。标记为空表示该事件类型本身即为确认。
MarkerMap = dict[str, tuple[tuple[EventType, ...], tuple[str, ...]]]


def templates_for(thesis_type: ThesisType | str) -> tuple[str, ...]:
    return get_def(ThesisType(thesis_type)).open_question_templates


def _confirming_event(facts: StrategyFacts, spec: tuple):
    event_types, markers = spec
    for event in facts.events:
        if event.event_type not in event_types:
            continue
        if not markers or any(m in (event.title or "") for m in markers):
            return event
    return None


def evaluate_questions(
    facts: StrategyFacts,
    thesis_type: ThesisType | str,
    markers: MarkerMap,
    unverifiable: dict[str, str] | None = None,
) -> tuple[QuestionSeed, ...]:
    """返回全部待确认事项及其状态。

    没有配 marker 的模板 → 保持 ``open``（**不假装已确认**）：
    这意味着「这个问题我们还没有自动确认的手段」，与 ``unverifiable``
    （「数据源根本没有」）是两件事，但都属于「未确认」。
    """
    unverifiable = unverifiable or {}
    seeds: list[QuestionSeed] = []
    for question in templates_for(thesis_type):
        if question in unverifiable:
            seeds.append(QuestionSeed(
                question=question, status="unverifiable", detail=unverifiable[question],
            ))
            continue
        spec = markers.get(question)
        event = _confirming_event(facts, spec) if spec else None
        if event is None:
            seeds.append(QuestionSeed(question=question, status="open"))
        else:
            seeds.append(QuestionSeed(
                question=question,
                status="confirmed",
                confirmed_by_event_id=event.id,
                detail=f"依据：{event.title or event.event_type.value}",
            ))
    return tuple(seeds)


def open_questions(
    facts: StrategyFacts,
    thesis_type: ThesisType | str,
    markers: MarkerMap,
    unverifiable: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """未确认的事项 —— ``unverifiable`` 也算未确认。

    ★ 不能把「无法判定」当成「已确认」：那等于用沉默冒充证据。
    """
    seeds = evaluate_questions(facts, thesis_type, markers, unverifiable)
    return tuple(s.question for s in seeds if s.status != "confirmed")


__all__ = ["MarkerMap", "QuestionSeed", "evaluate_questions", "open_questions",
           "templates_for"]
