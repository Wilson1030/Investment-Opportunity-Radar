"""节点 4 · ``analyze`` —— 研究卡叙事字段（docs/03 §4.5，规格 §25 / §52）。

产出 ``summary`` / ``why_now`` / ``uncertainties`` / ``next_events_to_watch``，
并标注每段是事实还是推断。
"""

from __future__ import annotations

from app.ai.nodes.base import PromptNode
from app.ai.prompts import ANALYZE_RULES, PROMPT_VERSIONS
from app.ai.schemas import AnalyzeInput, AnalyzeOutput


class AnalyzeNode(PromptNode):
    name = "analyze"
    prompt_version = PROMPT_VERSIONS["analyze"]
    layer = "analyze"
    Input = AnalyzeInput
    Output = AnalyzeOutput
    rules = ANALYZE_RULES


NODE = AnalyzeNode()

__all__ = ["NODE", "AnalyzeNode"]
