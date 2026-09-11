"""节点 5 · ``score_semantic`` —— LLM 语义分（D08 辅分）。

**这个节点被允许评估的范围被刻意限制得很窄**：只补规则没覆盖到的因素。
prompt 里会明确列出规则已经算了什么，并要求模型自述 ``rules_already_covered``，
从机制上防止同一因素被计两次。

语义分**不参与排序**，只在详情页展示，用于在分歧 >20 时提示人工复核（docs/04 §10）。
"""

from __future__ import annotations

from app.ai.nodes.base import PromptNode
from app.ai.prompts import PROMPT_VERSIONS, SCORE_SEMANTIC_RULES
from app.ai.schemas import ScoreSemanticInput, ScoreSemanticOutput

#: 规则已经覆盖的因素清单 —— 直接渲染进 prompt，告诉模型「这些不用你再算」
RULE_COVERED_FACTORS: tuple[str, ...] = (
    "事件类型命中（是否为核心事件类型）",
    "公告数量与事件类型数量（组合催化）",
    "事件金额规模阈值",
    "证据等级（A/B/C/D/E）与是否官方披露",
    "ST / 风险警示状态",
    "财务状况（营收、毛利率、经营现金流、应收账款、资产负债率）",
    "股东结构（控制权变更、增持、回购、质押、减持）",
    "市场关注度（报道条数、异动公告、龙虎榜、研报覆盖、社交讨论）",
    "时效衰减（信息新鲜度）",
    "待确认事项数量、问询函、历史失败记录",
)


def _render_rules() -> str:
    covered = "\n".join(f"  - {item}" for item in RULE_COVERED_FACTORS)
    return (
        SCORE_SEMANTIC_RULES.replace("{covered}", covered)
        .replace("{rule_score}", "【见下方输入数据中的 rule_score 字段】")
    )


class ScoreSemanticNode(PromptNode):
    name = "score_semantic"
    prompt_version = PROMPT_VERSIONS["score_semantic"]
    layer = "analyze"
    Input = ScoreSemanticInput
    Output = ScoreSemanticOutput
    rules = _render_rules()


NODE = ScoreSemanticNode()

__all__ = ["NODE", "RULE_COVERED_FACTORS", "ScoreSemanticNode"]
