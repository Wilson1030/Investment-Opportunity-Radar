"""My Thesis（docs/05 §5，M10-05）。

规格 §21：用户关注的是「因为 X 逻辑，所以关注 Y」，所以这一页按 **Thesis 类型聚合**，
而不是列一串股票代码。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.api import serializers
from app.api.deps import company_index, get_or_create_default_profile, get_session
from app.api.envelope import DISCLAIMER, NotFound, ok
from app.models.enums import OpportunityStatus
from app.models.evidence import Evidence
from app.models.opportunity import Alert, OpenQuestion, Opportunity
from app.models.thesis import Thesis
from app.strategies import get_def

router = APIRouter(tags=["thesis"])

TRACKING_STATUSES = {
    OpportunityStatus.PENDING_CONFIRMATION,
    OpportunityStatus.TRACKING,
    OpportunityStatus.THESIS_CONFIRMED,
    OpportunityStatus.OBSERVING,
    OpportunityStatus.DISCOVERED,
}


@router.get("/thesis")
def my_thesis(session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)
    opportunities = session.exec(
        select(Opportunity).where(Opportunity.profile_id == profile.id)
    ).all()
    tracked = [o for o in opportunities if OpportunityStatus(o.status) in TRACKING_STATUSES]

    thesis_map = {
        int(t.id or 0): t
        for t in session.exec(
            select(Thesis).where(Thesis.id.in_([o.thesis_id for o in tracked] or [0]))  # type: ignore[attr-defined]
        ).all()
    }
    companies, stocks = company_index(session, [int(o.company_id) for o in tracked])

    opportunity_ids = [int(o.id or 0) for o in tracked] or [0]

    unread_by_opportunity: dict[int, int] = {}
    for opportunity_id, count in session.exec(
        select(Alert.opportunity_id, func.count())
        .where(Alert.opportunity_id.in_(opportunity_ids))  # type: ignore[attr-defined]
        .where(Alert.is_read == False)  # noqa: E712
        .group_by(Alert.opportunity_id)
    ).all():
        unread_by_opportunity[int(opportunity_id)] = int(count)

    open_questions: dict[int, int] = {}
    for opportunity_id, count in session.exec(
        select(OpenQuestion.opportunity_id, func.count())
        .where(OpenQuestion.opportunity_id.in_(opportunity_ids))  # type: ignore[attr-defined]
        .where(OpenQuestion.status == "open")
        .group_by(OpenQuestion.opportunity_id)
    ).all():
        open_questions[int(opportunity_id)] = int(count)

    items: list[dict] = []
    by_type: dict[str, int] = {}
    for opportunity in tracked:
        thesis = thesis_map.get(int(opportunity.thesis_id))
        if thesis is None:
            continue
        thesis_type = str(thesis.thesis_type)
        display = get_def(thesis_type).display_name
        by_type[display] = by_type.get(display, 0) + 1
        items.append({
            "id": thesis.id,
            "opportunity_id": opportunity.id,
            "company": serializers.company_brief(
                companies.get(int(opportunity.company_id)),
                stocks.get(int(opportunity.company_id)),
            ),
            "thesis_type": thesis_type,
            "display_name": display,
            "statement": thesis.statement,
            "status": str(opportunity.status),
            "rule_score": opportunity.rule_score,
            "risk_score": opportunity.risk_score,
            "evidence_count": len(opportunity.supporting_evidence_ids),
            "contradictory_count": len(opportunity.contradictory_evidence_ids),
            "open_question_count": open_questions.get(int(opportunity.id or 0), 0),
            # ★ 逻辑失效条件必须可见（规格 §23）
            "invalidating_events": [
                d.description for d in get_def(thesis_type).invalidating_events
            ],
            "next_events_to_watch": opportunity.next_events_to_watch,
            "last_updated_at": opportunity.last_updated_at.isoformat(),
            "has_unread_alert": unread_by_opportunity.get(int(opportunity.id or 0), 0) > 0,
        })

    return ok({
        "total_tracking": len(items),
        "by_type": [
            {"display_name": name, "count": count}
            for name, count in sorted(by_type.items(), key=lambda kv: -kv[1])
        ],
        "items": sorted(items, key=lambda i: -(i["rule_score"] or 0)),
        "disclaimer": DISCLAIMER,
    })


@router.get("/thesis/{thesis_id}")
def thesis_detail(thesis_id: int, session: Session = Depends(get_session)) -> dict:
    thesis = session.get(Thesis, thesis_id)
    if thesis is None:
        raise NotFound("thesis", thesis_id)

    evidence = list(session.exec(
        select(Evidence).where(Evidence.id.in_(thesis.supporting_evidence_ids or [0]))  # type: ignore[attr-defined]
    ).all())
    contradictions = list(session.exec(
        select(Evidence).where(Evidence.id.in_(thesis.contradictory_evidence_ids or [0]))  # type: ignore[attr-defined]
    ).all())

    return ok({
        "thesis": serializers.thesis_detail(thesis, evidence, contradictions),
        "disclaimer": DISCLAIMER,
    })


__all__ = ["router"]
