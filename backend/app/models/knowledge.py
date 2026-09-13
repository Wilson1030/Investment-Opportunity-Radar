"""知识层实体：Company / Stock / Announcement / Paragraph / News / Financial*。

对应 docs/02 §4.4 ~ §4.6。

设计约定
--------
* **只做表定义，不含业务逻辑**（docs/03 §9）。
* 不使用 SQLModel ``Relationship``，只用显式外键 int —— 减少 mapper 配置耦合，
  查询在 engine/ 层显式书写，便于将来拆分与测试。
* 列表 / 字典字段用 ``Column(JSON)``。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import EventType, ParseStatus, ReliabilityLevel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(SQLModel, table=True):
    """公司 —— 与 Stock 分离（规格 §30：投资逻辑发生在 Company / Event，而非 Price）。"""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    full_name: str | None = None
    unified_social_credit_code: str | None = Field(default=None, index=True)
    industry: str | None = Field(default=None, index=True)
    industry_chain: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    province: str | None = None

    # 关系字段（规格 §48 轻量知识图谱的起步形态）
    controlling_shareholder: str | None = None
    actual_controller: str | None = None

    # 筛选条件（注意：is_st 是筛选条件，**不是**投资逻辑 —— 规格 §5.6 示例 J，INV-C1）
    is_st: bool = Field(default=False, index=True)
    is_risk_warning: bool = Field(default=False, index=True)

    listed_at: date | None = None
    delisted_at: date | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Stock(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    code: str = Field(unique=True, index=True)
    exchange: str
    board: str | None = None
    name: str
    is_active: bool = True


class Announcement(SQLModel, table=True):
    """上市公司公告（规格 §7.2）。全文段落切分见 :class:`Paragraph`。"""

    __table_args__ = (UniqueConstraint("company_id", "document_id", name="uq_announcement_doc"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    document_id: str = Field(index=True)
    title: str
    announcement_type: str | None = None          # 原始类型（§7.2 清单）
    event_type: EventType | None = Field(default=None, index=True)  # 归类后的标准类型

    # 时间：事件发生时间与系统发现时间严格区分（规格 §40，INV-EV1）
    publication_time: datetime = Field(index=True)
    discovery_time: datetime = Field(default_factory=_utcnow, index=True)

    source_url: str
    raw_path: str | None = None
    raw_format: str | None = None                 # pdf / html
    fulltext: str | None = None
    parse_status: ParseStatus = Field(default=ParseStatus.OK, index=True)
    parse_error: str | None = None
    paragraph_count: int = 0
    ingest_run_id: int | None = Field(default=None, foreign_key="ingestrun.id")


class Paragraph(SQLModel, table=True):
    """段落切分 —— Evidence 定位到原文的最小单位（D10 / INV-E1 的校验依据）。"""

    __table_args__ = (
        UniqueConstraint("announcement_id", "page", "para_index", name="uq_paragraph_loc"),
    )

    id: int | None = Field(default=None, primary_key=True)
    announcement_id: int = Field(foreign_key="announcement.id", index=True)
    page: int
    para_index: int
    text: str
    char_start: int
    char_end: int


class News(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source_name", "external_id", name="uq_news_source"),)

    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(index=True)
    title: str
    summary: str | None = None
    source_name: str
    source_reliability: ReliabilityLevel = ReliabilityLevel.C
    publication_time: datetime = Field(index=True)
    discovery_time: datetime = Field(default_factory=_utcnow)
    url: str
    related_company_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    related_industries: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    event_type: EventType | None = None
    sentiment: str | None = None                  # positive / negative / neutral
    cluster_id: int | None = Field(default=None, foreign_key="eventcluster.id", index=True)


class FinancialPeriod(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("company_id", "period", "report_type", name="uq_fin_period"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    period: str                                   # 2026H1 / 2026Q3 / 2025A
    period_end: date
    report_type: str                              # annual / semi / q1 / q3 / express / forecast
    published_at: datetime | None = None
    source_url: str | None = None


class FinancialMetric(SQLModel, table=True):
    """结构化财务指标 + 同比 + 异常归因（M2-03 / M2-11）。

    INV-F1：单指标恶化**不得**直接判定为利空 —— 异常必须走 ``anomaly_note`` 归因。
    """

    __table_args__ = (UniqueConstraint("period_id", "metric", name="uq_fin_metric"),)

    id: int | None = Field(default=None, primary_key=True)
    period_id: int = Field(foreign_key="financialperiod.id", index=True)
    metric: str = Field(index=True)               # revenue / net_profit / ocf / receivable ...
    value: float | None = None
    unit: str | None = None
    yoy: float | None = None
    qoq: float | None = None
    is_anomaly: bool = False
    anomaly_note: str | None = None               # 「净利润下降」的可能原因


class ValuationSnapshot(SQLModel, table=True):
    """估值快照（规格 §46 的辅助信息层 + value 策略 C5 的判定依据）。

    ★ 为什么值得单独建表而不是只在内存里算：
    估值分位要能**复现与追溯** —— 「当时说它处于近三年 12% 分位」
    这个判断必须能在事后核对（分位是相对量，会随窗口滚动而变化）。
    所以连同 ``window_days`` 与 ``source_name`` 一起落库。

    ★ 分位的方向约定：**数值越小 = 估值越低**（0 = 近三年最便宜）。
    这个约定必须写死，否则某天算反了不会报错、只会静默输出相反结论。
    """

    __table_args__ = (
        UniqueConstraint("company_id", "as_of", name="uq_valuation_daily"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    as_of: date = Field(index=True)
    #: 总市值（亿元）
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    #: PE(TTM) 在窗口内的分位 ∈ [0,1]；越小越便宜
    pe_percentile: float | None = None
    #: PB 在窗口内的分位 ∈ [0,1]
    pb_percentile: float | None = None
    #: 分位计算所用的窗口天数（近三年 ≈ 1095）
    window_days: int = 1095
    source_name: str = "baidu_gushitong"
    source_url: str | None = None
