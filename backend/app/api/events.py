"""事件与提醒（docs/05 §4 / §7，M10-06）。

**新闻聚类（M2-10 / 规格 §42）**：不返回 17 张重复卡片，而是
「围绕 ST XXX 重组事件的 17 条报道」+ 3~5 条要点。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.api import serializers
from app.api.deps import company_index, get_session
from app.api.envelope import NotFound, RuleViolation, ok, ok_list
from app.models.enums import EventType
from app.models.events import Event, EventCluster
from app.models.knowledge import News
from app.models.opportunity import Alert

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events(
    event_type: str | None = Query(default=None, description="csv"),
    company_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    min_importance: float | None = None,
    is_invalidating: bool | None = None,
    sort: str = "event_time",
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    statement = select(Event)
    if event_type:
        try:
            values = [EventType(v.strip()) for v in event_type.split(",") if v.strip()]
        except ValueError as exc:
            raise RuleViolation(
                f"未知事件类型：{event_type}",
                {"allowed": [e.value for e in EventType]},
            ) from exc
        statement = statement.where(Event.event_type.in_(values))  # type: ignore[attr-defined]
    if company_id:
        statement = statement.where(Event.company_id == company_id)
    if since:
        statement = statement.where(Event.event_time >= since)
    if until:
        statement = statement.where(Event.event_time <= until)
    if min_importance is not None:
        statement = statement.where(Event.importance >= min_importance)
    if is_invalidating is not None:
        statement = statement.where(Event.is_invalidating == is_invalidating)

    order_column = Event.importance if sort == "importance" else (
        Event.discovery_time if sort == "discovery_time" else Event.event_time
    )
    rows = session.exec(statement.order_by(order_column.desc())).all()  # type: ignore[attr-defined]

    total = len(rows)
    page_rows = rows[offset : offset + limit]
    companies, stocks = company_index(session, [int(r.company_id) for r in page_rows])

    data = []
    for event in page_rows:
        payload = serializers.event_brief(event)
        payload["company"] = serializers.company_brief(
            companies.get(int(event.company_id)), stocks.get(int(event.company_id))
        )
        data.append(payload)

    return ok_list(data, page={
        "limit": limit, "offset": offset, "total": total, "has_more": offset + limit < total,
    })


@router.get("/events/clusters")
def list_clusters(limit: int = 20, session: Session = Depends(get_session)) -> dict:
    clusters = session.exec(
        select(EventCluster).order_by(EventCluster.last_seen.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()

    data = []
    for cluster in clusters:
        source_rows = session.exec(
            select(News.source_name, News.source_reliability, func.count())
            .where(News.cluster_id == cluster.id)
            .group_by(News.source_name, News.source_reliability)
        ).all()
        companies, stocks = company_index(
            session, [int(cluster.company_id)] if cluster.company_id else []
        )
        data.append({
            "id": cluster.id,
            "label": cluster.label,
            "company": serializers.company_brief(
                companies.get(int(cluster.company_id)) if cluster.company_id else None,
                stocks.get(int(cluster.company_id)) if cluster.company_id else None,
            ),
            "event_type": str(cluster.event_type) if cluster.event_type else None,
            "member_count": cluster.member_count,
            "key_points": cluster.key_points,
            "sources": [
                {"name": name, "reliability": str(level), "count": int(count)}
                for name, level, count in source_rows
            ],
            "first_seen": cluster.first_seen.isoformat(),
            "last_seen": cluster.last_seen.isoformat(),
        })
    return ok(data)


@router.get("/alerts")
def list_alerts(
    is_read: bool | None = None, limit: int = 50, session: Session = Depends(get_session)
) -> dict:
    statement = select(Alert)
    if is_read is not None:
        statement = statement.where(Alert.is_read == is_read)
    rows = session.exec(
        statement.order_by(Alert.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return ok([serializers.alert_detail(a) for a in rows])


@router.post("/alerts/{alert_id}/read")
def mark_alert_read(alert_id: int, session: Session = Depends(get_session)) -> dict:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise NotFound("alert", alert_id)
    alert.is_read = True
    session.add(alert)
    session.commit()
    return ok({"id": alert_id, "is_read": True})


@router.post("/alerts/read-all")
def mark_all_read(session: Session = Depends(get_session)) -> dict:
    rows = session.exec(select(Alert).where(Alert.is_read == False)).all()  # noqa: E712
    for alert in rows:
        alert.is_read = True
        session.add(alert)
    session.commit()
    return ok({"updated": len(rows)})


__all__ = ["router"]
