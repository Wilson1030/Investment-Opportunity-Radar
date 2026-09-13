"""AI 分析阶段：把 ``hunt_risk`` → ``analyze`` → ``score_semantic`` 接进机会。

## 为什么需要这个模块

阶段 3 就写好了这 3 个节点（含 Schema 与 prompt），但**从未接进 pipeline** ——
实测后果：6 张真实机会卡的 ``summary`` 全空、``semantic_score`` / ``divergence``
全是 ``None``。也就是说：

* **卡片的「AI 判断」是空白** —— 而「告诉我为什么值得关注」正是产品的核心承诺
* **双分制（D08）只有一半** —— 规则分在算，语义分从未产生，分歧提示也不存在
* **「主动找反证」（规格 §51）没落实** —— ``risks`` 只来自规则里的风险因素，
  没有对 Thesis 的反向检索

前端其实早就留好了位置（``ai_judgement`` 区块、``DivergenceBadge``），
只是没数据可显示。本模块负责把数据填进去。

## 顺序为什么是 风险 → 叙事 → 语义分

* ``hunt_risk`` 先跑：它产出的 ``risks`` 与 ``open_questions`` 是 ``analyze``
  的输入（叙事必须包含不确定性，不能只见利好）
* ``analyze`` 再跑：生成 summary / why_now / uncertainties / next_watch
* ``score_semantic`` 最后跑：它要拿到**规则分与规则明细**，才能只补规则没覆盖的部分
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.ai.nodes import ANALYZE, HUNT_RISK, SCORE_SEMANTIC
from app.ai.runner import NodeRunner
from app.ai.schemas import (
    AnalyzeInput,
    EvidenceBrief,
    HuntRiskInput,
    RuleItemBrief,
    ScoreSemanticInput,
)
from app.engine.scoring import (
    RuleScoreResult,
    compute_divergence,
    is_divergence_flagged,
)
from app.facts import StrategyFacts
from app.models.enums import ReliabilityLevel
from app.models.evidence import Evidence
from app.models.opportunity import Opportunity


@dataclass
class AiAnalysis:
    """一次 AI 分析的产物（尚未落库）。"""

    summary: str = ""
    why_now: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_events_to_watch: list[str] = field(default_factory=list)
    contradictory_evidence_ids: list[int] = field(default_factory=list)
    semantic_score: float | None = None
    divergence: float | None = None
    divergence_flagged: bool = False
    semantic_factors: list[dict] = field(default_factory=list)
    rules_already_covered: list[str] = field(default_factory=list)
    #: 各节点的执行状态（失败时不阻塞机会生成，但必须可诊断）
    node_status: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.summary) or self.semantic_score is not None


def _evidence_briefs(session: Session, evidence_ids: list[int]) -> list[EvidenceBrief]:
    if not evidence_ids:
        return []
    rows = session.exec(select(Evidence).where(Evidence.id.in_(evidence_ids))).all()  # type: ignore[attr-defined]
    briefs: list[EvidenceBrief] = []
    for row in rows:
        briefs.append(EvidenceBrief(
            id=int(row.id or 0),
            reliability_level=ReliabilityLevel(row.reliability_level),
            relevant_text=(row.relevant_text or "")[:600],
            publication_time=row.publication_time,
        ))
    return briefs


def _company_history(facts: StrategyFacts) -> dict:
    """给 ``hunt_risk`` 的历史事实 —— 反证检索需要知道「过去发生过什么」。"""
    return {
        "name": facts.company.name,
        "is_st": facts.company.is_st,
        "industry": facts.company.industry,
        "loss_years": facts.financials.loss_years,
        "profitable_years": facts.financials.profitable_years,
        "ocf_positive": facts.financials.ocf_positive,
        "ocf_is_proxy": facts.financials.ocf_is_proxy,
        "debt_ratio_rising": facts.financials.debt_ratio_rising,
        "receivable_growth_exceeds_revenue": facts.financials.receivable_growth_exceeds_revenue,
        "has_history_failure": facts.has_history_failure,
        "has_unanswered_inquiry": facts.has_unanswered_inquiry,
        "has_late_stage_pending_approval": facts.has_late_stage_pending_approval,
        "open_question_count": facts.open_question_count,
        "event_titles": [e.title for e in facts.events][:12],
    }


def analyze_opportunity(
    session: Session,
    opportunity: Opportunity,
    facts: StrategyFacts,
    thesis_statement: str,
    score: RuleScoreResult,
    runner: NodeRunner,
    *,
    rule_next_watch: list[str] | None = None,
) -> AiAnalysis:
    """对单个机会跑完整的 AI 分析链路。

    **任何一步失败都不阻塞机会生成** —— 记下 ``node_status`` 继续走，
    但没有 AI 叙事的机会必须在报告里可见（不能静默降级）。
    """
    result = AiAnalysis()
    evidence = _evidence_briefs(session, list(opportunity.supporting_evidence_ids or []))
    thesis_type = score.thesis_type

    # ---------- 1) 主动找反证（规格 §51）----------
    hunt_input = HuntRiskInput(
        thesis_type=thesis_type,          # type: ignore[arg-type]
        thesis_statement=thesis_statement,
        evidence=evidence,
        company_history=_company_history(facts),
    )
    hunt = runner.run(HUNT_RISK, hunt_input)
    result.node_status["hunt_risk"] = hunt.status.value
    if hunt.ok and hunt.output is not None:
        result.risks = list(hunt.output.risks)
        result.uncertainties = list(hunt.output.open_questions)
        for item in hunt.output.contradictory_evidence:
            for evidence_id in item.evidence_ids:
                if evidence_id not in result.contradictory_evidence_ids:
                    result.contradictory_evidence_ids.append(evidence_id)
        if item := hunt.output.no_contradiction_statement:
            # 明确「已检查但未发现反证」也是结论，必须留痕而不是空着
            result.risks.append(f"（反证检索结论）{item}")

    # ---------- 2) 研究叙事 ----------
    analyze_input = AnalyzeInput(
        thesis_type=thesis_type,          # type: ignore[arg-type]
        thesis_statement=thesis_statement,
        evidence=evidence,
        risks=result.risks,
        company_facts=_company_history(facts),
        open_questions=result.uncertainties,
    )
    narrative = runner.run(ANALYZE, analyze_input)
    result.node_status["analyze"] = narrative.status.value
    if narrative.ok and narrative.output is not None:
        result.summary = narrative.output.summary
        result.why_now = list(narrative.output.why_now)
        # 不确定性取并集：反证节点与叙事节点都可能发现
        for item in narrative.output.uncertainties:
            if item not in result.uncertainties:
                result.uncertainties.append(item)
        # 「下一步观察什么」= 规则阶梯（确定性）+ AI 补充（deal 特定节点）
        merged: list[str] = list(rule_next_watch or [])
        for item in narrative.output.next_events_to_watch:
            if item not in merged:
                merged.append(item)
        result.next_events_to_watch = merged

    # ---------- 3) 语义分（只补规则未覆盖的因素，D08）----------
    rule_items = [
        RuleItemBrief(dimension=dimension.value, delta=hit.delta, reason=hit.reason)
        for dimension, hit in score.score_items
    ]
    semantic_input = ScoreSemanticInput(
        thesis_type=thesis_type,          # type: ignore[arg-type]
        rule_score=score.rule_score,
        rule_score_items=rule_items,
        thesis_statement=thesis_statement,
        evidence=evidence,
        company_facts=_company_history(facts),
    )
    semantic = runner.run(SCORE_SEMANTIC, semantic_input)
    result.node_status["score_semantic"] = semantic.status.value
    if semantic.ok and semantic.output is not None:
        result.semantic_score = semantic.output.semantic_score
        result.divergence = compute_divergence(score.rule_score, result.semantic_score)
        result.divergence_flagged = is_divergence_flagged(result.divergence)
        result.semantic_factors = [f.model_dump() for f in semantic.output.factors]
        result.rules_already_covered = list(semantic.output.rules_already_covered)

    return result


__all__ = ["AiAnalysis", "analyze_opportunity"]
