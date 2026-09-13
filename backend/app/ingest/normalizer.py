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
from app.models.knowledge import (
    Announcement,
    Company,
    FinancialMetric,
    FinancialPeriod,
    News,
    Paragraph,
    Stock,
)


@dataclass(frozen=True)
class UpsertOutcome:
    announcement_id: int | None
    created: bool
    paragraph_count: int
    skipped_reason: str | None = None


#: 从公司名识别风险警示状态 —— cninfo 的 secName 就是「*ST西发」「ST龙元」这种形态，
#: 信息已经在手里，不需要另找 ST 名单（实测 akshare 的东方财富接口在本环境不可达）。
_ST_NAME_MARKERS = ("*ST", "ST", "退市", "风险警示")  # 顺序无关，逐一判定


def infer_is_st(name: str) -> bool:
    """从公司名推断是否处于风险警示状态。

    ★ 为什么不能省：C4「经营困境背景」依赖 is_st，
    而真实链路拿不到 ST 名单时它恒为 False ——
    结果一堆名字里明明写着「*ST」的公司，coverage 全部相同、卡片毫无区分度。

    注意：ST 是 *ST 的子串，但两者都算风险警示，无需区分。
    """
    if not name:
        return False
    head = name.strip()[:4].upper()
    return any(marker.upper() in head for marker in _ST_NAME_MARKERS)


def upsert_company(
    session: Session, code: str, name: str = "", *, is_st: bool | None = None,
    commit: bool = True, exchange: str | None = None,
) -> Company:
    # 未显式指定时，从公司名推断（cninfo 的 secName 自带 ST 前缀）
    if is_st is None:
        is_st = infer_is_st(name)

    stock = session.exec(select(Stock).where(Stock.code == code)).first()
    if stock is not None:
        company = session.get(Company, stock.company_id)
        if company is not None:
            changed = False
            # 真实数据里公司名可能后到（先以代码占位）—— 补上，而不是新建一条
            if name and (not company.name or company.name == code):
                company.name = name
                changed = True
            if is_st and not company.is_st:
                company.is_st = True
                changed = True
            if changed:
                company.updated_at = datetime.now(timezone.utc)
                session.add(company)
                if commit:
                    session.commit()
            return company

    company = Company(name=name or code, is_st=bool(is_st), is_risk_warning=bool(is_st))
    session.add(company)
    session.flush()
    session.add(Stock(company_id=int(company.id or 0), code=code,
                      exchange=exchange or _exchange_of(code), name=name or code))
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


def upsert_financials(
    session: Session,
    company_id: int,
    periods: list,
    *,
    source_url: str | None = None,
    commit: bool = True,
) -> int:
    """写入结构化财务期与指标（幂等：同一 ``(company_id, period, report_type)`` 更新而非新增）。

    返回写入的**期数**。

    ★ 只有非 ``None`` 的指标才落库 —— 缺失就是不写，**不用 0 或均值补**
    （财务数字补错的代价比缺失大得多）。
    """
    from datetime import date as _date

    from app.ingest.financials import METRIC_UNITS, report_type_of

    written = 0
    for item in periods:
        if getattr(item, "is_empty", False):
            continue
        period_end = _date.fromisoformat(item.period_end)
        report_type = report_type_of(item.period_end)

        # ★ 按 ``period_end`` 查找，而不是按 ``period`` 文本 ——
        # ``period`` 的含义曾从「日期」修正为「报告期标签（2026H1）」，
        # 若按文本查找，同一条记录会被当成新记录再插一遍，**历史数据翻倍**。
        row = session.exec(
            select(FinancialPeriod).where(
                FinancialPeriod.company_id == company_id,
                FinancialPeriod.period_end == period_end,
            )
        ).first()
        if row is None:
            row = FinancialPeriod(
                company_id=company_id,
                period=item.label,
                period_end=period_end,
                report_type=report_type,
                source_url=source_url,
            )
            session.add(row)
            session.flush()
        else:
            row.period = item.label
            row.report_type = report_type
            session.add(row)
        written += 1

        period_id = int(row.id or 0)
        for metric, value in item.values.items():
            if value is None:
                continue
            yoy = item.yoy.get(metric)
            existing = session.exec(
                select(FinancialMetric).where(
                    FinancialMetric.period_id == period_id,
                    FinancialMetric.metric == metric,
                )
            ).first()
            unit = METRIC_UNITS.get(metric)
            if existing is None:
                session.add(FinancialMetric(
                    period_id=period_id, metric=metric, value=value, unit=unit, yoy=yoy,
                ))
            else:
                existing.value = value
                existing.yoy = yoy
                # 单位以「当前口径」为准（历史行可能没有单位）
                existing.unit = unit
                session.add(existing)

    if commit:
        session.commit()
    return written


def upsert_valuation(
    session: Session,
    company_id: int,
    snapshot,
    *,
    source_url: str | None = None,
    commit: bool = True,
):
    """写入一天的估值快照（幂等：同一 ``(company_id, as_of)`` 更新而非新增）。

    ★ 分位必须**连同窗口天数一起落库**：分位是相对量，
    没有窗口信息的事后核对是无意义的（「12% 分位」在不同窗口下含义不同）。

    返回写入的行；``snapshot.is_empty`` 时返回 ``None``（**不写空行** ——
    一行全是 NULL 的快照只会让「有没有数据」更难判断）。
    """
    from app.models.knowledge import ValuationSnapshot

    if getattr(snapshot, "is_empty", True):
        return None

    row = session.exec(
        select(ValuationSnapshot).where(
            ValuationSnapshot.company_id == company_id,
            ValuationSnapshot.as_of == snapshot.as_of,
        )
    ).first()
    if row is None:
        row = ValuationSnapshot(company_id=company_id, as_of=snapshot.as_of)
    row.market_cap = snapshot.market_cap
    row.pe_ttm = snapshot.pe_ttm
    row.pb = snapshot.pb
    row.pe_percentile = snapshot.pe_percentile
    row.pb_percentile = snapshot.pb_percentile
    row.window_days = snapshot.window_days
    row.source_url = source_url
    session.add(row)
    if commit:
        session.commit()
    return row


def upsert_news(
    session: Session,
    item,
    *,
    related_company_ids: list[int] | None = None,
    dry_run: bool = False,
    commit: bool = True,
) -> News | None:
    """写入一条新闻（幂等：``(source_name, external_id)`` 唯一）。

    接受 :class:`app.ingest.news.RawNews`。``related_company_ids`` 由调用方
    通过 :func:`app.ingest.news.match_companies` 匹配得到 ——
    **一条新闻可以关联多家公司**（例：「A 公司收购 B 公司」）。
    """
    existing = session.exec(
        select(News).where(
            News.source_name == item.source_name,
            News.external_id == item.external_id,
        )
    ).first()
    if existing is not None:
        return existing
    if dry_run:
        return None

    news = News(
        external_id=item.external_id,
        title=item.title,
        summary=item.summary,
        source_name=item.source_name,
        source_reliability=ReliabilityLevel.C,
        publication_time=item.published_at,
        discovery_time=datetime.now(timezone.utc),
        url=item.url,
        related_company_ids=list(related_company_ids or []),
        # 与公告走**同一套**分类器 —— 两处关键词不漂移
        event_type=classifier.classify_announcement(item.title),
    )
    session.add(news)
    if commit:
        session.commit()
        session.refresh(news)
    return news


def upsert_cluster(
    session: Session,
    company_id: int,
    draft,
    *,
    commit: bool = True,
):
    """写入 / 更新一家公司的事件簇（幂等：同一公司同一事件类型只保留一簇）。

    ★ 为什么按 ``(company_id, event_type)`` 而不是每次新建一簇：
    簇的数量会直接进入「市场关注度」维度并影响评分 ——
    每次采集都新建一簇的话，跑两次就有两倍的「关注度」。
    所以同一公司同一事件类型**就地更新**（成员数、要点、时间范围）。
    """
    from app.models.events import EventCluster

    row = session.exec(
        select(EventCluster).where(
            EventCluster.company_id == company_id,
            EventCluster.event_type == draft.event_type,
        )
    ).first()
    if row is None:
        row = EventCluster(company_id=company_id, label=draft.label)
    row.label = draft.label
    row.event_type = draft.event_type
    row.member_count = draft.member_count
    row.key_points = list(draft.key_points)
    row.first_seen = draft.first_seen or row.first_seen
    row.last_seen = draft.last_seen or row.last_seen
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    return row

def source_type_of(raw: RawAnnouncement) -> SourceType:
    return SourceType.ANNOUNCEMENT


__all__ = [
    "UpsertOutcome",
    "replace_paragraphs",
    "upsert_financials",
    "source_type_of",
    "upsert_announcement",
    "upsert_company",
    "upsert_news",
]
