"""采集命令行入口（M10-08 / docs/07 §2.1）。

用法::

    python -m app.ingest --stage full          # 盘前全量
    python -m app.ingest --stage incremental   # 公告增量
    python -m app.ingest --stage rescore       # 盘后重算（不采集）
    python -m app.ingest --dry-run --limit 5   # 只打印不写库（默认 dry_run=true）

**dry-run 语义（docs/03 §7.2）**：采集、解析、LLM 全部真实执行，
唯一差别是不写业务表，改为把结果 dump 到 ``data/cache/dry_run/{date}/``。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.db import engine, init_db
from app.engine import classifier, funnel
from app.engine import scope as scope_engine
from app.ingest import parcel
from app.ingest.base import AdapterError, RawAnnouncement
from app.ingest.cninfo import CninfoAdapter
from app.ingest.normalizer import upsert_announcement, upsert_company
from app.models.audit import IngestRun
from app.models.knowledge import Company, Stock

STAGE_CHOICES = ("full", "incremental", "rescore")


def _dry_run_dir() -> Path:
    path = settings.cache_dir / "dry_run" / date.today().isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_codes(session: Session, scope_name: str, lookback_days: int) -> list[tuple[int, str]]:
    """返回 ``[(company_id, code)]`` —— 优先用库里的候选池，库为空时回退到 akshare ST 名单。"""
    result = scope_engine.select_candidates(
        session, scope_engine.ScopeConfig(scope=scope_name, lookback_days=lookback_days)
    )
    pairs: list[tuple[int, str]] = []
    for company_id in result.company_ids:
        stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
        if stock is not None:
            pairs.append((company_id, stock.code))
    if pairs:
        return pairs

    # 库里还没有公司 → 用 akshare 的 ST 名单初始化（真正的冷启动路径）
    try:
        from app.ingest.akshare_source import AkshareSource

        codes = AkshareSource().st_company_codes()
    except (AdapterError, ImportError) as exc:
        print(f"⚠ 无法获取 ST 名单（{exc}）；候选池为空，本次无可采集对象", file=sys.stderr)
        return []

    for code in codes:
        company = upsert_company(session, code, is_st=True)
        pairs.append((int(company.id or 0), code))
    return pairs


def run_ingest(
    stage: str = "incremental",
    *,
    dry_run: bool | None = None,
    limit: int | None = None,
    scope_name: str | None = None,
) -> funnel.PipelineReport:
    dry_run = settings.ingest_dry_run if dry_run is None else dry_run
    scope_name = scope_name or settings.ingest_scope

    report = funnel.PipelineReport(scope=scope_name, dry_run=dry_run, stage_name=stage)
    started = datetime.now(timezone.utc)

    init_db()
    with Session(engine) as session:
        run = IngestRun(
            adapter="cninfo", scope=scope_name, dry_run=dry_run, stage=stage,
            started_at=started,
        )
        if not dry_run:
            session.add(run)
            session.commit()
            session.refresh(run)
            report.run_id = int(run.id or 0)

        if stage == "rescore":
            # 盘后重算：评分与失效检测由 pipeline 层负责，这里只登记运行
            report.funnel.record("cards", 0)
            print("rescore 阶段：评分重算与失效检测（骨架期未实现，见 docs/07 §P6）")
        else:
            _collect(
                session, report, dry_run=dry_run, limit=limit,
                scope_name=scope_name, lookback_days=settings.ingest_lookback_days,
            )

        report.total_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        if not dry_run:
            run.finished_at = datetime.now(timezone.utc)
            run.items_found = report.funnel.announcements_fetched
            run.items_new = report.funnel.passed_prefilter
            run.items_skipped = report.funnel.announcements_skipped
            run.items_failed = len(report.errors)
            run.funnel = report.funnel.to_dict()
            run.quality = report.quality.to_dict()
            run.errors = report.errors
            run.error_summary = "; ".join(e["error"] for e in report.errors[:3]) or None
            session.add(run)
            session.commit()

    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if dry_run:
        target = _dry_run_dir() / f"{stage}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[dry-run] 结果已写入 {target}（业务表未被修改）")
    return report


def _collect(
    session: Session,
    report: funnel.PipelineReport,
    *,
    dry_run: bool,
    limit: int | None,
    scope_name: str,
    lookback_days: int,
) -> None:
    adapter = CninfoAdapter()
    end = date.today()
    start = end - timedelta(days=lookback_days)

    candidates = _candidate_codes(session, scope_name, lookback_days)
    report.funnel.record("candidates", len(candidates))
    if limit:
        candidates = candidates[:limit]

    parse_failures = 0
    parse_attempts = 0
    dumped: list[dict] = []

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
                session, company_id, raw, parsed, dry_run=dry_run,
                ingest_run_id=report.run_id,
            )
            if outcome.skipped_reason and "幂等" in outcome.skipped_reason:
                report.funnel.increment("announcements_skipped")
            if dry_run:
                dumped.append({
                    "code": code,
                    "document_id": raw.document_id,
                    "title": raw.title,
                    "event_type": classifier.classify_announcement(
                        raw.title, raw.announcement_type
                    ),
                    "matched_keywords": classifier.matched_keywords(raw.title),
                    "parsed": None if parsed is None else {
                        "status": parsed.parse_status,
                        "paragraph_count": len(parsed.paragraphs),
                        "sample_paragraph": parsed.paragraphs[1][2] if len(parsed.paragraphs) > 1 else None,
                    },
                })

    report.quality.parse_failure_rate = (
        parse_failures / parse_attempts if parse_attempts else 0.0
    )
    if dry_run and dumped:
        target = _dry_run_dir() / "candidates.json"
        target.write_text(json.dumps(dumped, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_remote(adapter: CninfoAdapter, raw: RawAnnouncement, report):
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

    # 未知格式：如实标记失败，而不是猜（R2）
    report.add_error("parse_format", f"未知文档格式：{raw.url}", document_id=raw.document_id)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Investment Opportunity Radar · 数据采集",
    )
    parser.add_argument("--stage", choices=STAGE_CHOICES, default="incremental")
    parser.add_argument("--scope", default=None,
                        choices=["st_and_risk_warning", "all_a_shares"])
    parser.add_argument("--live", action="store_true",
                        help="关闭 dry-run，真实写库（需先通过 dry-run 预热，docs/07 §6）")
    parser.add_argument("--limit", type=int, default=None, help="每级最多处理多少家公司")
    args = parser.parse_args(argv)

    run_ingest(
        stage=args.stage,
        dry_run=not args.live,
        limit=args.limit,
        scope_name=args.scope,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
