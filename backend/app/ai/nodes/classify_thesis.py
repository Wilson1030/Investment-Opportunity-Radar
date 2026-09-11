"""节点 2 · ``classify_thesis`` —— 事件 → 可能命中的投资逻辑（docs/03 §4.3）。

只把 Rule Engine 预筛过的候选策略定义交给模型，不让它看到全部 10 类
（减少小模型的选择负担，也避免它「发明」不存在的逻辑类型）。
"""

from __future__ import annotations

from app.ai.nodes.base import PromptNode
from app.ai.prompts import CLASSIFY_THESIS_RULES, PROMPT_VERSIONS
from app.ai.schemas import ClassifyThesisInput, ClassifyThesisOutput


class ClassifyThesisNode(PromptNode):
    name = "classify_thesis"
    prompt_version = PROMPT_VERSIONS["classify_thesis"]
    layer = "extract"
    Input = ClassifyThesisInput
    Output = ClassifyThesisOutput
    rules = CLASSIFY_THESIS_RULES


NODE = ClassifyThesisNode()

__all__ = ["NODE", "ClassifyThesisNode"]
