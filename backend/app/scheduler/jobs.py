"""交易日感知的三段式调度（D12）。

    08:30            全量：公告 / 新闻 / 财务 → 事件识别     → 产出「盘前今日机会」
    每 30 分钟       增量：仅拉取 cninfo 最新公告（增量去重）  → 盘中重大公告及时进入
    15:30            重算：评分 / Thesis 监控 / 失效检测       → 产出「持续跟踪」与失效提醒

默认 ``SCHEDULER_ENABLED=false`` + ``INGEST_DRY_RUN=true``：
先手动跑、先 dry-run，连续观察 3 个交易日确认数据质量后再开真写（docs/07 §5、§6）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.config import settings
from app.scheduler.calendar import get_calendar

logger = logging.getLogger(__name__)


def _should_run_now() -> bool:
    calendar = get_calendar()
    today = datetime.now().date()
    if not calendar.is_trading_day(today):
        logger.info("今日非交易日，跳过调度")
        return False
    return True


def job_premarket() -> dict:
    """08:30 盘前全量。"""
    if not _should_run_now():
        return {"skipped": "non_trading_day"}
    from app.ingest.cli import run_ingest

    report = run_ingest(stage="full", dry_run=settings.ingest_dry_run)
    logger.info("盘前全量完成：%s", report.funnel.to_dict())
    return report.to_dict()


def job_intraday() -> dict:
    """盘中增量（每 30 分钟）—— 仅新增公告，幂等去重保证不重复处理。"""
    if not _should_run_now():
        return {"skipped": "non_trading_day"}
    from app.ingest.cli import run_ingest

    report = run_ingest(stage="incremental", dry_run=settings.ingest_dry_run)
    logger.info("盘中增量完成：%s", report.funnel.to_dict())
    return report.to_dict()


def job_postmarket() -> dict:
    """15:30 盘后重算：评分 / Thesis 监控 / 失效检测 / Alert 生成。"""
    if not _should_run_now():
        return {"skipped": "non_trading_day"}
    from app.ingest.cli import run_ingest

    report = run_ingest(stage="rescore", dry_run=settings.ingest_dry_run)
    logger.info("盘后重算完成：hint=%s", report.funnel.drop_at())
    return report.to_dict()


def build_scheduler():
    """构造 APScheduler 实例（未启用时返回 ``None``）。"""
    if not settings.scheduler_enabled:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler(timezone=settings.timezone)

    pre_h, pre_m = _parse_hhmm(settings.schedule_premarket)
    post_h, post_m = _parse_hhmm(settings.schedule_postmarket)

    # 盘前全量
    scheduler.add_job(
        job_premarket, CronTrigger(hour=pre_h, minute=pre_m, day_of_week="mon-fri"),
        id="premarket", replace_existing=True,
    )
    # 盘中增量（仅在交易时段内有效：09:15 – 15:00）
    scheduler.add_job(
        job_intraday,
        IntervalTrigger(minutes=max(5, settings.schedule_intraday_minutes)),
        id="intraday", replace_existing=True,
        jitter=60,
    )
    # 盘后重算
    scheduler.add_job(
        job_postmarket, CronTrigger(hour=post_h, minute=post_m, day_of_week="mon-fri"),
        id="postmarket", replace_existing=True,
    )
    return scheduler


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.split(":")
        return int(hour), int(minute)
    except (ValueError, AttributeError):
        return 8, 30


def describe() -> dict:
    calendar = get_calendar()
    return {
        "enabled": settings.scheduler_enabled,
        "dry_run": settings.ingest_dry_run,
        "timezone": settings.timezone,
        "premarket": settings.schedule_premarket,
        "intraday_minutes": settings.schedule_intraday_minutes,
        "postmarket": settings.schedule_postmarket,
        "calendar_ok": not calendar.degraded,
        "calendar_warning": calendar.warning,
    }


__all__ = [
    "build_scheduler",
    "describe",
    "job_intraday",
    "job_postmarket",
    "job_premarket",
]
