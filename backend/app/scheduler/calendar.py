"""交易日历（R5：单一数据源 + 显式退化）。

规格 §40 / docs/03 §6.1：交易日历取不到时，**退化为「工作日 = 交易日」并显式告警**。
退化本身可接受（节假日空跑一次无害），但**静默退化不可接受** —— 用户必须知道
系统当前不是按真实交易日历在跑。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclass
class TradingCalendar:
    days: frozenset[date] = field(default_factory=frozenset)
    degraded: bool = True
    warning: str | None = None

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls) -> TradingCalendar:
        try:
            from app.ingest.akshare_source import AkshareSource

            days = AkshareSource().trading_days()
            if days:
                return TradingCalendar(days=frozenset(days), degraded=False)
            reason = "交易日历返回为空"
        except Exception as exc:  # noqa: BLE001 - 任何取不到的情况都退化，但必须告警
            reason = f"{type(exc).__name__}: {exc}"

        warning = (
            f"⚠ 交易日历不可用（{reason}），已退化为「工作日 = 交易日」。"
            "法定节假日将空跑一次调度。请检查 akshare 交易日接口。"
        )
        logger.warning(warning)
        return TradingCalendar(degraded=True, warning=warning)

    # ------------------------------------------------------------------ #
    def is_trading_day(self, day: date) -> bool:
        if self.degraded:
            return day.weekday() < 5
        return day in self.days

    def next_trading_day(self, day: date, *, include_self: bool = False) -> date:
        cursor = day if include_self else day + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return cursor

    def previous_trading_day(self, day: date, *, include_self: bool = False) -> date:
        cursor = day if include_self else day - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        return cursor


_CALENDAR: TradingCalendar | None = None


def get_calendar(refresh: bool = False) -> TradingCalendar:
    global _CALENDAR
    if _CALENDAR is None or refresh:
        _CALENDAR = TradingCalendar.load()
    return _CALENDAR


__all__ = ["TradingCalendar", "get_calendar"]
