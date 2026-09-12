"""ORM → API 响应结构的序列化（docs/05 契约）。

集中在这里而不是散落在各路由，目的是让 ``test_api_contract.py`` 的快照测试
有单一落点：响应结构变化时只需改这一处。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine import freshness
from app.models.enums import (
    DIMENSION_LABELS,
    EventType,
    OpportunityStatus,
    ReliabilityLevel,
    ScoreDimension,
    ThesisType,
)
from app.models.evidence import Evidence
from app.models.events import Event
from app.models.knowledge import Company, Stock
from app.models.opportunity import (
    Alert,
    OpenQuestion,
    Opportunity,
    OpportunityScore,
    ScoreItem,
)
from app.models.thesis import Thesis
from app.strategies import get_def

#: 状态中文标签（UI 直接用）
STATUS_LABELS: dict[OpportunityStatus, str] = {
    OpportunityStatus.DISCOVERED: "发现",
    OpportunityStatus.PENDING_CONFIRMATION: "待确认",
    OpportunityStatus.TRACKING: "重点跟踪",
    OpportunityStatus.THESIS_CONFIRMED: "逻辑成立",
    OpportunityStatus.OBSERVING: "观察",
    OpportunityStatus.INVALIDATED: "逻辑失效",
    OpportunityStatus.ARCHIVED: "归档",
}

EVENT_LABELS: dict[EventType, str] = {
    EventType.M_AND_A: "并购",
    EventType.RESTRUCTURING: "重大资产重组",
    EventType.ASSET_INJECTION: "资产注入",
    EventType.CONTROL_CHANGE: "控制权变更",
    EventType.SHAREHOLDER_BUY: "股东增持",
    EventType.SHAREHOLDER_SELL: "股东减持",
    EventType.BUYBACK: "公司回购",
    EventType.BANKRUPTCY_REORGANIZATION: "破产重整",
    EventType.EARNINGS_TURNAROUND: "业绩拐点",
    EventType.POLICY_CATALYST: "政策催化",
    EventType.MAJOR_CONTRACT: "重大合同",
    EventType.NEW_PRODUCT: "新产品 / 技术突破",
    EventType.MANAGEMENT_CHANGE: "管理层变化",
    EventType.REGULATORY_RISK: "监管风险",
    EventType.LITIGATION: "重大诉讼",
    EventType.DIVIDEND_POLICY: "分红政策",
    EventType.OTHER: "其他",
}

RELIABILITY_NOTES: dict[ReliabilityLevel, str] = {
    ReliabilityLevel.A: "公司正式公告 / 交易所披露",
    ReliabilityLevel.B: "公司财报 / 官方文件",
    ReliabilityLevel.C: "高可信媒体",
    ReliabilityLevel.D: "机构 / 研究观点",
    ReliabilityLevel.E: "社交媒体 / 市场讨论",
}


def _v(value):
    """安全取枚举值。

    SQLModel 的 JSON / 字符串列从数据库读回来是 ``str``（不是枚举实例），
    而策略注册表里的字段是真枚举 —— 两种情况都要能序列化。
    """
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _age_days(moment: datetime | None) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 86400)


def company_brief(company: Company | None, stock: Stock | None = None) -> dict:
    if company is None:
        return {}
    return {
        "id": company.id,
        "name": company.name,
        "code": stock.code if stock else "",
        "industry": company.industry,
        "is_st": company.is_st,
    }


def event_brief(event: Event) -> dict:
    age = _age_days(event.event_time)
    return {
        "id": event.id,
        "event_type": _v(event.event_type),
        "event_type_label": EVENT_LABELS.get(event.event_type, event.event_type.value),
        "title": event.title,
        "summary": event.summary,
        "event_time": event.event_time.isoformat() if event.event_time else None,
        "discovery_time": event.discovery_time.isoformat() if event.discovery_time else None,
        "importance": event.importance,
        "certainty": event.certainty,
        "certainty_level": _v(event.certainty_level),
        "source_type": _v(event.source_type),
        "source_url": event.source_url,
        "affected_thesis": [_v(t) for t in event.affected_thesis],
        "evidence_ids": event.evidence_ids,
        "is_invalidating": event.is_invalidating,
        "freshness": freshness.freshness_tag(age).value,
        "relative_time": freshness.relative_time(age),
        "time_note": f"事件发生时间 {freshness.relative_time(age)}",
    }


def deep_link(evidence: Evidence) -> str:
    """带**页码锚点**的原文链接。

    ★ 不加锚点的话，点击只会停在 PDF 第 1 页 —— 而 cninfo 的重整/重组公告
    动辄几十上百页，用户在文档里根本找不到被引用的那句话，
    「跳转原文」就等于失效（实测反馈：跳转都是错的）。

    浏览器 PDF 阅读器（Chrome / Edge / Firefox）都支持 #page=N。
    """
    url = evidence.source_url or ""
    if not url or not evidence.page or "#" in url:
        return url
    return f"{url}#page={int(evidence.page)}"


def evidence_detail(evidence: Evidence) -> dict:
    age = _age_days(evidence.publication_time)
    return {
        "id": evidence.id,
        "source_type": _v(evidence.source_type),
        "source_name": evidence.source_name,
        "source_url": evidence.source_url,
        #: 可直接打开的深链（含页码锚点）—— 前端应优先用它
        "source_deep_link": deep_link(evidence),
        "publication_time": evidence.publication_time.isoformat(),
        "reliability_level": _v(evidence.reliability_level),
        "reliability_note": RELIABILITY_NOTES.get(evidence.reliability_level, ""),
        "assertion_kind": _v(evidence.assertion_kind),
        "document_id": evidence.document_id,
        "relevant_text": evidence.relevant_text,
        "page": evidence.page,
        "para_index": evidence.para_index,
        "extracted_facts": evidence.extracted_facts,
        "confidence": evidence.confidence,
        "freshness": freshness.freshness_tag(age).value,
        "relative_time": freshness.relative_time(age),
    }


def opportunity_card(
    opportunity: Opportunity,
    company: Company | None = None,
    stock: Stock | None = None,
    events: list[Event] | None = None,
    *,
    thesis_type: ThesisType | None = None,
    evidence_levels: list[ReliabilityLevel] | None = None,
    a_grade_count: int = 0,
    risk_count: int = 0,
    open_question_count: int | None = None,
) -> dict:
    definition = get_def(thesis_type) if thesis_type else None
    status = OpportunityStatus(_v(opportunity.status))
    evidence_levels = evidence_levels or []
    hard = any(_v(lv) in ("A", "B") for lv in evidence_levels)

    return {
        "id": opportunity.id,
        "company": company_brief(company, stock),
        "thesis_type": _v(definition.code) if definition else None,
        "thesis_display_name": definition.display_name if definition else None,
        "status": _v(status),
        "status_label": STATUS_LABELS.get(OpportunityStatus(_v(status)), _v(status)),
        "match_score": opportunity.match_score,
        "rule_score": opportunity.rule_score,
        "risk_score": opportunity.risk_score,
        "semantic_score": opportunity.semantic_score,
        "divergence": opportunity.divergence,
        "divergence_flagged": bool(
            opportunity.divergence is not None and opportunity.divergence > 20
        ),
        "why_in_radar": opportunity.why_in_radar or opportunity.why_now,
        # ★ 阶段必须出现在卡片上：用户要「提前布局」，但把苗头当确定的事会误导决策
        "catalyst_stage": opportunity.catalyst_stage,
        "is_early_signal": opportunity.is_early_signal,
        "summary": opportunity.summary,
        "latest_events": [event_brief(e) for e in (events or [])][:5],
        "ai_judgement": opportunity.summary,
        "evidence_count": len(opportunity.supporting_evidence_ids),
        "contradictory_count": len(opportunity.contradictory_evidence_ids),
        "open_question_count": (
            open_question_count if open_question_count is not None
            else len(opportunity.uncertainties)
        ),
        "risk_count": risk_count or len(opportunity.risks),
        "a_grade_evidence_count": a_grade_count,
        # ★ M4-04：支撑证据中没有 A/B 类 → 前端强制显示「仅市场讨论，未经证实」
        "only_market_discussion": bool(evidence_levels) and not hard,
        "next_events_to_watch": opportunity.next_events_to_watch,
        "risks": opportunity.risks,
        "score_version": opportunity.score_version,
        "first_discovered_at": opportunity.first_discovered_at.isoformat(),
        "last_updated_at": opportunity.last_updated_at.isoformat(),
    }


def score_breakdown(
    opportunity: Opportunity,
    dimension_rows: list[OpportunityScore],
    item_rows: list[ScoreItem],
) -> dict:
    """可解释性的核心结构（docs/05 §3.4 / §7 的两级展开）。"""
    items_by_dimension: dict[ScoreDimension, list[ScoreItem]] = {}
    for item in item_rows:
        items_by_dimension.setdefault(ScoreDimension(_v(item.dimension)), []).append(item)

    dimensions: list[dict] = []
    for row in sorted(dimension_rows,
                      key=lambda r: _dimension_order(ScoreDimension(_v(r.dimension)))):
        dimension = ScoreDimension(_v(row.dimension))
        is_risk = dimension is ScoreDimension.RISK
        entry = {
            "dimension": dimension.value,
            "display_name": DIMENSION_LABELS[dimension],
            "raw_value": row.raw_value,
            "weight": row.weight,
            "weighted_value": row.weighted_value,
            "direction": "negative" if is_risk else "positive",
            "items": [
                {
                    "rule_id": item.rule_id,
                    "delta": item.delta,
                    "reason": item.reason,
                    "evidence_ids": item.evidence_ids,
                }
                for item in items_by_dimension.get(dimension, [])
            ],
        }
        if is_risk:
            entry["direction_note"] = "★ 数值越高代表风险越大（与上方维度方向相反）"
        dimensions.append(entry)

    return {
        "rule_score": opportunity.rule_score,
        "semantic_score": opportunity.semantic_score,
        "divergence": opportunity.divergence,
        "divergence_flagged": bool(
            opportunity.divergence is not None and opportunity.divergence > 20
        ),
        "risk_score": opportunity.risk_score,
        "score_version": opportunity.score_version,
        "dimensions": dimensions,
    }


def _dimension_order(dimension: ScoreDimension) -> int:
    order = [
        ScoreDimension.THESIS_MATCH,
        ScoreDimension.EVENT_CATALYST,
        ScoreDimension.CATALYST_STRENGTH,
        ScoreDimension.CERTAINTY,
        ScoreDimension.FUNDAMENTALS,
        ScoreDimension.SHAREHOLDER_STRUCTURE,
        ScoreDimension.MARKET_ATTENTION,
        ScoreDimension.HISTORY_CASE,
        ScoreDimension.RISK,
    ]
    return order.index(dimension) if dimension in order else 99


def thesis_detail(thesis: Thesis, evidence: list[Evidence], contradictions: list[Evidence]) -> dict:
    definition = get_def(ThesisType(_v(thesis.thesis_type)))
    return {
        "id": thesis.id,
        "thesis_type": definition.code.value,
        "display_name": definition.display_name,
        "statement": thesis.statement,
        "why_now": {
            "past": thesis.why_now_past,
            "recent": thesis.why_now_recent,
            "this_week": thesis.why_now_this_week,
            "conclusion": thesis.why_now_conclusion,
        },
        "supporting_evidence": [evidence_detail(e) for e in evidence],
        "contradictory_evidence": [evidence_detail(e) for e in contradictions],
        # ★ M5-03：每个 Thesis 必须定义失效条件
        "invalidating_events": [
            {
                "event_type": d.event_type.value,
                "severity": d.severity.value,
                "description": d.description,
            }
            for d in definition.invalidating_events
        ],
        "open_questions": [q.question for q in ()],
    }
def open_question_detail(question: OpenQuestion) -> dict:
    return {
        "id": question.id,
        "question": question.question,
        "status": question.status,
        "confirmed_evidence_id": question.confirmed_evidence_id,
    }


def alert_detail(alert: Alert) -> dict:
    age = _age_days(alert.created_at)
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "title": alert.title,
        "message": alert.message,
        "suggestion": alert.suggestion,
        "opportunity_id": alert.opportunity_id,
        "score_before": alert.score_before,
        "score_after": alert.score_after,
        "triggered_by_event_id": alert.triggered_by_event_id,
        "is_read": alert.is_read,
        "created_at": alert.created_at.isoformat(),
        "relative_time": freshness.relative_time(age),
    }


def strategy_detail(definition) -> dict:
    return {
        "code": definition.code.value,
        "display_name": definition.display_name,
        "status": definition.status.value,
        "user_goal": definition.user_goal,
        "description": definition.description,
        "support_event_types": [t.value for t in definition.support_event_types],
        "invalidating_event_types": sorted(
            {d.event_type.value for d in definition.invalidating_events}
        ),
        "core_condition_count": len(definition.core_conditions),
        "core_conditions": [
            {"key": c.key, "label": c.label, "weight": c.weight} for c in definition.core_conditions
        ],
        "open_question_templates": list(definition.open_question_templates),
        "catalyst_ladder": [
            {"stage": s.stage, "score": s.score} for s in definition.catalyst_ladder
        ],
        "default_weights": {k.value: v for k, v in definition.default_weights.items()},
        "risk_factors": [
            {"key": r.key, "label": r.label, "weight": r.weight} for r in definition.risk_factors
        ],
        "anti_patterns": list(definition.anti_patterns),
    }


__all__ = [
    "EVENT_LABELS",
    "RELIABILITY_NOTES",
    "STATUS_LABELS",
    "alert_detail",
    "company_brief",
    "event_brief",
    "evidence_detail",
    "open_question_detail",
    "opportunity_card",
    "score_breakdown",
    "strategy_detail",
    "thesis_detail",
]
