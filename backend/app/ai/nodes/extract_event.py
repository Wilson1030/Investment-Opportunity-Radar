"""节点 1 · ``extract_event`` —— 公告文本 → 结构化事件（docs/03 §4.2）。

用抽取层模型（量大、任务简单、要求 JSON 稳定），默认本机 Ollama ``qwen3:4b``。
"""

from __future__ import annotations

from app.ai.nodes.base import PromptNode
from app.ai.prompts import EXTRACT_EVENT_RULES, PROMPT_VERSIONS
from app.ai.schemas import ExtractEventInput, ExtractEventOutput


class ExtractEventNode(PromptNode):
    name = "extract_event"
    prompt_version = PROMPT_VERSIONS["extract_event"]
    layer = "extract"
    Input = ExtractEventInput
    Output = ExtractEventOutput
    rules = EXTRACT_EVENT_RULES


NODE = ExtractEventNode()

__all__ = ["NODE", "ExtractEventNode"]
