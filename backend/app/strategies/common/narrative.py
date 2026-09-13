"""泛化的 Thesis 语句与 Why Now（规格 §21 / §52）。

规格 §21：不要把用户保存成「自选股：ST XXX」，而应保存成
**「因为重组预期，所以关注 ST XXX」**。本模块负责生成那句「因为……」。
规格 §52 进一步固定了 Why Now 的四段结构：
``past`` / ``recent`` / ``this_week`` / ``conclusion``。

## 为什么用「叙事三要素」而不是每策略一个模板字符串

10 类策略的措辞差异集中在三件事上：

  ``trigger``  发生了什么（「出现并购 / 收购事件」）
  ``support``  支撑是什么（「标的与上市公司存在业务协同」）
  ``caveat``   还差什么（「交易价格与标的盈利能力尚待确认」）

给每类策略写这三句，比给每类写一整套模板字符串更短、也更难写错
（不会出现「说了改善却忘了说持续性」这种缺项）。

★ 硬性约束：``caveat`` 不得为空。规格示例 B 明确禁止
「业绩大涨，所以看好」这类**只有结论**的表达 ——
每个 Thesis 都必须同时给出「尚未确认」的部分。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.facts import StrategyFacts
from app.strategies.base import StrategyEvaluation

#: 叙事三要素的求值函数
NarrativeFn = Callable[[StrategyFacts], str]


@dataclass(frozen=True)
class Narrative:
    """一类策略的叙事三要素。"""

    subject: str          # 「重组预期」「困境反转」…
    trigger: NarrativeFn  # 发生了什么
    support: NarrativeFn  # 支撑是什么
    caveat: str           # 还差什么（**不得为空**）


def _latest_event_title(facts: StrategyFacts, limit: int = 40) -> str | None:
    ordered = sorted(
        [e for e in facts.events if e.event_time],
        key=lambda e: e.event_time,
        reverse=True,
    )
    if not ordered:
        return None
    return (ordered[0].title or ordered[0].event_type.value)[:limit]


def build_statement(
    facts: StrategyFacts, evaluation: StrategyEvaluation, narrative: Narrative
) -> str:
    """生成 Thesis 语句：``{trigger}，{support}，因此构成{subject}；{caveat}``。"""
    trigger = narrative.trigger(facts)
    support = narrative.support(facts)
    parts = [p for p in (trigger, support) if p]
    body = "，".join(parts)
    return f"{body}，因此构成{narrative.subject}；{narrative.caveat}"


def why_now(facts: StrategyFacts, narrative: Narrative) -> dict[str, str]:
    """Why Now 四段（规格 §52 的固定叙事结构）。"""
    latest = _latest_event_title(facts)
    past = narrative.trigger(facts)
    recent = narrative.support(facts)
    return {
        "past": past or "历史数据见财务页与公告列表",
        "recent": recent or "近期未见与该逻辑相关的新增信息",
        "this_week": f"最新事件：{latest}" if latest else "本周无新增相关公告",
        "conclusion": (
            f"因此该公司进入「{narrative.subject}」机会池；{narrative.caveat}"
        ),
    }


__all__ = ["Narrative", "NarrativeFn", "build_statement", "why_now"]
