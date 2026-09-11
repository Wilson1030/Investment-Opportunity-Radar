"""akshare 数据适配器：ST 名单 / 交易日历 / 财务指标 / 个股新闻。

依赖 ``akshare``（可选安装：``pip install -e ".[ingest]"``）。
akshare 的接口签名与字段偶尔变动，因此每个方法都做**字段存在性校验**，
变动时抛 :class:`AdapterSchemaError` 而不是静默返回空数据（R2 / M2-13）。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache

from app.ingest.base import AdapterError, AdapterSchemaError

#: 财务指标字段映射（akshare 中文列名 → 我们的 metric 键）
METRIC_FIELD_MAP: dict[str, str] = {
    "营业总收入": "revenue",
    "营业收入": "revenue",
    "归母净利润": "net_profit",
    "净利润": "net_profit",
    "经营活动产生的现金流量净额": "ocf",
    "应收账款": "receivable",
    "销售毛利率": "gross_margin",
    "毛利率": "gross_margin",
    "资产负债率": "debt_ratio",
}


def _require_akshare():
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover
        raise AdapterError(
            "缺少 akshare，请执行：pip install -e \".[ingest]\""
        ) from exc
    return ak


class AkshareSource:
    name = "akshare"

    # ------------------------------------------------------------------ #
    @lru_cache(maxsize=1)
    def st_company_codes(self) -> tuple[str, ...]:
        """ST / 风险警示股票代码（候选池的第一大来源，D11）。"""
        ak = _require_akshare()
        try:
            frame = ak.stock_zh_a_st_em()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取 ST 名单失败：{exc}") from exc

        if frame is None or "代码" not in getattr(frame, "columns", []):
            raise AdapterSchemaError(
                "akshare ST 名单返回结构变化（缺少「代码」列）",
                str(getattr(frame, "columns", None)),
            )
        return tuple(str(code).zfill(6) for code in frame["代码"].tolist())

    @lru_cache(maxsize=1)
    def all_company_codes(self) -> tuple[str, ...]:
        """全 A 股代码（``INGEST_SCOPE=all_a_shares`` 时使用）。"""
        ak = _require_akshare()
        try:
            frame = ak.stock_zh_a_spot_em()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取全市场列表失败：{exc}") from exc
        if frame is None or "代码" not in getattr(frame, "columns", []):
            raise AdapterSchemaError("akshare 全市场列表返回结构变化", str(getattr(frame, "columns", None)))
        return tuple(str(code).zfill(6) for code in frame["代码"].tolist())

    # ------------------------------------------------------------------ #
    @lru_cache(maxsize=32)
    def trading_days(self) -> tuple[date, ...]:
        """交易日历（R5：取不到时调用方须退化为「工作日 = 交易日」并显式告警）。"""
        ak = _require_akshare()
        try:
            frame = ak.tool_trade_date_hist_sina()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取交易日历失败：{exc}") from exc

        column = None
        for candidate in ("trade_date", "trade_date"):
            if candidate in getattr(frame, "columns", []):
                column = candidate
                break
        if column is None and len(getattr(frame, "columns", [])) >= 1:
            column = frame.columns[0]
        if frame is None or column is None:
            raise AdapterSchemaError("akshare 交易日历返回结构变化", str(getattr(frame, "columns", None)))

        days: list[date] = []
        for raw in frame[column].tolist():
            if isinstance(raw, date):
                days.append(raw)
            else:
                try:
                    days.append(datetime.fromisoformat(str(raw)).date())
                except ValueError:
                    continue
        return tuple(sorted(days))

    # ------------------------------------------------------------------ #
    def financial_metrics(self, code: str) -> dict[str, float]:
        """单个公司的关键财务指标（结构化，不是 PDF 存档 —— M2-03）。"""
        ak = _require_akshare()
        try:
            frame = ak.stock_financial_abstract(symbol=code)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取财务数据失败（{code}）：{exc}") from exc
        if frame is None or frame.empty:
            raise AdapterSchemaError(f"akshare 财务数据为空（{code}）")

        # akshare 的该接口返回「指标名 + 多期数值」的宽表，取最新一列
        columns = list(frame.columns)
        if "指标" not in columns:
            raise AdapterSchemaError(
                "akshare 财务数据返回结构变化（缺少「指标」列）", str(columns)
            )
        value_columns = [c for c in columns if c != "指标"]
        if not value_columns:
            raise AdapterSchemaError("akshare 财务数据无期数列", str(columns))
        latest = value_columns[-1]

        metrics: dict[str, float] = {}
        for _, row in frame.iterrows():
            metric = METRIC_FIELD_MAP.get(str(row["指标"]).strip())
            if metric is None or metric in metrics:
                continue
            try:
                metrics[metric] = float(row[latest])
            except (TypeError, ValueError):
                continue
        return metrics

    # ------------------------------------------------------------------ #
    def company_news(self, code: str, limit: int = 30) -> list[dict]:
        """个股新闻（C 类证据来源）。"""
        ak = _require_akshare()
        try:
            frame = ak.stock_news_em(symbol=code)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取新闻失败（{code}）：{exc}") from exc
        if frame is None:
            return []

        required = ("新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接")
        missing = [c for c in required if c not in getattr(frame, "columns", [])]
        if missing:
            raise AdapterSchemaError(
                f"akshare 新闻返回结构变化（缺少 {missing}）", str(getattr(frame, "columns", None))
            )

        items: list[dict] = []
        for _, row in frame.head(limit).iterrows():
            published = _parse_datetime(row["发布时间"])
            items.append({
                "title": str(row["新闻标题"]),
                "summary": str(row["新闻内容"])[:500],
                "source_name": str(row["文章来源"]),
                "url": str(row["新闻链接"]),
                "publication_time": published,
                "external_id": str(row["新闻链接"]),
            })
        return items


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def clear_caches() -> None:
    AkshareSource.st_company_codes.cache_clear()
    AkshareSource.all_company_codes.cache_clear()
    AkshareSource.trading_days.cache_clear()


__all__ = ["METRIC_FIELD_MAP", "AkshareSource", "clear_caches"]
