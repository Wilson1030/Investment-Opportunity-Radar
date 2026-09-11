"""机会接口（docs/05 §3）。

    GET   /api/opportunities                      列表 + 排序
    GET   /api/opportunities/{id}                 详情（完整的「7 问」结构）
    GET   /api/opportunities/{id}/score-breakdown ★ 可解释性核心接口
    GET   /api/opportunities/{id}/evidence        证据（含原文段落定位）
    POST  /api/opportunities/{id}/actions         反馈闭环
    PATCH /api/opportunities/{id}/status          状态迁移（非法迁移 → 409）
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api import serializers
from app.api.deps import company_index, get_session
from app.api.envelope import DISCLAIMER, InvalidStatusTransition, NotFound, RuleViolation, ok, ok_list
from app.engine import guard
from app.models.enums import (
    ALLOWED_STATUS_TRANSITIONS,
    OpportunityStatus,
    ReliabilityLevel,
    ThesisType,
    UserActionKind,
)
from app.models.evidence import Evidence
from app.models.events import Event
from app.models.opportunity import (
    Opportunity,
    OpportunityScore,
    OpportunityStatusLog,
    ScoreItem,
    UserAction,
)
from app.models.thesis import Thesis
from app.strategies import get_def
from app.strategies.base import InvalidationHit
from app.strategies.restructuring import invalidation as restructuring_invalidation

router = APIRouter(tags=["opportunities"])

SORT_FIELDS = {
    "rule_score": Opportunity.rule_score,
    "match_score": Opportunity.match_score,
    "risk_score": Opportunity.risk_score,
    "certainty": Opportunity.rule_score,
    "event_strength": Opportunity.rule_score,
    "last_updated_at": Opportunity.last_updated_at,
}


class ActionRequest(BaseModel):
    action: str
    reason_thesis_types: list[ThesisType] = Field(default_factory=list)


class StatusRequest(BaseModel):
    to_status: OpportunityStatus
    reason: str = ""


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
@router.get("/opportunities")
def list_opportunities(
    status: str | None = Query(default=None, description="csv，如 pending_confirmation,tracking"),
    thesis_type: str | None = Query(default=None, description="csv"),
    company_id: int | None = None,
    min_rule_score: float | None = None,
    max_risk_score: float | None = None,
    sort: str = "rule_score",
    order: str = "desc",
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    statement = select(Opportunity)
    if status:
        try:
            values = [OpportunityStatus(v.strip()) for v in status.split(",") if v.strip()]
        except ValueError as exc:
            raise RuleViolation(
                f"未知状态：{status}",
                {"allowed": [s.value for s in OpportunityStatus]},
            ) from exc
        statement = statement.where(Opportunity.status.in_(values))  # type: ignore[attr-defined]
    if company_id:
        statement = statement.where(Opportunity.company_id == company_id)
    if min_rule_score is not None:
        statement = statement.where(Opportunity.rule_score >= min_rule_score)
    if max_risk_score is not None:
        statement = statement.where(Opportunity.risk_score <= max_risk_score)

    rows = session.exec(statement).all()

    # thesis_type 过滤需要 join Thesis（Opportunity 表上没有该列）
    if thesis_type:
        try:
            wanted = {ThesisType(v.strip()) for v in thesis_type.split(",") if v.strip()}
        except ValueError as exc:
            raise RuleViolation(
                f"未知策略类型：{thesis_type}",
                {"allowed": [t.value for t in ThesisType]},
            ) from exc
        thesis_ids = {
            int(t.id or 0)
            for t in session.exec(
                select(Thesis).where(Thesis.thesis_type.in_(wanted))  # type: ignore[attr-defined]
            ).all()
        }
        rows = [r for r in rows if r.thesis_id in thesis_ids]

    column = SORT_FIELDS.get(sort, Opportunity.rule_score)
    reverse = order != "asc"
    rows.sort(key=lambda r: (getattr(r, column.key, None) is None, getattr(r, column.key, None)),
              reverse=reverse)

    total = len(rows)
    page_rows = rows[offset : offset + limit]

    company_ids = [int(r.company_id) for r in page_rows]
    companies, stocks = company_index(session, company_ids)
    theses = {
        int(t.id or 0): t
        for t in session.exec(
            select(Thesis).where(Thesis.id.in_([r.thesis_id for r in page_rows] or [0]))  # type: ignore[attr-defined]
        ).all()
    }

    data = [
        serializers.opportunity_card(
            row,
            companies.get(int(row.company_id)),
            stocks.get(int(row.company_id)),
            [],
            thesis_type=theses[int(row.thesis_id)].thesis_type if int(row.thesis_id) in theses else None,
            risk_count=len(row.risks),
        )
        for row in page_rows
    ]
    return ok_list(
        data,
        page={"limit": limit, "offset": offset, "total": total, "has_more": offset + limit < total},
        meta={"disclaimer": DISCLAIMER},
    )


# --------------------------------------------------------------------------- #
# 详情
# --------------------------------------------------------------------------- #
@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: int, session: Session = Depends(get_session)) -> dict:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFound("opportunity", opportunity_id)

    thesis = session.get(Thesis, opportunity.thesis_id)
    companies, stocks = company_index(session, [int(opportunity.company_id)])
    company = companies.get(int(opportunity.company_id))
    stock = stocks.get(int(opportunity.company_id))

    events = session.exec(
        select(Event)
        .where(Event.company_id == opportunity.company_id)
        .order_by(Event.event_time.desc())  # type: ignore[attr-defined]
    ).all()

    evidence = _load_evidence(session, opportunity.supporting_evidence_ids)
    contradictions = _load_evidence(session, opportunity.contradictory_evidence_ids)
    questions = _open_questions(session, opportunity_id)
    status_history = session.exec(
        select(OpportunityStatusLog)
        .where(OpportunityStatusLog.opportunity_id == opportunity_id)
        .order_by(OpportunityStatusLog.changed_at)  # type: ignore[attr-defined]
    ).all()

    levels = [ReliabilityLevel(e.reliability_level) for e in evidence]
    card = serializers.opportunity_card(
        opportunity, company, stock, events,
        thesis_type=thesis.thesis_type if thesis else None,
        evidence_levels=levels,
        a_grade_count=sum(1 for lv in levels if lv is ReliabilityLevel.A),
        risk_count=len(opportunity.risks),
        open_question_count=sum(1 for q in questions if q.status == "open"),
    )
    card["latest_events"] = [serializers.event_brief(e) for e in events[:5]]

    return ok({
        "card": card,
        "thesis": _thesis_payload(thesis, evidence, contradictions),
        # ★ 规格 §24：待确认必须具体，且要能看到「已确认」一侧
        "open_questions": [serializers.open_question_detail(q) for q in questions],
        "confirmed_facts": _confirmed_facts(opportunity, events),
        "risks": [
            {"factor": risk, "weight": None, "severity": None, "description": risk}
            for risk in opportunity.risks
        ],
        "next_events_to_watch": opportunity.next_events_to_watch,
        "timeline": [
            {
                "date": e.event_time.date().isoformat() if e.event_time else None,
                "event_type": e.event_type.value,
                "title": e.title,
                "evidence_id": e.evidence_ids[0] if e.evidence_ids else None,
            }
            for e in reversed(events)
        ],
        "evidence": [serializers.evidence_detail(e) for e in evidence],
        "news_clusters": [],
        # ★ 行情仅为辅助信息层（规格 §46），不是首页主体
        "market": {
            "note": "行情数据仅作辅助信息，用于观察事件后的价格行为（规格 §46）",
        },
        "status_history": [
            {
                "from_status": str(h.from_status) if h.from_status else None,
                "to_status": str(h.to_status),
                "reason": h.reason,
                "score_before": h.score_before,
                "score_after": h.score_after,
                "changed_at": h.changed_at.isoformat(),
            }
            for h in status_history
        ],
        "disclaimer": DISCLAIMER,
    })


# --------------------------------------------------------------------------- #
# 评分拆解（可解释性核心）
# --------------------------------------------------------------------------- #
@router.get("/opportunities/{opportunity_id}/score-breakdown")
def score_breakdown(opportunity_id: int, session: Session = Depends(get_session)) -> dict:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFound("opportunity", opportunity_id)

    dimensions = session.exec(
        select(OpportunityScore).where(OpportunityScore.opportunity_id == opportunity_id)
    ).all()
    items = session.exec(
        select(ScoreItem).where(ScoreItem.opportunity_id == opportunity_id)
    ).all()

    payload = serializers.score_breakdown(opportunity, dimensions, items)
    payload["computed_at"] = opportunity.last_updated_at.isoformat()
    payload["disclaimer"] = DISCLAIMER
    if not dimensions:
        payload["note"] = (
            "尚未生成评分拆解 —— 请先运行 pipeline（python -m app.ingest --stage rescore）。"
            "系统不会展示无法解释的分数（规格 §13）。"
        )
    return ok(payload)


@router.get("/opportunities/{opportunity_id}/evidence")
def opportunity_evidence(opportunity_id: int, session: Session = Depends(get_session)) -> dict:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFound("opportunity", opportunity_id)
    evidence = _load_evidence(session, opportunity.supporting_evidence_ids)
    contradictions = _load_evidence(session, opportunity.contradictory_evidence_ids)
    return ok({
        "supporting": [serializers.evidence_detail(e) for e in evidence],
        "contradictory": [serializers.evidence_detail(e) for e in contradictions],
        "note": "每条证据均可定位到原文段落（page / para_index）",
    })


# --------------------------------------------------------------------------- #
# 反馈与状态
# --------------------------------------------------------------------------- #
@router.post("/opportunities/{opportunity_id}/actions")
def record_action(
    opportunity_id: int, request: ActionRequest, session: Session = Depends(get_session)
) -> dict:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFound("opportunity", opportunity_id)

    try:
        action = UserActionKind(request.action)
    except ValueError as exc:
        raise RuleViolation(
            f"未知操作：{request.action}",
            {"allowed": [a.value for a in UserActionKind]},
        ) from exc

    from app.api.deps import get_or_create_default_profile

    profile = get_or_create_default_profile(session)
    session.add(UserAction(
        investor_id=int(profile.investor_id),
        opportunity_id=opportunity_id,
        action=action.value,
        reason_thesis_types=request.reason_thesis_types,
    ))

    new_status = str(opportunity.status)
    if action is UserActionKind.CONFIRMED or action is UserActionKind.TRACKED:
        target = OpportunityStatus.TRACKING
        if _can_transition(OpportunityStatus(opportunity.status), target):
            _append_status(session, opportunity, target, reason=f"用户操作：{action.value}")
            new_status = target.value

    session.commit()
    return ok({
        "action": action.value,
        "new_status": new_status,
        "message": "已记录，将用于优化你的画像（你可以随时手动覆盖，M1-06）",
    })


@router.patch("/opportunities/{opportunity_id}/status")
def patch_status(
    opportunity_id: int, request: StatusRequest, session: Session = Depends(get_session)
) -> dict:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFound("opportunity", opportunity_id)

    from_status = OpportunityStatus(opportunity.status)
    to_status = request.to_status
    try:
        guard.check_status_transition(from_status, to_status)
    except guard.InvariantViolation as exc:
        raise InvalidStatusTransition(
            from_status.value, to_status.value,
            sorted(s.value for s in ALLOWED_STATUS_TRANSITIONS.get(from_status, set())),
        ) from exc

    # ★ INV-E2：C/D/E 类证据不得单独支撑 thesis_confirmed
    if to_status is OpportunityStatus.THESIS_CONFIRMED:
        levels = [e.reliability_level for e in _load_evidence(
            session, opportunity.supporting_evidence_ids
        )]
        try:
            guard.check_soft_evidence_confirmation(levels, to_status)
        except guard.InvariantViolation as exc:
            raise RuleViolation(str(exc), {"reliability_levels": [str(lv) for lv in levels]}) from exc

    _append_status(session, opportunity, to_status, reason=request.reason)
    session.commit()
    return ok({
        "opportunity_id": opportunity_id,
        "from_status": from_status.value,
        "to_status": to_status.value,
        "score": opportunity.rule_score,
    })


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _can_transition(from_status: OpportunityStatus, to_status: OpportunityStatus) -> bool:
    return to_status in ALLOWED_STATUS_TRANSITIONS.get(from_status, set())


def _append_status(
    session: Session, opportunity: Opportunity, to_status: OpportunityStatus, *, reason: str
) -> None:
    previous = OpportunityStatus(opportunity.status)
    session.add(OpportunityStatusLog(
        opportunity_id=int(opportunity.id or 0),
        from_status=previous,
        to_status=to_status,
        reason=reason,
        score_before=opportunity.rule_score,
        score_after=opportunity.rule_score,
        changed_at=datetime.now(timezone.utc),
    ))
    opportunity.status = to_status
    opportunity.last_updated_at = datetime.now(timezone.utc)
    session.add(opportunity)


def _load_evidence(session: Session, evidence_ids: list[int]) -> list[Evidence]:
    if not evidence_ids:
        return []
    return list(session.exec(
        select(Evidence).where(Evidence.id.in_(evidence_ids))  # type: ignore[attr-defined]
    ).all())


def _open_questions(session: Session, opportunity_id: int):
    from app.models.opportunity import OpenQuestion

    return list(session.exec(
        select(OpenQuestion).where(OpenQuestion.opportunity_id == opportunity_id)
    ).all())


def _confirmed_facts(opportunity: Opportunity, events: list[Event]) -> list[str]:
    """「已确认」一侧 —— 来自事件而非「待确认」标签（规格 §24）。"""
    facts: list[str] = []
    for event in events:
        label = serializers.EVENT_LABELS.get(event.event_type)
        if label and label not in facts:
            facts.append(label)
    return facts


def _thesis_payload(
    thesis: Thesis | None, evidence: list[Evidence], contradictions: list[Evidence]
) -> dict | None:
    if thesis is None:
        return None
    definition = get_def(ThesisType(thesis.thesis_type))
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
        "supporting_evidence": [serializers.evidence_detail(e) for e in evidence],
        "contradictory_evidence": [serializers.evidence_detail(e) for e in contradictions],
        # ★ M5-03：每个 Thesis 必须定义失效条件
        "invalidating_events": [
            {
                "event_type": d.event_type.value,
                "severity": d.severity.value,
                "description": d.description,
            }
            for d in definition.invalidating_events
        ],
    }


__all__ = ["router"]
