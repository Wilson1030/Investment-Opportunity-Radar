"""ORM → API 响应结构的序列化（docs/05 契约）。

集中在这里而不是散落在各路由，目的是让 ``test_api_contract.py`` 的快照测试
有单一落点：响应结构变化时只需改这一处。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine import freshness
from sqlmodel import Session, select

from app.models.knowledge import FinancialMetric, FinancialPeriod, ValuationSnapshot
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



# --------------------------------------------------------------------------- #
# 财务数据（契约见 docs/05-API契约.md §3.3 的 ``financials``）
# --------------------------------------------------------------------------- #
#: 金额类指标：库里以「元」存储，对外按「亿元」显示
#: （契约如此；也让「20.1 亿元」比「2010000000」可读得多）
_MONEY_METRICS = frozenset({"revenue", "net_profit", "ocf"})
_YI = 1e8


def _metric_entry(metric: str, m: FinancialMetric) -> dict:
    """单个指标 → 契约形状 ``{value, unit?, yoy, is_anomaly?, anomaly_note?}``。"""
    entry: dict = {}
    if m.value is None:
        entry["value"] = None
    elif metric in _MONEY_METRICS:
        entry["value"] = round(m.value / _YI, 4)
        entry["unit"] = "亿元"
    else:
        entry["value"] = round(m.value, 6)
        # 无量纲比率**不给 unit** —— 硬塞「%」会让 0.18 被读成 0.18%
        if m.unit:
            entry["unit"] = m.unit
    entry["yoy"] = round(m.yoy, 6) if m.yoy is not None else None
    # INV-F1：指标恶化必须带归因才算「可解释」
    if m.is_anomaly:
        entry["is_anomaly"] = True
        entry["anomaly_note"] = m.anomaly_note
    return entry


def financial_series(
    session: Session, company_id: int, *, limit: int = 8
) -> list[dict]:
    """按报告期**倒序**（最新在前）返回结构化财务。

    ``period`` 用报告期标签（``2026H1`` / ``2025A``）而不是日期 ——
    看 ``2026-06-30`` 容易误以为是「某一天的数据」。
    """
    from app.ingest.financials import METRIC_ORDER, period_label

    periods = list(session.exec(
        select(FinancialPeriod)
        .where(FinancialPeriod.company_id == company_id)
        .order_by(FinancialPeriod.period_end.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all())
    if not periods:
        return []

    period_ids = [int(p.id or 0) for p in periods]
    metrics = session.exec(
        select(FinancialMetric).where(FinancialMetric.period_id.in_(period_ids))  # type: ignore[attr-defined]
    ).all()
    grouped: dict[int, list[FinancialMetric]] = {pid: [] for pid in period_ids}
    for m in metrics:
        grouped.setdefault(int(m.period_id), []).append(m)

    rank = {name: i for i, name in enumerate(METRIC_ORDER)}
    out: list[dict] = []
    for p in periods:
        rows = sorted(
            grouped.get(int(p.id or 0), []),
            key=lambda m: (rank.get(m.metric, len(rank)), m.metric),
        )
        raw_period = str(p.period)
        label = (
            period_label(p.period_end.isoformat())
            if raw_period[:4].isdigit() and "-" in raw_period
            else raw_period
        )
        out.append({
            "period": label,
            "period_end": p.period_end.isoformat(),
            "report_type": p.report_type,
            "metrics": {m.metric: _metric_entry(m.metric, m) for m in rows},
        })
    return out


def market_layer(session: Session, company_id: int, *, note: str) -> dict:
    """辅助信息层（规格 §46）：估值快照 + 固定说明。

    ★ 为什么放在「辅助层」而不是首页主体：规格 §46 明确要求
    行情 / 估值不得成为机会发现的主体 —— 它是**事后观察**工具。
    所以这里连字段名都保持中性（``percentile`` 越小越便宜，
    而不是「低估 / 高估」这种带结论的词）。
    """
    row = session.exec(
        select(ValuationSnapshot)
        .where(ValuationSnapshot.company_id == company_id)
        .order_by(ValuationSnapshot.as_of.desc())  # type: ignore[attr-defined]
    ).first()
    payload: dict = {"note": note}
    if row is None:
        payload["valuation"] = None
        payload["valuation_note"] = (
            "未采集到估值数据 —— 因此「估值是否处于历史低位」无法判断"
            "（缺失就是不显示，不用估算值填充）"
        )
        return payload

    payload["valuation"] = {
        "as_of": row.as_of.isoformat(),
        "market_cap": row.market_cap,
        "market_cap_unit": "亿元",
        "pe_ttm": row.pe_ttm,
        "pb": row.pb,
        "pe_percentile": row.pe_percentile,
        "pb_percentile": row.pb_percentile,
        "window_days": row.window_days,
        "source_name": row.source_name,
        "source_url": row.source_url,
        "direction_note": "分位越小表示估值越低（0 = 窗口内最便宜）",
    }
    return payload


def _signal(
    key: str, label: str, value_text: str, held: bool, impact: str, *,
    adverse_when_held: bool = True,
) -> dict:
    """把「规则条件是否成立」渲染成一条可读信号。

    ``tone`` 由**双向**决定，而不是只看条件是否成立：
    「经营现金流不为正」不成立 = 现金流为正 = 好消息（绿），
    而「现金流同比改善」不成立只是**没有好消息**（灰），不是坏消息。
    """
    if held:
        tone = "bad" if adverse_when_held else "good"
    else:
        tone = "good" if adverse_when_held else "muted"
    return {
        "key": key,
        "label": label,
        "value_text": value_text,
        "held": held,
        "impact": impact,
        "tone": tone,
    }


def financial_signals(facts) -> list[dict]:
    """财务事实 → 可读信号列表。

    ``key`` 与 :mod:`app.engine.rules` 里的规则键一致 ——
    这样前端每一行都能追溯到「它对应 C4 / RISK 的哪条规则」，
    而不是给一个无法核对的数字（规格 M6-03 可解释性）。

    ★ ``impact`` 一律写成「影响哪个维度」这种**中性**陈述。
    早先写成「推高 RISK」这种带方向的句子，当条件不成立（tone=绿）时
    就变成了自相矛盾的显示 —— 方向必须只由 ``tone`` 表达。
    """
    proxy = "（代理值：每股经营现金流）" if facts.ocf_is_proxy else ""
    return [
        _signal(
            "loss_years_gte_2", "连续亏损年数", f"{facts.loss_years} 年",
            facts.loss_years >= 2, "影响「经营困境」C4",
        ),
        _signal(
            "not_profitable", "近年盈利年数", f"{facts.profitable_years} 年",
            facts.profitable_years == 0, "影响「经营困境」C4",
        ),
        _signal(
            "ocf_not_positive", "最新经营现金流",
            ("为正" if facts.ocf_positive else "不为正") + proxy,
            not facts.ocf_positive, "影响「经营困境」C4、「风险」RISK",
        ),
        _signal(
            "ocf_improving", "经营现金流同比",
            ("改善" if facts.ocf_improving else "未改善") + proxy,
            facts.ocf_improving, "改善可降低「风险」RISK",
            adverse_when_held=False,
        ),
        _signal(
            "margin_declining", "毛利率同比改善期数",
            f"{facts.margin_improving_quarters} 期",
            facts.margin_improving_quarters == 0, "影响「经营困境」C4",
        ),
        _signal(
            "revenue_improving", "营收同比改善期数",
            f"{facts.revenue_improving_quarters} 期",
            facts.revenue_improving_quarters == 0, "影响「经营困境」C4",
        ),
        _signal(
            "receivable_problem", "应收增速 vs 营收增速",
            "应收增速更快" if facts.receivable_growth_exceeds_revenue else "未超过",
            facts.receivable_growth_exceeds_revenue, "影响「风险」RISK",
        ),
        _signal(
            "debt_ratio_rising", "资产负债率",
            "在上升" if facts.debt_ratio_rising else "未上升",
            facts.debt_ratio_rising, "影响「基本面」FUNDAMENTALS",
        ),
        _signal(
            "one_off_attributed", "恶化是否已归因",
            "已归因于一次性因素（按 INV-F1 不据此扣分）"
            if facts.deteriorating_attributed_to_one_off else "未归因",
            facts.deteriorating_attributed_to_one_off,
            "决定 C4 是否扣分", adverse_when_held=False,
        ),
    ]

__all__ = [
    "EVENT_LABELS",
    "RELIABILITY_NOTES",
    "STATUS_LABELS",
    "alert_detail",
    "company_brief",
    "event_brief",
    "evidence_detail",
    "open_question_detail",
    "financial_series",
    "market_layer",
    "financial_signals",
    "opportunity_card",
    "score_breakdown",
    "strategy_detail",
    "thesis_detail",
]
