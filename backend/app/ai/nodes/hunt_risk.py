"""节点 3 · ``hunt_risk`` —— 主动寻找反证（docs/03 §4.4，规格 §51）。

> 系统不能只寻找支持 Thesis 的证据，还必须主动寻找**反证**。

用分析层模型（需要推理），并要求 ``contradictory_evidence`` 字段**必须存在**。
"""

from __future__ import annotations

from app.ai.nodes.base import PromptNode
from app.ai.prompts import HUNT_RISK_RULES, PROMPT_VERSIONS
from app.ai.schemas import HuntRiskInput, HuntRiskOutput


class HuntRiskNode(PromptNode):
    name = "hunt_risk"
    prompt_version = PROMPT_VERSIONS["hunt_risk"]
    layer = "analyze"
    Input = HuntRiskInput
    Output = HuntRiskOutput
    rules = HUNT_RISK_RULES


NODE = HuntRiskNode()

__all__ = ["NODE", "HuntRiskNode"]
