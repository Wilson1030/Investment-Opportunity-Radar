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
    """同一条新闻重复采集只落一条（``(source_name, external_id)`` 唯一）。

    ★ 签名改过：现在接受 :class:`app.ingest.news.RawNews`，
    并由调用方传入 ``related_company_ids``（一条新闻可关联多家公司）。
    原先接受 dict + 单个 company_id —— 那种形状无法表达
    「A 公司收购 B 公司」这类同时涉及两家公司的新闻。
    """
    from app.ingest.news import RawNews

    item = RawNews(
        source_name="证券时报",
        external_id="http://example.com/news/1",
        title="某公司重组预期升温",
        summary="……",
        url="http://example.com/news/1",
        published_at=T0,
    )
    company = upsert_company(session, "600xxx", "ST XXX", is_st=True)
    company_id = int(company.id or 0)

    first = upsert_news(session, item, related_company_ids=[company_id])
    second = upsert_news(session, item, related_company_ids=[company_id])
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

    from app.ingest.news import RawNews

    news = upsert_news(session, RawNews(
        source_name="s", external_id="x", title="t", summary="",
        url="u", published_at=T0,
    ), dry_run=True)
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


# --------------------------------------------------------------------------- #
# 从公司名推断 ST 状态（cninfo 的 secName 自带前缀）
# --------------------------------------------------------------------------- #
def test_infer_is_st_from_company_name():
    """★ 真实链路拿不到 ST 名单时，is_st 恒为 False ——
    结果名字里明明写着「*ST」的公司 coverage 全部相同、卡片毫无区分度。
    cninfo 的 secName 就是「*ST西发」这种形态，信息本来就在手里。
    """
    from app.ingest.normalizer import infer_is_st

    for name in ["*ST西发", "ST龙元", "ST东时", "*ST沐邦", "ST加加", "ST新元"]:
        assert infer_is_st(name) is True, name
    for name in ["南网能源", "天桥起重", "西藏发展", ""]:
        assert infer_is_st(name) is False, name


def test_upsert_company_infers_st_when_not_specified(session):
    from app.ingest.normalizer import upsert_company

    company = upsert_company(session, "600001", "*ST测试")
    assert company.is_st is True
    assert company.is_risk_warning is True

    normal = upsert_company(session, "600002", "正常公司")
    assert normal.is_st is False

    # 显式指定时以参数为准（不猜）
    forced = upsert_company(session, "600003", "*ST仍按参数", is_st=False)
    assert forced.is_st is False
