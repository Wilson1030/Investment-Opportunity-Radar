"""LLM 抽取结果 → Event + Evidence（**必须经过证据闸门**）。

这是 docs/03 §6.3 的执行者。三条硬规则：

1. 每条证据片段必须通过四道校验（段落存在 / 文本子串 / 证据 ID 存在 / 来源等级由规则赋值）
2. **某事件的证据全部被拒 → 该事件不落库**（它没有证据，而不该「先落库再说」）
3. ``event_time`` 不得猜测：公告未写明时回退为公告发布时间，并在 ``attributes`` 里标注来源
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.ai.schemas import ExtractEventOutput
from app.engine import guard
from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, SourceType
from app.models.evidence import Evidence
from app.models.events import Event
from app.models.knowledge import Announcement, Paragraph
from app.strategies import STRATEGIES, get_strategy
from app.strategies.base import NotImplementedStrategy

#: event_time 缺失时的回退来源标记
EVENT_TIME_FROM_PUBLICATION = "publication_time"


@dataclass
class EventWriteResult:
    created: bool
    event_id: int | None = None
    evidence_ids: tuple[int, ...] = ()
    accepted_slices: int = 0
    rejected_slices: tuple[tuple[str, str], ...] = ()
    skipped_reason: str | None = None
    event_time_source: str = "disclosed"
    event_type: str | None = None
    title: str = ""

    @property
    def gate_passed(self) -> bool:
        """证据闸门是否放行。

        ★ 漏斗里的 ``events_extracted`` 统计的是**这一项**，而不是「落库成功」——
        否则 dry-run（不写判断类数据）会让漏斗看起来像全断，而 dry-run 恰恰是
        最需要看清 AI 链路质量的时候。
        """
        return self.accepted_slices > 0

    @property
    def rejection_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reason, _ in self.rejected_slices:
            key = reason.split("：", 1)[0]
            counts[key] = counts.get(key, 0) + 1
        return counts


def _paragraph_lookup(session: Session, announcement_id: int):
    rows = session.exec(
        select(Paragraph).where(Paragraph.announcement_id == announcement_id)
    ).all()
    table = {(int(p.page), int(p.para_index)): p for p in rows}

    def lookup(page: int, para_index: int) -> str | None:
        row = table.get((page, para_index))
        return row.text if row else None

    return lookup, table


def _mark_invalidating(event_type: EventType, title: str) -> bool:
    """该事件是否会破坏某个已实现策略的逻辑（规格 §22 / §23）。"""
    for code, definition in STRATEGIES.items():
        implementation = get_strategy(code)
        if isinstance(implementation, NotImplementedStrategy):
            continue
        for rule in definition.invalidating_events:
            if rule.event_type is not event_type:
                continue
            if rule.title_contains and not any(kw in title for kw in rule.title_contains):
                continue
            return True
    return False


def persist_extraction(
    session: Session,
    company_id: int,
    announcement: Announcement,
    extraction: ExtractEventOutput,
    *,
    dry_run: bool = False,
    commit: bool = True,
) -> EventWriteResult:
    """把一次 LLM 抽取落库为 Evidence + Event（经证据闸门）。"""
    announcement_id = int(announcement.id or 0)
    lookup, table = _paragraph_lookup(session, announcement_id)

    slices = [
        guard.EvidenceSlice(page=s.page, para_index=s.para_index, relevant_text=s.relevant_text)
        for s in extraction.evidence_slices
    ]
    decision = guard.gate_evidence_slices(slices, lookup)
    rejected = tuple(
        (f"{item.page},{item.para_index}", reason) for item, reason in decision.rejected
    )

    # ★ 规则 2：证据全部被拒 → 事件不落库
    if decision.all_rejected:
        return EventWriteResult(
            created=False,
            accepted_slices=0,
            rejected_slices=rejected,
            skipped_reason=(
                f"证据全部被拒（{len(slices)} 条），事件未落库 —— 没有证据的判断不写进系统"
            ),
            event_type=str(extraction.event_type),
            title=extraction.title or announcement.title,
        )

    # 来源等级由规则赋值，不接受 LLM 自述（docs/04 §6）
    reliability = guard.reliability_for_source(SourceType.ANNOUNCEMENT)
    evidence_level = reliability

    # 规则 3：event_time 不得猜测
    event_time = extraction.event_time
    time_source = "disclosed"
    if event_time is None:
        event_time = announcement.publication_time
        time_source = EVENT_TIME_FROM_PUBLICATION
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    discovery_time = datetime.now(timezone.utc)

    try:
        guard.check_event_times(event_time, discovery_time)   # INV-EV1
        guard.check_announcement_evidence(SourceType.ANNOUNCEMENT, announcement_id)  # INV-E3
    except guard.InvariantViolation as exc:
        return EventWriteResult(
            created=False, rejected_slices=rejected, skipped_reason=str(exc),
            accepted_slices=len(decision.accepted),
        )

    source_url = announcement.source_url
    existing = session.exec(
        select(Event).where(
            Event.company_id == company_id,
            Event.event_type == extraction.event_type,
            Event.event_time == event_time,
            Event.source_url == source_url,
        )
    ).first()
    if existing is not None:
        return EventWriteResult(
            created=False,
            event_id=int(existing.id or 0),
            evidence_ids=tuple(existing.evidence_ids or ()),
            accepted_slices=len(decision.accepted),
            rejected_slices=rejected,
            skipped_reason="事件已存在（INV-EV2 幂等去重）",
            event_time_source=time_source,
        )

    if dry_run:
        return EventWriteResult(
            created=False,
            accepted_slices=len(decision.accepted),
            rejected_slices=rejected,
            skipped_reason="dry_run：未写库（判断类数据）",
            event_time_source=time_source,
            event_type=str(extraction.event_type),
            title=extraction.title or announcement.title,
        )

    # ---- Evidence ----
    evidence_rows: list[Evidence] = []
    for item in decision.accepted:
        paragraph = table.get((item.page, item.para_index))
        evidence = Evidence(
            source_type=SourceType.ANNOUNCEMENT,
            source_name="巨潮资讯",
            source_url=source_url,
            publication_time=announcement.publication_time,
            reliability_level=reliability,
            announcement_id=announcement_id,
            paragraph_id=int(paragraph.id) if paragraph and paragraph.id else None,
            document_id=announcement.document_id,
            relevant_text=item.relevant_text,      # 原文片段，未经改写
            page=item.page,
            para_index=item.para_index,
            assertion_kind="fact",
            extracted_facts=[f.statement for f in extraction.extracted_facts][:10],
            confidence=extraction.certainty,
        )
        session.add(evidence)
        evidence_rows.append(evidence)
    session.flush()
    evidence_ids = tuple(int(e.id or 0) for e in evidence_rows)

    # ---- Event ----
    attributes: dict = {
        "amount_ratio": extraction.amount_ratio,
        "counterparty_known": extraction.counterparty_known,
        "not_mentioned": extraction.not_mentioned,
        "event_time_source": time_source,
        "llm_summary": extraction.summary,
    }
    event = Event(
        company_id=company_id,
        event_type=extraction.event_type,
        title=extraction.title or announcement.title,
        summary=extraction.summary,
        event_time=event_time,
        discovery_time=discovery_time,
        importance=extraction.importance,
        certainty=extraction.certainty,
        certainty_level=extraction.certainty_level,
        source_type=SourceType.ANNOUNCEMENT,
        source_url=source_url,
        affected_thesis=list(extraction.affected_thesis),
        evidence_ids=list(evidence_ids),
        attributes=attributes,
        is_invalidating=_mark_invalidating(extraction.event_type, extraction.title or ""),
    )
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)

    return EventWriteResult(
        created=True,
        event_id=int(event.id or 0) if event.id else None,
        evidence_ids=evidence_ids,
        accepted_slices=len(decision.accepted),
        rejected_slices=rejected,
        event_time_source=time_source,
        event_type=str(extraction.event_type),
        title=extraction.title or announcement.title,
    )


def facts_after_write(session: Session, company_id: int, facts: StrategyFacts) -> StrategyFacts:
    """落库后刷新事实（便于同一次运行内立即生成机会）。"""
    from app.pipeline import facts_builder

    return facts_builder.build_strategy_facts(session, company_id)


__all__ = [
    "EVENT_TIME_FROM_PUBLICATION",
    "EventWriteResult",
    "facts_after_write",
    "persist_extraction",
]
