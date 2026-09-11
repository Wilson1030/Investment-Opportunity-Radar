"""``GET /api/radar`` —— 首页聚合（docs/05 §2，M10-01）。

一次请求返回首页所需全部数据，避免前端瀑布式请求。
**首屏不出现 K 线**（M10-08）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.api import serializers
from app.api.deps import (
    company_index,
    get_or_create_default_profile,
    get_session,
    profile_weights,
)
from app.api.envelope import DISCLAIMER, ok
from app.models.audit import IngestRun
from app.models.enums import OpportunityStatus, ReliabilityLevel
from app.models.evidence import Evidence
from app.models.events import Event
from app.models.opportunity import Alert, OpenQuestion, Opportunity
from app.models.thesis import Thesis
from app.scheduler.calendar import get_calendar
from app.strategies import get_def

router = APIRouter(tags=["radar"])

DEFAULT_CARD_LIMIT = 6


@router.get("/radar")
def radar(session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)
    weights = profile_weights(session, int(profile.id or 0))

    # 状态计数
    counts = {s.value: 0 for s in OpportunityStatus}
    for status, count in session.exec(
        select(Opportunity.status, func.count()).group_by(Opportunity.status)
    ).all():
        counts[str(status)] = int(count)

    # 今日机会卡（按规则分降序）
    cards = _cards(session, profile.id, limit=DEFAULT_CARD_LIMIT)

    # 最近重要事件
    recent_events = session.exec(
        select(Event).order_by(Event.discovery_time.desc()).limit(8)  # type: ignore[attr-defined]
    ).all()

    # 未读提醒
    alerts = session.exec(
        select(Alert).where(Alert.is_read == False).order_by(Alert.created_at.desc()).limit(5)  # noqa: E712
    ).all()

    calendar = get_calendar()
    last_run = session.exec(select(IngestRun).order_by(IngestRun.started_at.desc())).first()

    top_weights = [
        {
            "thesis_type": key,
            "display_name": get_def(key).display_name,
            "weight": value,
        }
        for key, value in sorted(weights.items(), key=lambda kv: -kv[1])[:5]
    ]

    return ok({
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "top_weights": top_weights,
            "auto_learn_enabled": profile.auto_learn_enabled,
            "locked_weights": [str(t) for t in profile.locked_weights],
        },
        "today": {
            "date": date.today().isoformat(),
            "is_trading_day": calendar.is_trading_day(date.today()),
            "calendar_degraded": calendar.degraded,
            "calendar_warning": calendar.warning,
            "new_count": counts.get(OpportunityStatus.DISCOVERED.value, 0),
            "cards": cards,
        },
        "counts": counts,
        "recent_events": [serializers.event_brief(e) for e in recent_events],
        "alerts": [serializers.alert_detail(a) for a in alerts],
        "pipeline": {
            "last_run_at": last_run.started_at.isoformat() if last_run else None,
            "dry_run": last_run.dry_run if last_run else None,
            "funnel": last_run.funnel if last_run else {},
            "quality": last_run.quality if last_run else {},
            "hint": _drop_hint(last_run),
        },
        "disclaimer": DISCLAIMER,
    })


def _drop_hint(run: IngestRun | None) -> str | None:
    if run is None or not run.funnel:
        return None
    from app.engine.funnel import FunnelCounters

    counters = FunnelCounters(**{k: v for k, v in run.funnel.items()
                                 if k in FunnelCounters.__dataclass_fields__})
    return counters.drop_at()


def _cards(session: Session, profile_id: int | None, limit: int = 6) -> list[dict]:
    rows = session.exec(
        select(Opportunity)
        .where(Opportunity.profile_id == profile_id)
        .order_by(Opportunity.rule_score.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    if not rows:
        return []

    company_ids = [int(o.company_id) for o in rows]
    companies, stocks = company_index(session, company_ids)

    theses = {
        int(t.id or 0): t
        for t in session.exec(select(Thesis).where(Thesis.id.in_([o.thesis_id for o in rows]))).all()  # type: ignore[attr-defined]
    }
    opportunity_ids = [int(o.id or 0) for o in rows]

    events_by_company: dict[int, list[Event]] = {}
    for event in session.exec(
        select(Event)
        .where(Event.company_id.in_(company_ids))  # type: ignore[attr-defined]
        .order_by(Event.event_time.desc())  # type: ignore[attr-defined]
    ).all():
        events_by_company.setdefault(int(event.company_id), []).append(event)

    questions: dict[int, int] = {}
    for opportunity_id, count in session.exec(
        select(OpenQuestion.opportunity_id, func.count())
        .where(OpenQuestion.opportunity_id.in_(opportunity_ids))  # type: ignore[attr-defined]
        .group_by(OpenQuestion.opportunity_id)
    ).all():
        questions[int(opportunity_id)] = int(count)

    id_to_thesis_type = {}
    for opportunity in rows:
        thesis = theses.get(int(opportunity.thesis_id))
        if thesis is not None:
            id_to_thesis_type[int(opportunity.id or 0)] = thesis.thesis_type

    evidence_levels: dict[int, list[ReliabilityLevel]] = {}
    a_grade: dict[int, int] = {}
    for opportunity in rows:
        levels: list[ReliabilityLevel] = []
        for evidence_id in opportunity.supporting_evidence_ids:
            row = session.get(Evidence, evidence_id)
            if row is not None:
                levels.append(ReliabilityLevel(row.reliability_level))
        evidence_levels[int(opportunity.id or 0)] = levels
        a_grade[int(opportunity.id or 0)] = sum(
            1 for lv in levels if lv is ReliabilityLevel.A
        )

    from app.models.enums import ThesisType

    cards: list[dict] = []
    for opportunity in rows:
        opportunity_id = int(opportunity.id or 0)
        thesis_type = id_to_thesis_type.get(opportunity_id)
        if thesis_type is None:
            # Thesis 缺失属于数据异常：不静默丢卡片，也不假装有类型
            continue
        cards.append(
            serializers.opportunity_card(
                opportunity,
                companies.get(int(opportunity.company_id)),
                stocks.get(int(opportunity.company_id)),
                events_by_company.get(int(opportunity.company_id), []),
                thesis_type=ThesisType(thesis_type),
                evidence_levels=evidence_levels.get(opportunity_id, []),
                a_grade_count=a_grade.get(opportunity_id, 0),
                risk_count=len(opportunity.risks),
                open_question_count=questions.get(opportunity_id),
            )
        )
    return cards


__all__ = ["router"]
