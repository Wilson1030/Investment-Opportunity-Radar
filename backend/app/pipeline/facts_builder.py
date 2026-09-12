"""DB → :class:`StrategyFacts`（纯数据）。

**为什么要这一层**：策略与评分只接受纯数据（docs/03 §4 的节点契约），
数据库读取集中在这里，因此规则层可以完全脱离数据库被单测。

派生逻辑（不是简单搬运）::

    FinancialMetric 序列        → 连续改善期数 / 连续亏损年数 / 现金流方向
    Event + Evidence 等级       → 每个事件的证据等级、公司证据池
    Event 类型与标题关键词       → 股东结构变化、市场关注、监管问询、历史失败
    News                        → 报道/聚类条数（E 类讨论的唯一去处）
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.facts import (
    CompanyFacts,
    EventFact,
    FinancialFacts,
    MarketFacts,
    ShareholderFacts,
    StrategyFacts,
)
from app.models.enums import EventType, ReliabilityLevel
from app.models.evidence import Evidence
from app.models.events import Event, EventCluster
from app.models.knowledge import (
    Announcement,
    Company,
    FinancialMetric,
    FinancialPeriod,
    Stock,
)

#: 判定「历史失败」的关键词
_FAILURE_KEYWORDS = ("终止", "失败", "撤回", "撤销", "未通过", "不予核准")
#: 判定「未回复的监管问询」
_INQUIRY_KEYWORDS = ("问询函", "监管函", "关注函", "警示函")
#: 判定「仍有前置审批未完成」
_PENDING_APPROVAL_KEYWORDS = ("尚需", "需取得", "需提交", "有待", "尚未完成", "尚需获得")
#: 判定「异常波动」
_ABNORMAL_KEYWORDS = ("异常波动", "交易异常")


# --------------------------------------------------------------------------- #
# 公司
# --------------------------------------------------------------------------- #
def build_company_facts(session: Session, company_id: int) -> CompanyFacts:
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"公司不存在：{company_id}")
    stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
    return CompanyFacts(
        id=company_id,
        name=company.name,
        code=stock.code if stock else "",
        is_st=company.is_st,
        industry=company.industry,
        industry_chain=tuple(company.industry_chain or ()),
        controlling_shareholder=company.controlling_shareholder,
        actual_controller=company.actual_controller,
    )


# --------------------------------------------------------------------------- #
# 事件（含每个事件的证据等级）
# --------------------------------------------------------------------------- #
def build_event_facts(session: Session, company_id: int) -> tuple[EventFact, ...]:
    events = session.exec(
        select(Event).where(Event.company_id == company_id).order_by(Event.event_time)  # type: ignore[attr-defined]
    ).all()
    if not events:
        return ()

    evidence_ids = {eid for e in events for eid in (e.evidence_ids or [])}
    levels: dict[int, ReliabilityLevel] = {}
    if evidence_ids:
        for row in session.exec(
            select(Evidence).where(Evidence.id.in_(evidence_ids))  # type: ignore[attr-defined]
        ).all():
            levels[int(row.id or 0)] = ReliabilityLevel(row.reliability_level)

    facts: list[EventFact] = []
    for event in events:
        event_levels = [levels[eid] for eid in (event.evidence_ids or []) if eid in levels]
        best = (
            min(event_levels, key=lambda lv: "ABCDE".index(lv.value))
            if event_levels
            else None
        )
        attributes = event.attributes or {}
        facts.append(
            EventFact(
                id=int(event.id or 0),
                event_type=EventType(event.event_type),
                title=event.title or "",
                summary=event.summary or "",
                event_time=event.event_time,
                importance=float(event.importance),
                certainty=float(event.certainty),
                evidence_ids=tuple(event.evidence_ids or ()),
                evidence_level=best,
                amount_ratio=float(attributes.get("amount_ratio", 0.0) or 0.0),
                counterparty_known=bool(attributes.get("counterparty_known", False)),
            )
        )
    return tuple(facts)


def collect_evidence_levels(session: Session, company_id: int) -> tuple[ReliabilityLevel, ...]:
    """该公司全部证据的等级池（决定确定性基线）。"""
    rows = session.exec(
        select(Evidence.reliability_level)
        .join(Announcement, Evidence.announcement_id == Announcement.id, isouter=True)  # type: ignore[arg-type]
        .where(Announcement.company_id == company_id)
    ).all()
    levels = [ReliabilityLevel(r if isinstance(r, str) else r) for r in rows]
    if levels:
        return tuple(levels)
    # 回退：从事件关联的证据取
    facts = build_event_facts(session, company_id)
    return tuple(f.evidence_level for f in facts if f.evidence_level is not None)


def newest_evidence_age_days(session: Session, company_id: int) -> float:
    rows = session.exec(
        select(Evidence.publication_time)
        .join(Announcement, Evidence.announcement_id == Announcement.id, isouter=True)  # type: ignore[arg-type]
        .where(Announcement.company_id == company_id)
        .order_by(Evidence.publication_time.desc())  # type: ignore[attr-defined]
        .limit(1)
    ).all()
    if not rows:
        return 0.0
    moment = rows[0]
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 86400)


# --------------------------------------------------------------------------- #
# 财务趋势
# --------------------------------------------------------------------------- #
def _series(
    session: Session, company_id: int, metric: str
) -> list[tuple[datetime, float | None, float | None]]:
    """返回 ``[(period_end, value, yoy)]``，按期间升序。"""
    rows = session.exec(
        select(FinancialPeriod.period_end, FinancialMetric.value, FinancialMetric.yoy)
        .join(FinancialPeriod, FinancialMetric.period_id == FinancialPeriod.id)  # type: ignore[arg-type]
        .where(FinancialPeriod.company_id == company_id, FinancialMetric.metric == metric)
        .order_by(FinancialPeriod.period_end)  # type: ignore[attr-defined]
    ).all()
    out: list[tuple[datetime, float | None, float | None]] = []
    for period_end, value, yoy in rows:
        if isinstance(period_end, str):
            period_end = datetime.fromisoformat(period_end)
        out.append((period_end, value, yoy))
    return out


def _trailing_count(
    series: list[tuple[datetime, float | None, float | None]],
    predicate,
    *,
    use_yoy: bool = False,
) -> int:
    """从最新一期往前数，连续满足条件的期数。"""
    count = 0
    for _, value, yoy in reversed(series):
        subject = yoy if use_yoy else value
        if subject is None or not predicate(subject):
            break
        count += 1
    return count


def build_financial_facts(session: Session, company_id: int) -> FinancialFacts:
    """从结构化财务指标推导趋势。

    **不在这里做「恶化 = 利空」的判断** —— 那属于规则层，而且必须以
    ``anomaly_note`` 归因为前提（INV-F1）。
    """
    net_profit = _series(session, company_id, "net_profit")
    revenue = _series(session, company_id, "revenue")
    margin = _series(session, company_id, "gross_margin")
    debt_ratio = _series(session, company_id, "debt_ratio")

    # 现金流：优先用总额（akshare 的财报表），没有则用**每股经营现金流**作代理 ——
    # 只看「符号」与「趋势」，两者都成立。代理方法会记录在代码注释里，不假装是总额。
    ocf = _series(session, company_id, "ocf")
    ocf_is_proxy = False
    if not ocf:
        ocf = _series(session, company_id, "ocf_per_share")
        ocf_is_proxy = bool(ocf)

    # 应收账款：没有余额时用**周转天数**的变化方向近似 ——
    # 「周转天数上升 且 营收增速 ≤ 0」才判定为应收账款问题（两者都恶化）
    receivable = _series(session, company_id, "receivable")
    receivable_days = _series(session, company_id, "receivable_days")

    if not any((net_profit, revenue, margin, ocf, receivable, debt_ratio)):
        return FinancialFacts()

    latest_ocf = ocf[-1][1] if ocf else None
    latest_ocf_yoy = ocf[-1][2] if ocf else None

    receivable_yoy = receivable[-1][2] if receivable else None
    revenue_yoy = revenue[-1][2] if revenue else None

    # 一次性因素归因（来自财务指标层的 anomaly_note）
    attributed = False
    anomaly_rows = session.exec(
        select(FinancialMetric.is_anomaly, FinancialMetric.anomaly_note)
        .join(FinancialPeriod, FinancialMetric.period_id == FinancialPeriod.id)  # type: ignore[arg-type]
        .where(FinancialPeriod.company_id == company_id, FinancialMetric.is_anomaly == True)  # noqa: E712
    ).all()
    for is_anomaly, note in anomaly_rows:
        if is_anomaly and note and any(
            kw in note for kw in ("一次性", "减值", "重组费用", "非经常性", "资产处置")
        ):
            attributed = True
            break

    return FinancialFacts(
        loss_years=_trailing_count(net_profit, lambda v: v < 0),
        revenue_improving_quarters=_trailing_count(revenue, lambda v: v > 0, use_yoy=True),
        margin_improving_quarters=_trailing_count(margin, lambda v: v > 0, use_yoy=True),
        # 注：``ocf`` 可能是每股代理值，但「为正 / 同比改善」的语义与总额一致
        ocf_positive=bool(latest_ocf is not None and latest_ocf > 0),
        ocf_improving=bool(latest_ocf_yoy is not None and latest_ocf_yoy > 0),
        ocf_is_proxy=ocf_is_proxy,
        profitable_years=_trailing_count(net_profit, lambda v: v > 0),
        receivable_growth_exceeds_revenue=(
            # 优先用余额同比（精确）
            bool(
                receivable_yoy is not None and revenue_yoy is not None
                and receivable_yoy > revenue_yoy
            )
            if receivable
            # 回退：周转天数上升 且 营收未增长（近似，两者都恶化才算）
            else bool(
                receivable_days
                and _trailing_count(receivable_days, lambda v: True) >= 0
                and len(receivable_days) >= 2
                and (receivable_days[-1][1] or 0) > (receivable_days[-2][1] or 0)
                and (revenue_yoy is None or revenue_yoy <= 0)
            )
        ),
        debt_ratio_rising=bool(
            _trailing_count(debt_ratio, lambda v: v > 0, use_yoy=True) > 0
        ),
        deteriorating_attributed_to_one_off=attributed,
    )


# --------------------------------------------------------------------------- #
# 股东结构 / 市场关注
# --------------------------------------------------------------------------- #
def build_shareholder_facts(
    session: Session, company_id: int, events: tuple[EventFact, ...]
) -> ShareholderFacts:
    has = lambda *types: any(e.event_type in types for e in events)  # noqa: E731
    control_titles = [e.title for e in events if e.event_type is EventType.CONTROL_CHANGE]
    buyback_events = [e for e in events if e.event_type is EventType.BUYBACK]

    return ShareholderFacts(
        controlling_shareholder_changed=has(EventType.CONTROL_CHANGE),
        actual_controller_changed=any("实际控制人" in t for t in control_titles),
        insider_buy=has(EventType.SHAREHOLDER_BUY),
        buyback=bool(buyback_events),
        # 启发式：回购规模是否显著（有金额占比 或 标题含「不低于」）
        buyback_scale_significant=any(
            e.amount_ratio >= 0.05 or "不低于" in e.title for e in buyback_events
        ),
        # 质押数据当前没有可靠来源 —— 保持 False，风险按基础严重度计（不假装知道）
        high_pledge=False,
        insider_sell=has(EventType.SHAREHOLDER_SELL),
    )


def build_market_facts(
    session: Session, company_id: int, events: tuple[EventFact, ...]
) -> MarketFacts:
    """市场关注度。

    ★ 这里就是 C / D / E 类信息的**唯一去处**（docs/04 §4.7）：
    它们能抬高「市场关注」，但不能抬高确定性，也不能抬高匹配度。
    """
    # 事件簇数量（新闻聚类结果，规格 §43）
    cluster_count = len(
        session.exec(
            select(EventCluster.id).where(EventCluster.company_id == company_id)
        ).all()
    )

    # E 类讨论：该公司公告所衍生的 market_discussion 类证据
    social_buzz = (
        session.exec(
            select(Evidence.id)
            .join(Announcement, Evidence.announcement_id == Announcement.id)  # type: ignore[arg-type]
            .where(
                Announcement.company_id == company_id,
                Evidence.assertion_kind == "market_discussion",
            )
            .limit(1)
        ).first()
        is not None
    )

    return MarketFacts(
        news_cluster_count=cluster_count,
        abnormal_volatility=any(
            any(kw in e.title for kw in _ABNORMAL_KEYWORDS) for e in events
        ),
        on_dragon_tiger=False,          # 需要行情数据源，MVP 未接入
        institutional_reports_delta=0,  # 需要研报数据源，MVP 未接入
        social_buzz=social_buzz,
    )


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def build_strategy_facts(session: Session, company_id: int) -> StrategyFacts:
    """组装一份完整的 :class:`StrategyFacts`（``open_question_count`` 先置 0）。"""
    events = build_event_facts(session, company_id)
    return StrategyFacts(
        company=build_company_facts(session, company_id),
        events=events,
        financials=build_financial_facts(session, company_id),
        shareholder=build_shareholder_facts(session, company_id, events),
        market=build_market_facts(session, company_id, events),
        # ★ 按标题关键词识别，而不是只看事件类型：分类器有优先级，
        #   「关于重大资产重组事项的问询函」的主类型是 RESTRUCTURING，
        #   若只认 REGULATORY_RISK 会丢掉监管信号。
        has_unanswered_inquiry=any(
            any(kw in e.title for kw in _INQUIRY_KEYWORDS) for e in events
        ),
        has_history_failure=any(
            any(kw in e.title for kw in _FAILURE_KEYWORDS)
            for e in events
            if e.event_type in (EventType.RESTRUCTURING, EventType.M_AND_A,
                                EventType.CONTROL_CHANGE, EventType.ASSET_INJECTION)
        ),
        has_late_stage_pending_approval=any(
            any(kw in e.title for kw in _PENDING_APPROVAL_KEYWORDS) for e in events
        ),
        has_conflicting_media=False,   # 需要口径比对，MVP 未接入
        evidence_levels=collect_evidence_levels(session, company_id),
        newest_evidence_age_days=newest_evidence_age_days(session, company_id),
    )


def with_open_question_count(facts: StrategyFacts, count: int) -> StrategyFacts:
    """第二遍：把待确认事项数量写回 facts（顺序由策略的 questions 模块决定）。

    为什么需要两遍：待确认事项本身依赖事实（哪些公告已经出现），
    而确定性维度又需要用它的数量扣分。
    """
    return replace(facts, open_question_count=count)


__all__ = [
    "build_company_facts",
    "build_event_facts",
    "build_financial_facts",
    "build_market_facts",
    "build_shareholder_facts",
    "build_strategy_facts",
    "collect_evidence_levels",
    "newest_evidence_age_days",
    "with_open_question_count",
]
