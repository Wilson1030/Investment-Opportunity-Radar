"""采集幂等性（INV-NF-04 / M12-04 / INV-EV2）。

**同一批数据跑两次，行数不变。** 这是 dry-run 转真写的前置条件（docs/07 §6）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import func, select

from app.ingest.base import ParsedDocument, RawAnnouncement
from app.ingest.normalizer import (
    replace_paragraphs,
    upsert_announcement,
    upsert_company,
    upsert_news,
)
from app.models.enums import EventType, ParseStatus
from app.models.knowledge import Announcement, News, Paragraph, Stock

T0 = datetime(2026, 9, 8, 11, 32, tzinfo=timezone.utc)
URL = "http://static.cninfo.com.cn/example.pdf"


def _raw(document_id: str = "ANN-2026-0908-001") -> RawAnnouncement:
    return RawAnnouncement(
        company_code="600xxx",
        document_id=document_id,
        title="重大资产重组预案公告",
        announcement_type=None,
        publication_time=T0,
        url=URL,
    )


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        fulltext="公司拟以发行股份方式购买 XX 资产。交易对价尚未确定。",
        paragraphs=(
            (2, 1, "公司拟以发行股份方式购买 XX 资产。", 0, 18),
            (2, 2, "交易对价尚未确定。", 18, 27),
        ),
        parse_status=ParseStatus.OK.value,
        raw_format="pdf",
    )


# --------------------------------------------------------------------------- #
def test_announcement_upsert_is_idempotent(session):
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    company_id = int(company.id or 0)

    first = upsert_announcement(session, company_id, _raw(), _parsed())
    assert first.created is True
    assert first.paragraph_count == 2

    second = upsert_announcement(session, company_id, _raw(), _parsed())
    assert second.created is False
    assert "幂等" in (second.skipped_reason or "")
    assert second.announcement_id == first.announcement_id

    total = session.exec(select(func.count()).select_from(Announcement)).one()
    assert int(total) == 1, "同一 document_id 不得重复入库"


def test_paragraphs_are_not_duplicated(session):
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    outcome = upsert_announcement(session, int(company.id), _raw(), _parsed())
    announcement_id = int(outcome.announcement_id)

    written = replace_paragraphs(session, announcement_id, _parsed())
    assert written == 0, "同一 (page, para_index) 不得重复写入"

    count = session.exec(
        select(func.count()).select_from(Paragraph).where(
            Paragraph.announcement_id == announcement_id
        )
    ).one()
    assert int(count) == 2


def test_company_upsert_is_idempotent(session):
    first = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    second = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    assert int(first.id) == int(second.id)

    stocks = session.exec(select(Stock).where(Stock.code == "600xxx")).all()
    assert len(stocks) == 1

    companies = session.exec(
        select(func.count()).select_from(__import__("app.models", fromlist=["Company"]).Company)
    ).one()
    assert int(companies) == 1


def test_company_upsert_marks_st_when_discovered_later(session):
    """ST 状态可能后被发现（名单更新），必须能补上而不是新建一条。"""
    company = upsert_company(session, "000001", "平安银行", is_st=False)
    assert company.is_st is False

    refreshed = upsert_company(session, "000001", "平安银行", is_st=True)
    assert int(refreshed.id) == int(company.id)
    assert refreshed.is_st is True


def test_news_upsert_is_idempotent(session):
    item = {
        "external_id": "http://example.com/news/1",
        "title": "某公司重组预期升温",
        "summary": "……",
        "source_name": "证券时报",
        "url": "http://example.com/news/1",
        "publication_time": T0,
    }
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    company_id = int(company.id or 0)

    first = upsert_news(session, company_id, item)
    second = upsert_news(session, company_id, item)
    assert first is not None and second is not None
    assert int(first.id) == int(second.id)

    count = session.exec(select(func.count()).select_from(News)).one()
    assert int(count) == 1


def test_dry_run_does_not_write_business_tables(session):
    """dry-run 的语义：真实执行，但不写业务表（docs/03 §7.2）。"""
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    outcome = upsert_announcement(
        session, int(company.id), _raw(), _parsed(), dry_run=True
    )
    assert outcome.created is False
    assert "dry_run" in (outcome.skipped_reason or "")

    count = session.exec(select(func.count()).select_from(Announcement)).one()
    assert int(count) == 0

    news = upsert_news(session, int(company.id), {
        "external_id": "x", "title": "t", "source_name": "s", "url": "u",
        "publication_time": T0,
    }, dry_run=True)
    assert news is None


def test_announcement_classified_on_insert(session):
    """公告入库时就完成事件类型归类 —— 这是漏斗第二级的确定性过滤。"""
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    outcome = upsert_announcement(session, int(company.id), _raw(), _parsed())
    row = session.get(Announcement, outcome.announcement_id)
    assert row is not None
    assert row.event_type is EventType.RESTRUCTURING


def test_unmatched_announcement_has_no_event_type(session):
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    raw = RawAnnouncement(
        company_code="600xxx", document_id="ANN-X", title="关于变更公司注册地址的公告",
        announcement_type=None, publication_time=T0, url=URL,
    )
    outcome = upsert_announcement(session, int(company.id), raw, _parsed())
    row = session.get(Announcement, outcome.announcement_id)
    assert row is not None
    assert row.event_type is None, "未命中白名单的公告不应被强行归类"


def test_repeated_batch_keeps_row_count_stable(session):
    """一批 3 条公告跑两次 → 行数不变（INV-NF-04 的直接验证）。"""
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    company_id = int(company.id or 0)
    batch = [_raw(f"ANN-{i}") for i in range(3)]

    for raw in batch:
        upsert_announcement(session, company_id, raw, _parsed())
    first_count = session.exec(select(func.count()).select_from(Announcement)).one()

    for raw in batch:
        upsert_announcement(session, company_id, raw, _parsed())
    second_count = session.exec(select(func.count()).select_from(Announcement)).one()

    assert int(first_count) == int(second_count) == 3
    paragraphs = session.exec(select(func.count()).select_from(Paragraph)).one()
    assert int(paragraphs) == 6
