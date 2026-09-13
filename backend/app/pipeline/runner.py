"""Pipeline 编排：`python -m app.ingest` 的实现。

    Stage 1  候选池          scope 过滤 → 约 300 只（D11）
    Stage 2  公告采集+解析    cninfo 列表 → 全文 → 段落切分（D10）
    Stage 3  事件抽取        规则预筛 → LLM 抽取 → **证据闸门** → Event + Evidence
    Stage 4  机会组装        策略命中 → Thesis → 评分 → Opportunity + 待确认 + 状态
    Stage 5  dry-run 判定     事实写库、判断不写库，并把报告 dump 到 data/cache/dry_run/

**dry-run 语义（修订版）**

    ✅ 写：IngestRun（带 dry_run=True）→ 保证 dry-run 期间也能看到漏斗与质量指标
    ✅ 写：Company / Stock / Announcement / Paragraph
           —— 它们是**事实**，幂等且无害
    ❌ 不写：Event / Evidence / Thesis / Opportunity / Score / OpenQuestion / Alert
           —— 它们是**判断**，未经校准不应进入系统

区分点是「**事实 vs 判断**」，而不是「是否写库」：否则 LLM 抽取阶段没有输入，
dry-run 反而看不到 AI 链路的质量（这正是风险 R3 最需要数据的地方）。

两个数据源::

    --source mock    确定性离线（规则合成抽取结果）→ 用于回归整条链路
    --source cninfo  真实公告 + 真实 LLM（默认）

产出必须回答的三个问题（docs/03 §8）：
    1. 为什么今天只有 N 张卡？   → funnel.drop_at()
    2. 数据质量如何？             → quality.parse_failure_rate
    3. 本机 4B 模型能否胜任？     → quality.llm_schema_failure_rate
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.ai.cache import SqlNodeCache
from app.ai.nodes import EXTRACT_EVENT
from app.ai.provider import LlmError, ScriptedProvider, build_provider
from app.ai.runner import NodeRunner
from app.ai.schemas import (
    AnnouncementInput,
    CompanyInput,
    ExtractEventInput,
    ParagraphInput,
)
from app.config import settings
from app.db import engine, init_db
from app.engine import classifier, funnel
from app.engine import scope as scope_engine
from app.ingest import parcel
from app.ingest.base import AdapterError, RawAnnouncement
from app.ingest.cninfo import CninfoAdapter
from app.ingest.normalizer import upsert_announcement, upsert_company
from app.models.audit import IngestRun
from app.models.events import Event
from app.models.knowledge import Announcement, Company, Paragraph, Stock
from app.pipeline import event_writer, mock_source, opportunity_builder, profile_seed
from app.pipeline.opportunity_builder import OpportunityBuildResult, build_opportunities

SOURCE_MOCK = "mock"
SOURCE_CNINFO = "cninfo"

#: 候选池构建方式
#:   market  = 事件优先：全市场按日查询 → 关键词预筛（只用 cninfo，**默认**）
#:   company = 公司优先：先取 ST 名单再逐家查（需要 akshare；实测本环境不可达）
POOL_MARKET = "market"
POOL_COMPANY = "company"


@dataclass
class PipelineOptions:
    stage: str = "full"                     # full | incremental | rescore
    dry_run: bool = True
    limit: int | None = None
    scope: str = settings.ingest_scope
    source: str = SOURCE_MOCK
    #: 是否调用 LLM 做事件抽取（mock 源忽略此项，使用规则合成）
    with_llm: bool = True
    #: 仅为前 N 条公告调用 LLM（本地 4B 慢，先用小样本验证）
    llm_limit: int | None = 3
    #: 候选池构建方式（见 POOL_MARKET / POOL_COMPANY）
    pool: str = POOL_MARKET
    #: 全市场查询最多翻多少页（每页 30 条）
    market_pages: int = 20
    #: 单条公告送入 LLM 的最大字符数（None = 用 settings 默认值）
    max_input_chars: int | None = None
    #: cninfo 全文检索关键词（空 = 不检索，取全市场；默认读 .env 的 INGEST_SEARCHKEY）
    searchkey: str = ""
    #: 截断时最多保留多少段落（None = 用 settings 默认 14）
    max_input_paragraphs: int | None = None
    #: 是否对入池机会跑 AI 分析（hunt_risk → analyze → score_semantic）。
    #: 关闭后只出规则分 —— 用于调试、降级、或不想花 token 的场景。
    with_ai_analysis: bool = True
    #: 抽取阶段的并发度。**默认 1（行为与串行完全一致）**。
    #:
    #: ★ 为什么做成可配而不是直接调大：实测 Ollama 默认**串行**处理请求，
    #: 单纯加线程没有收益。要真正提速需要同时满足其一：
    #:   · 调大服务端并行度（`OLLAMA_NUM_PARALLEL=4`）—— 单 GPU 上约 1.5~2x
    #:   · 换云端抽取 —— 10~50x，但公告内容会出本机（成本/隐私取舍，由用户定）
    #: 把「并发能力」与「用哪个 provider」解耦：能力先备好，provider 随时可换。
    extract_workers: int = 1
    lookback_days: int | None = None
    profile_template: str | None = profile_seed.DEFAULT_TEMPLATE


@dataclass
class PipelineOutcome:
    report: funnel.PipelineReport
    opportunities: list[OpportunityBuildResult] = field(default_factory=list)
    extract_stats: dict = field(default_factory=dict)
    #: AI 分析阶段（hunt_risk / analyze / score_semantic）的调用统计
    analysis_stats: dict = field(default_factory=dict)
    company_ids: list[int] = field(default_factory=list)
    #: 逐条抽取结果（dry-run 的核心可读产出：让人核对 AI 判断的质量）
    extractions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = self.report.to_dict()
        payload["opportunities"] = [
            {
                "thesis_type": r.thesis_type,
                "created": r.created,
                "opportunity_id": r.opportunity_id,
                "coverage": round(r.coverage, 4),
                "match_score": r.match_score,
                "rule_score": None if r.rule_score is None else round(r.rule_score, 4),
                "risk_score": r.risk_score,
                "status": r.status,
                "invalidated": r.invalidated,
                "reason": r.reason,
            }
            for r in self.opportunities
        ]
        payload["llm"] = self.extract_stats
        payload["llm_analysis"] = self.analysis_stats
        payload["llm_calls_total"] = (
            int(self.extract_stats.get("calls", 0))
            + int(self.analysis_stats.get("calls", 0))
        )
        payload["companies"] = len(self.company_ids)
        payload["extractions"] = self.extractions
        return payload


def _dry_run_dir() -> Path:
    path = settings.cache_dir / "dry_run" / date.today().isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
def effective_dry_run(options: PipelineOptions) -> bool:
    """mock 源是**本地夹具**：它必须真实写库，否则无法验证「DB → 机会卡」这一段。

    真实数据源的 dry-run 语义见 :func:`run_pipeline` 的文档字符串。
    """
    if options.source == SOURCE_MOCK:
        return False
    return options.dry_run


def run_pipeline(options: PipelineOptions | None = None) -> PipelineOutcome:
    options = options or PipelineOptions()
    # 未显式传参时，用 .env 里的默认值（让 GitHub 用户只改 .env 就能定制）
    if not options.searchkey:
        options.searchkey = settings.ingest_searchkey
    if options.with_ai_analysis is True:
        options.with_ai_analysis = settings.with_ai_analysis
    options.lookback_days = options.lookback_days or settings.ingest_lookback_days
    if options.max_input_chars:
        settings.llm_max_input_chars = options.max_input_chars
    if options.max_input_paragraphs:
        settings.llm_max_paragraphs = options.max_input_paragraphs
    write = not effective_dry_run(options)
    if options.source == SOURCE_MOCK and options.dry_run:
        print(
            "[mock] 注意：mock 源会真实写库（它是本地夹具，用于验证 DB → 机会卡 的全链路）。\n"
            "       如需回到干净状态：删除 backend/data/radar.db 后重新运行。"
        )
    options.dry_run = not write

    settings.ensure_dirs()
    init_db()

    report = funnel.PipelineReport(
        scope=options.scope, dry_run=options.dry_run, stage_name=options.stage
    )
    started = datetime.now(timezone.utc)
    outcome = PipelineOutcome(report=report)

    with Session(engine) as session:
        run = _start_run(session, options, report)

        profile = profile_seed.get_or_create_default_profile(session, options.profile_template)
        weights = profile_seed.profile_weights(session, int(profile.id or 0))
        report.quality.field_missing_rate = 0.0
        del weights  # 权重在 build_opportunities 内按 profile_id 重新读取

        if options.source == SOURCE_MOCK:
            created = mock_source.seed(session, commit=True)
            print(f"[mock] 造数完成：{created}")
            company_ids = [
                int(c.id or 0) for c in session.exec(select(Company)).all()
            ]
            report.funnel.record("candidates", len(company_ids))
            announcements = mock_source.pending_announcements(session)
            report.funnel.record("announcements_fetched", len(announcements))
            report.funnel.record("passed_prefilter", len(announcements))
        else:
            company_ids, announcements = _collect_cninfo(session, options, report)

        outcome.company_ids = company_ids

        # ---------- Stage 3：事件抽取 ----------
        _extract_events(session, options, report, outcome, announcements)

        # ---------- Stage 4：机会组装（含 AI 分析）----------
        _build_opportunities(
            session, options, report, outcome, company_ids, profile.id,
            analysis_runner=_build_analysis_runner(session, options),
        )

        # ---------- 收尾 ----------
        report.total_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        _finish_run(session, run, options, report)

    if options.dry_run:
        target = _dry_run_dir() / f"pipeline_{options.source}_{options.stage}.json"
        target.write_text(
            json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[dry-run] 报告已写入 {target}（业务表未被修改）")

    return outcome


# --------------------------------------------------------------------------- #
def _start_run(session: Session, options: PipelineOptions, report: funnel.PipelineReport) -> IngestRun | None:
    """观测记录在 dry-run 下**也要写** —— 否则 dry-run 期间看不到漏斗与质量指标。"""
    run = IngestRun(
        adapter=options.source,
        scope=options.scope,
        dry_run=options.dry_run,
        stage=options.stage,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    report.run_id = int(run.id or 0)
    return run


def _finish_run(
    session: Session,
    run: IngestRun | None,
    options: PipelineOptions,
    report: funnel.PipelineReport,
) -> None:
    if run is None:  # pragma: no cover - 防御
        return
    run.finished_at = datetime.now(timezone.utc)
    run.items_found = report.funnel.announcements_fetched
    run.items_new = report.funnel.events_extracted
    run.items_skipped = report.funnel.announcements_skipped
    run.items_failed = len([e for e in report.errors if e.get("stage") != "info"])
    run.funnel = report.funnel.to_dict()
    run.quality = report.quality.to_dict()
    run.errors = report.errors
    run.error_summary = "; ".join(e.get("error", "") for e in report.errors[:3]) or None
    session.add(run)
    session.commit()


# --------------------------------------------------------------------------- #
# Stage 1 + 2：候选池与采集
# --------------------------------------------------------------------------- #
def _collect_cninfo(
    session: Session, options: PipelineOptions, report: funnel.PipelineReport
) -> tuple[list[int], list[Announcement]]:
    """真实采集。

    ``pool=market``（默认）走**事件优先**：全市场按日查询 → 关键词预筛 → 只解析命中项。
    这条路只需要 cninfo，不需要 akshare。
    """
    adapter = CninfoAdapter()
    end = date.today()
    start = end - timedelta(days=options.lookback_days or 90)

    if options.pool == POOL_MARKET:
        return _collect_market_first(session, options, report, adapter, start, end)

    candidates = _candidate_pairs(session, options, report)
    if options.limit:
        candidates = candidates[: options.limit]
    report.funnel.record("candidates", len(candidates))

    parse_attempts = 0
    parse_failures = 0

    for company_id, code in candidates:
        try:
            fetched = adapter.list_announcements(code, start, end, max_pages=1)
        except AdapterError as exc:
            report.add_error("cninfo_query", str(exc), code=code)
            continue

        for error in fetched.errors:
            report.add_error(error.get("stage", "cninfo"), error.get("error", ""), code=code)

        for raw in fetched.items:
            report.funnel.increment("announcements_fetched")
            if not classifier.passes_prefilter(raw.title, raw.announcement_type):
                report.funnel.increment("announcements_skipped")
                continue
            report.funnel.increment("passed_prefilter")

            parsed = None
            if settings.store_announcement_fulltext:
                parse_attempts += 1
                parsed = _parse_remote(adapter, raw, report)
                if parsed is None or parsed.parse_status != "ok":
                    parse_failures += 1

            outcome = upsert_announcement(
                session, company_id, raw, parsed,
                ingest_run_id=report.run_id, dry_run=False,   # 原始数据是事实，幂等且无害
            )
            if outcome.skipped_reason and "幂等" in outcome.skipped_reason:
                report.funnel.increment("announcements_skipped")

    report.quality.parse_failure_rate = parse_failures / parse_attempts if parse_attempts else 0.0
    if parse_attempts:
        report.quality.needs_ocr = parse_failures

    announcements = _pending_announcements(session)
    return [cid for cid, _ in candidates], announcements



def _collect_market_first(
    session: Session,
    options: PipelineOptions,
    report: funnel.PipelineReport,
    adapter: CninfoAdapter,
    start: date,
    end: date,
) -> tuple[list[int], list[Announcement]]:
    """Stage 0 + 1 + 2：全市场公告 → 关键词预筛 → 候选公司。

    漏斗：全市场数千条/3 天 → 命中白名单的少数条 → 去重后的候选公司。
    """
    fetched = adapter.list_market_announcements(
        start, end, page_size=30, max_pages=options.market_pages,
        searchkey=options.searchkey,
    )
    for error in fetched.errors:
        report.add_error(error.get("stage", "cninfo"), error.get("error", ""))

    report.funnel.record("announcements_fetched", len(fetched.items))
    scope_label = f"全文检索「{options.searchkey}」" if options.searchkey else "全市场扫描"
    print(
        f"[cninfo] {scope_label} {start} ~ {end}：{len(fetched.items)} 条公告"
        f"（{options.market_pages} 页 × 30；查询错误 {len(fetched.errors)} 条）"
    )

    matched: list[RawAnnouncement] = []
    for raw in fetched.items:
        if not classifier.passes_prefilter(raw.title, raw.announcement_type):
            report.funnel.increment("announcements_skipped")
            continue
        matched.append(raw)
    report.funnel.record("passed_prefilter", len(matched))

    distribution: dict[str, int] = {}
    for raw in matched:
        event_type = classifier.classify_announcement(raw.title, raw.announcement_type)
        key = event_type.value if event_type else "?"
        distribution[key] = distribution.get(key, 0) + 1
    print(f"[cninfo] 通过关键词预筛：{len(matched)} 条 → 事件类型分布 {distribution}")

    if options.limit:
        matched = matched[: options.limit]

    parse_attempts = 0
    parse_failures = 0
    company_ids: list[int] = []

    for raw in matched:
        company = upsert_company(session, raw.company_code, raw.company_name, commit=False)
        company_id = int(company.id or 0)
        if company_id not in company_ids:
            company_ids.append(company_id)

        parsed = None
        if settings.store_announcement_fulltext:
            parse_attempts += 1
            parsed = _parse_remote(adapter, raw, report)
            if parsed is None or parsed.parse_status != "ok":
                parse_failures += 1

        upsert_announcement(
            session, company_id, raw, parsed,
            ingest_run_id=report.run_id, dry_run=False,   # 原始数据是事实，幂等且无害
            commit=False,
        )

    session.commit()

    report.funnel.record("candidates", len(company_ids))
    report.quality.parse_failure_rate = (
        parse_failures / parse_attempts if parse_attempts else 0.0
    )
    report.quality.needs_ocr = parse_failures
    print(
        f"[cninfo] 候选公司 {len(company_ids)} 家 | 全文解析 {parse_attempts} 条，"
        f"失败/需 OCR {parse_failures} 条（失败率 {report.quality.parse_failure_rate:.2f}）"
    )

    _collect_financials(session, company_ids, report)
    _collect_valuation(session, company_ids, report)
    _collect_news(session, company_ids, report)

    return company_ids, _pending_announcements(session)


def _collect_news(
    session: Session, company_ids: list[int], report: funnel.PipelineReport
) -> None:
    """采集财经新闻并聚类（规格 §42 / §43）。

    ★ 只有全市场新闻源（财联社电报 / 新浪财经），没有「按公司查新闻」的接口。
    所以流程是：**先抓全市场 → 再用公司名 / 代码匹配 → 按事件类型聚簇**。
    匹配不到任何候选公司时也不报错（今天的新闻可能确实与候选池无关）。

    聚类结果进入 ``MarketFacts.news_cluster_count`` →
    「市场关注度」维度与 ``value`` 策略的 C6（低关注度加分）。
    """
    from app.ingest.normalizer import upsert_cluster, upsert_news
    from app.ingest.news import NewsSource, cluster_news, match_companies
    from app.models.knowledge import Company, Stock

    try:
        items = NewsSource().fetch(limit=50)
    except AdapterError as exc:
        report.add_error("news", str(exc))
        return

    # 候选公司清单（用于把新闻关联到公司）
    companies: list[tuple[int, str, str]] = []
    for company_id in company_ids:
        company = session.get(Company, company_id)
        stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
        if company is not None:
            companies.append((company_id, company.name or "", stock.code if stock else ""))

    stored = 0
    for item in items:
        related = match_companies(f"{item.title} {item.summary}", companies)
        if upsert_news(session, item, related_company_ids=related, commit=False) is not None:
            stored += 1
    session.commit()

    drafts = cluster_news(items, companies)
    for company_id, draft in drafts.items():
        upsert_cluster(session, company_id, draft, commit=False)
    session.commit()

    print(f"[新闻] 抓取 {len(items)} 条，落库 {stored} 条，"
          f"聚类 {len(drafts)} 簇（覆盖 {len(drafts)}/{len(company_ids)} 家候选）")


def _collect_valuation(
    session: Session, company_ids: list[int], report: funnel.PipelineReport
) -> None:
    """为候选公司采估值快照（规格 §46 的辅助信息层）。

    ★ 为什么必须在候选池阶段采：``value``（价值发现）策略的 C5
    「估值处于历史较低区间」权重 0.20 —— 不采的话这条永远返回「未采集」，
    价值策略就只能靠「有没有分红公告」判断，等于没有尺子。

    数据源用百度股市通（东方财富估值接口在本环境不可达）。
    单只失败不阻塞整批 —— 记录后继续。
    """
    from app.ingest.normalizer import upsert_valuation
    from app.ingest.valuation import ValuationSource
    from app.models.knowledge import Stock

    source = ValuationSource()
    ok = 0
    for company_id in company_ids:
        stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
        if stock is None:
            continue
        try:
            snapshot = source.fetch(stock.code)
        except AdapterError as exc:
            report.add_error("valuation", str(exc), code=stock.code)
            continue
        upsert_valuation(
            session, company_id, snapshot,
            source_url=source.source_url(stock.code), commit=False,
        )
        ok += 1

    session.commit()
    print(f"[估值] {ok}/{len(company_ids)} 家取到估值快照（百度股市通）")


def _collect_financials(
    session: Session, company_ids: list[int], report: funnel.PipelineReport
) -> None:
    """为候选公司采结构化财务数据。

    ★ 为什么必须在候选池阶段就采：``C4``（经营困境）、``FUNDAMENTALS``、``RISK``
    三个维度全都依赖财务数据。不采的话所有机会卡的这三项都是同一个数，
    **区分度只剩「催化剂阶段」一个维度**（首次 live 实测就是这个结果）。

    数据源用同花顺（东方财富在本环境不可达），见 :mod:`app.ingest.financials`。
    单只失败不阻塞整批 —— 记录错误后继续。
    """
    from app.ingest.financials import FinancialSource
    from app.engine.anomaly import attribute_latest
    from app.ingest.normalizer import upsert_financials
    from app.models.knowledge import Stock

    source = FinancialSource()
    ok = 0
    for company_id in company_ids:
        stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
        if stock is None:
            continue
        try:
            periods = source.fetch(stock.code)
        except AdapterError as exc:
            report.add_error("financials", str(exc), code=stock.code)
            continue
        if not periods:
            report.add_error("financials", "未取到财务期", code=stock.code)
            continue
        upsert_financials(session, company_id, periods, commit=False)
        # ★ INV-F1：异常必须归因。这一步就是那条不变量的**写入端** ——
        #   在此之前 is_anomaly / anomaly_note 从来没被写过，
        #   于是「已归因则不扣分」的规则从未生效过。
        attribute_latest(session, company_id)
        ok += 1

    session.commit()
    report.quality.field_missing_rate = (
        1.0 - ok / len(company_ids) if company_ids else 0.0
    )
    print(f"[财务] {ok}/{len(company_ids)} 家取到结构化财务（同花顺）")


def _candidate_pairs(
    session: Session, options: PipelineOptions, report: funnel.PipelineReport
) -> list[tuple[int, str]]:
    """候选池：优先用库里的，库为空时用 akshare 的 ST 名单冷启动。"""
    result = scope_engine.select_candidates(
        session,
        scope_engine.ScopeConfig(
            scope=options.scope, lookback_days=options.lookback_days or 90
        ),
    )
    pairs: list[tuple[int, str]] = []
    for company_id in result.company_ids:
        stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
        if stock is not None:
            pairs.append((company_id, stock.code))
    if pairs:
        return pairs

    try:
        from app.ingest.akshare_source import AkshareSource

        codes = AkshareSource().st_company_codes()
    except (AdapterError, ImportError, Exception) as exc:  # noqa: BLE001
        report.add_error(
            "candidate_pool",
            f"无法获取 ST 名单（{type(exc).__name__}: {exc}）；候选池为空",
        )
        return []

    from app.ingest.normalizer import upsert_company

    for code in codes:
        company = upsert_company(session, code, is_st=True, commit=False)
        pairs.append((int(company.id or 0), code))
    session.commit()
    return pairs


def _parse_remote(adapter: CninfoAdapter, raw: RawAnnouncement, report: funnel.PipelineReport):
    try:
        content = adapter.fetch_document(raw.url)
    except AdapterError as exc:
        report.add_error("download", str(exc), document_id=raw.document_id)
        return None

    if content[:4] == b"%PDF":
        tmp = settings.raw_dir / f"{raw.document_id}.pdf"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(content)
        return parcel.parse_pdf(tmp)
    if raw.url.lower().endswith((".html", ".htm")):
        return parcel.parse_html(content.decode("utf-8", errors="ignore"))

    report.add_error("parse_format", f"未知文档格式：{raw.url}", document_id=raw.document_id)
    return None


# --------------------------------------------------------------------------- #
# Stage 3：事件抽取（含证据闸门）
# --------------------------------------------------------------------------- #
def _pending_announcements(session: Session) -> list[Announcement]:
    rows = session.exec(
        select(Announcement).where(Announcement.event_type.is_not(None))  # type: ignore[union-attr]
    ).all()
    pending: list[Announcement] = []
    for row in rows:
        exists = session.exec(
            select(Event).where(
                Event.company_id == row.company_id, Event.source_url == row.source_url
            )
        ).first()
        if exists is None:
            pending.append(row)
    return pending


def _extract_events(
    session: Session,
    options: PipelineOptions,
    report: funnel.PipelineReport,
    outcome: PipelineOutcome,
    announcements: list[Announcement],
) -> None:
    runner: NodeRunner | None = None
    if options.source != SOURCE_MOCK and options.with_llm:
        provider_name, model, base_url, api_key = settings.llm_for("extract")
        provider = build_provider(provider_name, base_url, api_key)
        runner = NodeRunner(
            provider=provider,
            cache=SqlNodeCache() if not options.dry_run else _NoCache(),
            model=model,
            max_attempts=settings.llm_max_attempts,
            timeout_seconds=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
            disable_thinking=settings.llm_disable_thinking,
        )
        print(f"[llm] 抽取层 {provider_name}/{model} @ {base_url}")

    # ---- 准备待抽取的公告（含段落）----
    pending: list[tuple[object, list]] = []
    for announcement in announcements:
        if options.source != SOURCE_MOCK and len(pending) >= (options.llm_limit or 10**9):
            report.add_error(
                "info",
                f"已达 --llm-limit {options.llm_limit}，剩余公告本轮不再调用 LLM",
            )
            break
        paragraphs = mock_source.paragraph_objects(session, int(announcement.id or 0))
        if not paragraphs:
            report.add_error(
                "extract_skip", "公告无可用于定位证据的段落（解析失败或扫描件）",
                document_id=announcement.document_id,
            )
            continue
        pending.append((announcement, paragraphs))

    # ---- 并发调用 LLM（节点是纯函数），结果按顺序回收 ----
    # ★ 关键约束：**并发只包住 LLM 调用，落库仍在主线程串行执行** ——
    #   SQLite 多线程写不安全，缓存 IO 也已在 SqlNodeCache 内串行化。
    workers = max(1, int(options.extract_workers))
    if workers > 1 and len(pending) > 1:
        print(f"[llm] 并发抽取：{workers} 线程 / {len(pending)} 条公告")

    def _call(item):
        announcement, paragraphs = item
        try:
            return _extract_one(session, announcement, paragraphs, options, runner)
        except (LlmError, ValueError) as exc:
            return exc

    if workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            extracted = list(pool.map(_call, pending))
    else:
        extracted = [_call(item) for item in pending]

    # ---- 串行落库 ----
    for (announcement, _paragraphs), extraction in zip(pending, extracted):
        if isinstance(extraction, Exception):
            report.add_error(
                "extract_failed", str(extraction), document_id=announcement.document_id
            )
            continue
        if extraction is None:
            report.add_error(
                "extract_failed", "抽取未通过 Schema 校验（已重试）",
                document_id=announcement.document_id,
            )
            continue

        result = event_writer.persist_extraction(
            session, int(announcement.company_id), announcement, extraction,
            dry_run=options.dry_run,     # ★ 判断类数据：dry-run 不写
        )
        if result.gate_passed:
            # ★ 统计「通过证据闸门」而不是「落库成功」：dry-run 下判断类数据不落库，
            #   但抽取质量恰恰是 dry-run 最需要被看见的东西
            report.funnel.increment("events_extracted")

        rule_type = (
            announcement.event_type.value
            if hasattr(announcement.event_type, "value")
            else str(announcement.event_type or "")
        )
        outcome.extractions.append({
            "document_id": announcement.document_id,
            "company_code": _company_code(session, announcement),
            "announcement_title": announcement.title,
            # ★ 规则层与 LLM 层的判定都要留下：抽样核对时既要知道结论，也要知道分歧
            "rule_event_type": rule_type,
            "event_type": result.event_type,
            "agreement": bool(rule_type) and rule_type == result.event_type,
            "title": result.title,
            "gate_passed": result.gate_passed,
            "accepted_slices": result.accepted_slices,
            "accepted_texts": [
                {"page": page, "para_index": para, "text": text}
                for page, para, text in result.accepted_texts
            ],
            "rejected": [{"at": at, "reason": reason} for at, reason in result.rejected_slices],
            "event_time_source": result.event_time_source,
            "persisted": result.created,
            "skipped_reason": result.skipped_reason,
            "source_url": announcement.source_url,
        })

        if result.skipped_reason and "证据全部被拒" in result.skipped_reason:
            report.add_error(
                "evidence_gate", result.skipped_reason,
                document_id=announcement.document_id,
                reasons=result.rejection_reasons,
            )
        report.quality.evidence_rejected += len(result.rejected_slices)
        for reason, _ in result.rejected_slices:
            key = reason.split("：", 1)[0] if "：" in reason else reason
            report.quality.evidence_rejected_reasons[key] = (
                report.quality.evidence_rejected_reasons.get(key, 0) + 1
            )

    if runner is not None:
        outcome.extract_stats = runner.stats
        report.quality.llm_schema_failure_rate = runner.stats["schema_failure_rate"]
        report.quality.llm_cached_rate = runner.stats["cached_rate"]
        report.llm_ms = sum(r.latency_ms or 0 for r in runner.log)


def _company_code(session, announcement) -> str:
    stock = session.exec(
        select(Stock).where(Stock.company_id == announcement.company_id)
    ).first()
    return stock.code if stock else ""


def _truncate_paragraphs(announcement, paragraphs, max_chars: int, max_paragraphs: int):
    """按「与公告类型相关的关键词」挑段落，而不是盲目截前 N 字。

    真实公告（半年报 / 年报）可达数万字，但真正支撑事件判断的内容集中在
    「交易概述 / 控股股东 / 风险提示」等小节；会计附注对事件抽取没有帮助，
    却占了绝大部分长度。所以：
      1. 先按关键词命中数排序取前 ``max_paragraphs`` 条
      2. 再按原顺序（page, para_index）恢复顺序 —— 保持叙事连贯
      3. 受 ``max_chars`` 字符预算约束
    """
    event_type = classifier.classify_announcement(announcement.title)
    keywords = _EVIDENCE_HINTS.get(event_type, ()) if event_type else ()
    keywords = keywords + ("交易", "标的", "控股股东", "实际控制人", "问询", "风险提示",
                           "终止", "失败", "重组", "收购", "增持", "回购")

    scored = []
    for index, paragraph in enumerate(paragraphs):
        hits = sum(1 for kw in keywords if kw in paragraph.text)
        scored.append((hits, index, paragraph))
    scored.sort(key=lambda row: (-row[0], row[1]))

    picked_indexes: list[int] = []
    budget = max_chars
    for hits, index, paragraph in scored:
        if len(picked_indexes) >= max_paragraphs or budget <= 0:
            break
        if hits == 0 and picked_indexes:
            break                     # 关键词已经用完，不再补无关段落
        picked_indexes.append(index)
        budget -= len(paragraph.text)

    if not picked_indexes:            # 全部无关键词 → 退化为取最长的几条
        picked_indexes = [row[1] for row in
                          sorted(scored, key=lambda r: -len(r[2].text))[:max_paragraphs]]

    picked_indexes.sort()             # 恢复原文顺序
    return [paragraphs[i] for i in picked_indexes]


#: 与事件类型相关的证据关键词（截断时用于挑段落）—— 与 mock 合成器共用一份
from app.pipeline.mock_source import _EVIDENCE_KEYWORDS as _EVIDENCE_HINTS  # noqa: E402


def _extract_one(session, announcement, paragraphs, options, runner):
    if options.source == SOURCE_MOCK:
        return mock_source.synthesize_extraction(announcement, paragraphs)

    original_count = len(paragraphs)
    paragraphs = _truncate_paragraphs(
        announcement,
        paragraphs,
        settings.llm_max_input_chars,
        settings.llm_max_paragraphs,
    )
    if len(paragraphs) < original_count:
        print(
            f"[llm] 截断：{announcement.title[:26]}… "
            f"{original_count} 段 → {len(paragraphs)} 段"
            f"（预算 {settings.llm_max_input_chars} 字符）"
        )

    company = session.get(Company, int(announcement.company_id))
    stock = session.exec(
        select(Stock).where(Stock.company_id == announcement.company_id)
    ).first()
    payload = ExtractEventInput(
        company=CompanyInput(
            name=company.name if company else "",
            code=stock.code if stock else "",
            is_st=bool(company.is_st) if company else False,
            industry=company.industry if company else None,
        ),
        announcement=AnnouncementInput(
            document_id=announcement.document_id,
            title=announcement.title,
            announcement_type=announcement.announcement_type,
            publication_time=announcement.publication_time,
        ),
        paragraphs=[
            ParagraphInput(page=int(p.page), para_index=int(p.para_index), text=p.text)
            for p in paragraphs
        ],
    )
    result = runner.run(EXTRACT_EVENT, payload)  # type: ignore[union-attr]
    if not result.ok:
        return None
    return result.output


class _NoCache:
    """dry-run 时不写缓存（避免用 dry-run 结果污染正式缓存）。"""

    def get(self, *args, **kwargs):  # noqa: D102
        return None

    def put(self, **kwargs):  # noqa: D102
        return None


# --------------------------------------------------------------------------- #
# Stage 4：机会组装
# --------------------------------------------------------------------------- #
def _build_analysis_runner(session: Session, options: PipelineOptions) -> NodeRunner | None:
    """为 AI 分析阶段（hunt_risk / analyze / score_semantic）构造 runner。

    这三个节点都是 ``layer="analyze"``，因此走 ``ANALYZE_*`` 配置 ——
    用户可以「抽取层用便宜/本地模型、分析层用强模型」（docs/00 D07）。
    ``--no-ai-analysis`` 时返回 ``None``（只出规则分，用于调试与降级）。
    """
    if not options.with_ai_analysis:
        return None
    if options.source == SOURCE_MOCK:
        # mock 源必须**完全离线且确定性** —— 否则单测会去调真实 Ollama，
        # 既慢又不可复现（实测直接把测试跑超时）。
        # 用脚本化 provider 覆盖「AI 结果如何落库」这一段。
        return NodeRunner(
            provider=ScriptedProvider(responder=mock_source.analysis_responder()),
            cache=_NoCache(),
            model="scripted",
        )
    provider_name, model, base_url, api_key = settings.llm_for("analyze")
    provider = build_provider(provider_name, base_url, api_key)
    print(f"[llm] 分析层 {provider_name}/{model} @ {base_url}")
    return NodeRunner(
        provider=provider,
        cache=SqlNodeCache() if not options.dry_run else _NoCache(),
        model=model,
        max_attempts=settings.llm_max_attempts,
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        disable_thinking=settings.llm_disable_thinking,
    )


def _build_opportunities(
    session: Session,
    options: PipelineOptions,
    report: funnel.PipelineReport,
    outcome: PipelineOutcome,
    company_ids: list[int],
    profile_id: int | None,
    *,
    analysis_runner: NodeRunner | None = None,
) -> None:
    if profile_id is None:
        return
    weights = profile_seed.profile_weights(session, profile_id)

    for company_id in company_ids:
        if not session.exec(
            select(Event.id).where(Event.company_id == company_id).limit(1)
        ).first():
            continue

        results = build_opportunities(
            session, company_id, profile_id, weights,
            dry_run=options.dry_run, commit=not options.dry_run,
            analysis_runner=analysis_runner,
        )
        # 注：分析阶段的统计在**循环外**汇总，见本函数末尾 ——
        # 写在循环里会被后一家覆盖，表现为「8 次调用」而实际跑了 18 次。
        for result in results:
            if result.created or result.coverage >= opportunity_builder.MIN_COVERAGE:
                report.funnel.increment("thesis_candidates")
            if result.created:
                report.funnel.increment("cards")
        outcome.opportunities.extend(results)

    if analysis_runner is not None:
        # ★ 在循环外汇总：写在循环里会被后一家覆盖（实测漏报了 10 次调用）
        outcome.analysis_stats = analysis_runner.stats
        outcome.report.llm_ms = sum(r.latency_ms or 0 for r in analysis_runner.log)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
STAGE_CHOICES = ("full", "incremental", "rescore")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.runner",
        description="Investment Opportunity Radar · Pipeline（采集 → 事件 → 机会）",
    )
    parser.add_argument("--stage", choices=STAGE_CHOICES, default="incremental")
    parser.add_argument("--source", choices=[SOURCE_MOCK, SOURCE_CNINFO], default=SOURCE_MOCK,
                        help="mock = 确定性离线（不联网不调 LLM）；cninfo = 真实公告 + 真实 LLM")
    parser.add_argument("--scope", default=settings.ingest_scope,
                        choices=["st_and_risk_warning", "all_a_shares"])
    parser.add_argument("--live", action="store_true",
                        help="关闭 dry-run，真实写库（需先通过 dry-run 预热，docs/07 §6）")
    parser.add_argument("--limit", type=int, default=None, help="候选公司上限")
    parser.add_argument("--llm-limit", type=int, default=3, help="本轮最多对几条公告调用 LLM")
    parser.add_argument("--extract-workers", type=int, default=1,
                        help="抽取并发度（默认 1；需服务端并行度或云端才能提速）")
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--pool", choices=[POOL_MARKET, POOL_COMPANY], default=POOL_MARKET,
                        help="market = event-first via cninfo only (default); "
                             "company = ST list via akshare")
    parser.add_argument("--market-pages", type=int, default=20,
                        help="market-wide query pages (30 announcements each)")
    parser.add_argument("--max-chars", type=int, default=None,
                        help="max characters per announcement sent to the LLM")
    parser.add_argument("--searchkey", default="",
                        help="cninfo full-text search (e.g. 重大资产重组)")
    parser.add_argument("--no-llm", action="store_true", help="只采集不抽取（调试用）")
    parser.add_argument("--no-ai-analysis", action="store_true",
                        help="跳过 AI 分析阶段（hunt_risk/analyze/score_semantic），只出规则分")
    args = parser.parse_args(argv)

    outcome = run_pipeline(PipelineOptions(
        stage=args.stage,
        dry_run=not args.live,
        limit=args.limit,
        scope=args.scope,
        source=args.source,
        with_llm=not args.no_llm,
        with_ai_analysis=not args.no_ai_analysis,
        llm_limit=args.llm_limit,
        lookback_days=args.lookback_days,
        pool=args.pool,
        market_pages=args.market_pages,
        max_input_chars=args.max_chars,
        searchkey=args.searchkey,
        extract_workers=args.extract_workers,
    ))

    payload = outcome.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
