"""``GET /api/radar`` —— 首页聚合（docs/05 §2，M10-01）。

一次请求返回首页所需全部数据，避免前端瀑布式请求。
**首屏不出现 K 线**（M10-08）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.api import serializers
from app.api.deps import (
    company_index,
    get_or_create_default_profile,
    get_session,
    profile_weights,
)
from app.api.envelope import DISCLAIMER, ok
from app.api.strategy_overview import strategy_overview
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
def radar(
    thesis_type: str | None = Query(
        default=None,
        description="只看某一类投资逻辑（留空 = 全部已关注的策略）",
    ),
    session: Session = Depends(get_session),
) -> dict:
    profile = get_or_create_default_profile(session)
    weights = profile_weights(session, int(profile.id or 0))

    # 状态计数
    counts = {s.value: 0 for s in OpportunityStatus}
    for status, count in session.exec(
        select(Opportunity.status, func.count()).group_by(Opportunity.status)
    ).all():
        counts[str(status)] = int(count)

    # 「今日新增」= 今天首次发现的机会数（不是 status=discovered 的计数）
    day_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
    today_new = int(
        session.exec(
            select(func.count())
            .select_from(Opportunity)
            .where(Opportunity.profile_id == profile.id)
            .where(Opportunity.first_discovered_at >= day_start)
        ).one()
    )

    # 今日机会卡（按规则分降序；可按投资逻辑筛选）
    cards = _cards(session, profile.id, limit=DEFAULT_CARD_LIMIT, thesis_type=thesis_type)

    # ★ 策略全景：**每一类策略都要有交代**。
    #
    # 实现了 10 类策略却只出 4 类卡时，用户看到的是「剩下 6 类没加入」——
    # 而真相可能是「这批候选公司里没有符合那 6 类逻辑的标的」。
    # 不解释的话，用户只能认为它们没实现（用户的原话就是这样）。
    overview = strategy_overview(session, int(profile.id or 0), weights)

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
            #: 是否把「早期苗头」纳入关注范围（前端 Profile 页要能开关）
            "accept_early_signals": profile.accept_early_signals,
            "locked_weights": [str(t) for t in profile.locked_weights],
        },
        "today": {
            "date": date.today().isoformat(),
            "is_trading_day": calendar.is_trading_day(date.today()),
            "calendar_degraded": calendar.degraded,
            "calendar_warning": calendar.warning,
            "new_count": today_new,
            "cards": cards,
        },
        "counts": counts,
        #: 每类策略的卡片数与「为什么没有卡」
        "strategies": overview,
        #: 画像权重为 0、因而**永远不会出卡**的策略（必须显式告知）
        "inactive_strategies": [
            item for item in overview if item["weight"] <= 0
        ],
        "filtered_thesis_type": thesis_type,
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


#: 「今日机会」只列**活跃**机会。
#:
#: ★ 归档与失效都不算「机会」：
#:   · ARCHIVED = 终态（用户/系统已判定它不再值得关注）
#:   · INVALIDATED = 投资逻辑已死（它的对客通道是**失效提醒**，不是机会列表）
#:
#: 为什么必须是显式过滤而不是「反正分数低排不上」：
#: 实测归档/失效卡的分数可能**很高**（东兴/信达修正后 43.5 / 42.75，
#: 排到全库第一、第二位）—— 靠分数天然过滤是巧合，不是设计。
#: 它们仍可通过 ``/api/opportunities?status=...`` 查到，也在 counts 里可见。
ACTIVE_STATUSES: tuple[OpportunityStatus, ...] = (
    OpportunityStatus.DISCOVERED,
    OpportunityStatus.PENDING_CONFIRMATION,
    OpportunityStatus.TRACKING,
    OpportunityStatus.THESIS_CONFIRMED,
    OpportunityStatus.OBSERVING,
)


def _cards(
    session: Session, profile_id: int | None, limit: int = 6,
    thesis_type: str | None = None,
) -> list[dict]:
    statement = (
        select(Opportunity)
        .where(Opportunity.profile_id == profile_id)
        .where(Opportunity.status.in_([s.value for s in ACTIVE_STATUSES]))  # type: ignore[attr-defined]
    )
    if thesis_type:
        # ★ 按投资逻辑筛选：用户可以只看「价值发现」或「困境反转」。
        #   实现 10 类策略后，不筛选的话低分策略永远排在 6 张卡的窗口之外。
        statement = statement.join(
            Thesis, Thesis.id == Opportunity.thesis_id  # type: ignore[arg-type]
        ).where(Thesis.thesis_type == thesis_type)
    rows = session.exec(
        statement.order_by(Opportunity.rule_score.desc())  # type: ignore[attr-defined]
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
