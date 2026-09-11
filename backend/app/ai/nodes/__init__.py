"""AI 节点集（5 个纯函数节点，docs/03 §4）。

    extract_event      公告文本 → 结构化事件          （抽取层）
    classify_thesis    事件 → 可能命中的 Thesis 类型   （抽取层）
    hunt_risk          主动寻找反证                    （分析层）
    analyze            研究卡叙事字段                  （分析层）
    score_semantic     仅补规则未覆盖因素的语义分       （分析层）

**所有节点都是「构造 prompt + 声明 Schema」，不做任何副作用。**
LLM 调用 / 缓存 / 重试 / 审计 / 落库全部在 :mod:`app.ai.runner` 中。
"""

from app.ai.nodes.analyze import NODE as ANALYZE
from app.ai.nodes.base import PromptNode
from app.ai.nodes.classify_thesis import NODE as CLASSIFY_THESIS
from app.ai.nodes.extract_event import NODE as EXTRACT_EVENT
from app.ai.nodes.hunt_risk import NODE as HUNT_RISK
from app.ai.nodes.score_semantic import NODE as SCORE_SEMANTIC

#: 全量节点表（按流水线顺序）
ALL_NODES = (EXTRACT_EVENT, CLASSIFY_THESIS, HUNT_RISK, ANALYZE, SCORE_SEMANTIC)

#: 名称 → 节点
NODE_BY_NAME = {node.name: node for node in ALL_NODES}

#: 抽取层节点（用量大，走 EXTRACT_* 配置）
EXTRACT_NODES = tuple(n for n in ALL_NODES if n.layer == "extract")

#: 分析层节点（用量小，走 ANALYZE_* 配置）
ANALYZE_NODES = tuple(n for n in ALL_NODES if n.layer == "analyze")

__all__ = [
    "ALL_NODES",
    "ANALYZE",
    "ANALYZE_NODES",
    "CLASSIFY_THESIS",
    "EXTRACT_EVENT",
    "EXTRACT_NODES",
    "HUNT_RISK",
    "NODE_BY_NAME",
    "PromptNode",
    "SCORE_SEMANTIC",
]
