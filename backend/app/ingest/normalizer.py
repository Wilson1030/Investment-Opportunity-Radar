"""清洗、标准化与幂等落库（M12-04 / INV-EV2 / INV-NF-04）。

幂等键::

    公告  UNIQUE(company_id, document_id)
    段落  UNIQUE(announcement_id, page, para_index)   ← 证据闸门的比对基准
    新闻  UNIQUE(source_name, external_id)
    事件  UNIQUE(company_id, event_type, event_time, source_url)

**同一批数据跑两次，行数不变。** 这是 dry-run 转真写的前置条件之一（docs/07 §6）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.engine import classifier, guard
from app.ingest.base import ParsedDocument, RawAnnouncement
from app.models.enums import ParseStatus, ReliabilityLevel, SourceType
from app.models.knowledge import Announcement, Company, News, Paragraph, Stock


@dataclass(frozen=True)
class UpsertOutcome:
    announcement_id: int | None
    created: bool
    paragraph_count: int
    skipped_reason: str | None = None


def upsert_company(
    session: Session, code: str, name: str = "", *, is_st: bool = False,
    commit: bool = True,
) -> Company:
    stock = session.exec(select(Stock).where(Stock.code == code)).first()
    if stock is not None:
        company = session.get(Company, stock.company_id)
        if company is not None:
            if is_st and not company.is_st:
                company.is_st = True
                company.updated_at = datetime.now(timezone.utc)
                session.add(company)
                if commit:
                    session.commit()
            return company

    company = Company(name=name or code, is_st=is_st, is_risk_warning=is_st)
    session.add(company)
    session.flush()
    session.add(Stock(company_id=int(company.id or 0), code=code,
                      exchange=_exchange_of(code), name=name or code))
    if commit:
        session.commit()
        session.refresh(company)
    return company


def _exchange_of(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SSE"
    if code.startswith(("4", "8")):
        return "BSE"
    return "SZSE"


def upsert_announcement(
    session: Session,
    company_id: int,
    raw: RawAnnouncement,
    parsed: ParsedDocument | None = None,
    *,
    ingest_run_id: int | None = None,
    dry_run: bool = False,
    commit: bool = True,
) -> UpsertOutcome:
    """幂等写入公告 + 段落。已存在则跳过（不覆盖已解析结果）。"""
    guard.check_event_times(raw.publication_time, datetime.now(timezone.utc))

    existing = session.exec(
        select(Announcement).where(
            Announcement.company_id == company_id,
            Announcement.document_id == raw.document_id,
        )
    ).first()
    if existing is not None:
        count = session.exec(
            select(Paragraph).where(Paragraph.announcement_id == existing.id)
        ).all()
        return UpsertOutcome(existing.id, created=False, paragraph_count=len(count),
                             skipped_reason="document_id 已存在（幂等跳过）")

    event_type = classifier.classify_announcement(raw.title, raw.announcement_type)
    if dry_run:
        return UpsertOutcome(None, created=False, paragraph_count=0,
                             skipped_reason="dry_run：未写库")

    announcement = Announcement(
        company_id=company_id,
        document_id=raw.document_id,
        title=raw.title,
        announcement_type=raw.announcement_type,
        event_type=event_type,
        publication_time=raw.publication_time,
        discovery_time=datetime.now(timezone.utc),
        source_url=raw.url,
        raw_format=parsed.raw_format if parsed else None,
        fulltext=parsed.fulltext if parsed else None,
        parse_status=ParseStatus(parsed.parse_status) if parsed else ParseStatus.OK,
        parse_error=parsed.parse_error if parsed else None,
        paragraph_count=len(parsed.paragraphs) if parsed else 0,
        ingest_run_id=ingest_run_id,
    )
    session.add(announcement)
    session.flush()
    announcement_id = int(announcement.id or 0)

    paragraph_count = 0
    if parsed and parsed.paragraphs:
        paragraph_count = replace_paragraphs(session, announcement_id, parsed, commit=False)

    if commit:
        session.commit()
    return UpsertOutcome(announcement_id, created=True, paragraph_count=paragraph_count)


def replace_paragraphs(
    session: Session, announcement_id: int, parsed: ParsedDocument, *, commit: bool = True
) -> int:
    """写入段落（幂等：同一 (page, para_index) 已存在则跳过）。"""
    existing = {
        (p.page, p.para_index)
        for p in session.exec(
            select(Paragraph).where(Paragraph.announcement_id == announcement_id)
        ).all()
    }
    written = 0
    for page, para_index, text, start, end in parsed.paragraphs:
        if (page, para_index) in existing:
            continue
        session.add(Paragraph(
            announcement_id=announcement_id,
            page=page,
            para_index=para_index,
            text=text,
            char_start=start,
            char_end=end,
        ))
        written += 1
    if commit:
        session.commit()
    return written


def upsert_news(
    session: Session, company_id: int | None, item: dict, *, dry_run: bool = False,
    commit: bool = True,
) -> News | None:
    existing = session.exec(
        select(News).where(
            News.source_name == item["source_name"],
            News.external_id == item["external_id"],
        )
    ).first()
    if existing is not None:
        return existing
    if dry_run:
        return None

    news = News(
        external_id=item["external_id"],
        title=item["title"],
        summary=item.get("summary"),
        source_name=item["source_name"],
        source_reliability=ReliabilityLevel.C,
        publication_time=item["publication_time"],
        discovery_time=datetime.now(timezone.utc),
        url=item["url"],
        related_company_ids=[company_id] if company_id else [],
        event_type=classifier.classify_announcement(item["title"]),
    )
    session.add(news)
    if commit:
        session.commit()
        session.refresh(news)
    return news


def source_type_of(raw: RawAnnouncement) -> SourceType:
    return SourceType.ANNOUNCEMENT


__all__ = [
    "UpsertOutcome",
    "replace_paragraphs",
    "source_type_of",
    "upsert_announcement",
    "upsert_company",
    "upsert_news",
]
