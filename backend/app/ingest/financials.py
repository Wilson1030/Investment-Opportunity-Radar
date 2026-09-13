"""财务数据适配器 —— 用**可达的**数据源（新浪 / 同花顺），不用东方财富。

## 背景（重要更正）

之前判断「akshare 不可达」是**过宽的结论**。实测：

| 数据源 | 可达性 |
|---|:---:|
| 东方财富（`push2.eastmoney.com` 等） | ❌ 连接失败 |
| **新浪财经**（`money.finance.sina.com.cn`） | ✅ 0.4s |
| **同花顺**（`basic.10jqka.com.cn`） | ✅ 0.5s |
| cninfo 巨潮 | ✅ |
| 交易日历（新浪 `tool_trade_date_hist_sina`） | ✅ 8797 个交易日 |

所以财务与日历都能拿到，只是**不能走东方财富那几个接口**。

## 用哪个源

``stock_financial_abstract_ths``（同花顺，按报告期）一次给出所需的全部字段：

    营业总收入 + 营业总收入同比增长率   → revenue + yoy
    净利润 + 净利润同比增长率           → net_profit + yoy
    销售毛利率 / 资产负债率             → 趋势由相邻期自行计算
    每股经营现金流                      → 现金流的方向与趋势（代理）

## 单位与格式

同花顺返回的是「823.20亿」「-4.53%」这类**带单位的中文字符串**，
必须解析成数值；解析不了的一律返回 ``None``（**不猜**）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingest.base import AdapterError, AdapterSchemaError

#: 中文数量级
_UNIT_MULTIPLIER: dict[str, float] = {
    "万亿": 1e12,
    "亿": 1e8,
    "万": 1e4,
}

_NUMBER_RE = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?$")


def parse_cn_number(value: object) -> float | None:
    """解析「823.20亿」「-4.53%」「1,234.5 万」→ float；解析不了返回 ``None``。

    **解析失败一律返回 None，不猜。** 财务数字猜错的代价比缺失大得多。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in {"-", "--", "nan", "None", "不适用"}:
        return None

    percent = text.endswith("%")
    if percent:
        text = text[:-1]

    multiplier = 1.0
    for unit, factor in _UNIT_MULTIPLIER.items():
        if text.endswith(unit):
            multiplier = factor
            text = text[: -len(unit)]
            break

    if not _NUMBER_RE.match(text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    number *= multiplier
    return number / 100.0 if percent else number


#: 同花顺列名 → 我们的 metric 键
THS_COLUMNS: dict[str, str] = {
    "营业总收入": "revenue",
    "净利润": "net_profit",
    "销售毛利率": "gross_margin",
    "资产负债率": "debt_ratio",
    "每股经营现金流": "ocf_per_share",
    "应收账款周转天数": "receivable_days",
}

#: 同花顺自带同比的列 → metric 键
THS_YOY_COLUMNS: dict[str, str] = {
    "营业总收入同比增长率": "revenue",
    "净利润同比增长率": "net_profit",
}

#: 新浪现金流量表 → metric 键（同花顺摘要里**没有**经营现金流总额，
#: 只有「每股经营现金流」，而「总额」才能回答「经营现金流能否覆盖利息」）
SINA_CASHFLOW_COLUMNS: dict[str, str] = {
    "经营活动产生的现金流量净额": "ocf",
}

#: metric 键 → 单位。``None`` 表示**无量纲比率**（不要硬塞一个「%」，
#: 因为毛利率/资产负债率本身就是 0~1 的比值，加单位反而误导）
METRIC_UNITS: dict[str, str | None] = {
    "revenue": "元",
    "net_profit": "元",
    "ocf": "元",
    "gross_margin": None,
    "debt_ratio": None,
    "ocf_per_share": "元/股",
    "receivable_days": "天",
}

#: 前端表格的列顺序（金额在前、比率在后）
METRIC_ORDER: tuple[str, ...] = (
    "revenue", "net_profit", "ocf", "gross_margin",
    "debt_ratio", "ocf_per_share", "receivable_days",
)


def period_label(period_end: str) -> str:
    """``2026-06-30`` → ``2026H1``；``2025-12-31`` → ``2025A``。

    为什么要有标签：报告期必须能被人一眼读懂。``2026-06-30`` 是「日期」，
    ``2026H1`` 才是「报告期」—— 同一份中报，看日期会误以为是某一天的数据。
    """
    year = period_end[:4]
    return {
        "-03-31": f"{year}Q1",
        "-06-30": f"{year}H1",
        "-09-30": f"{year}Q3",
        "-12-31": f"{year}A",
    }.get(period_end[4:], f"{year}A")


def sina_symbol(code: str) -> str:
    """``600519`` → ``sh600519``（新浪要带市场前缀）。"""
    code = code.strip()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"



@dataclass(frozen=True)
class PeriodFinancials:
    """一个报告期的结构化财务数据。``None`` 表示该字段未能取到。"""

    period: str              # 报告期标签，如 2026H1
    period_end: str          # ISO 日期
    metric: str              # 为兼容导入而保留，实际用 values
    values: dict[str, float | None]
    yoy: dict[str, float | None]

    @property
    def is_empty(self) -> bool:
        return not any(v is not None for v in self.values.values())

    @property
    def label(self) -> str:
        """报告期标签；``period`` 是日期时按 ``period_end`` 现算。"""
        return (
            self.period
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.period or "")
            else period_label(self.period_end)
        )


class CashFlowSource:
    """新浪现金流量表适配器 —— 取**经营活动现金流净额**（总额）。

    ★ 为什么值得多花一次请求：同花顺摘要只有「每股经营现金流」，
    它只能看方向与趋势；而「经营现金流净额」才能回答
    「公司靠经营能不能自己造血」。实测新浪 0.6s 可达。
    """

    name = "akshare_sina_cashflow"

    def fetch_ocf(self, code: str, *, periods: int = 8) -> dict[str, float]:
        """返回 ``{ISO 日期: 经营现金流净额(元)}``；失败返回空字典。

        **非致命**：拿不到就让 ``facts_builder`` 回退到每股代理值
        （它会标 ``ocf_is_proxy=True``），而不是让整批采集失败。
        """
        try:
            import akshare as ak
        except ImportError:  # pragma: no cover
            return {}
        try:
            frame = ak.stock_financial_report_sina(
                stock=sina_symbol(code), symbol="现金流量表"
            )
        except Exception:  # noqa: BLE001 - 非致命，回退代理值
            return {}
        if frame is None or frame.empty or "报告日" not in frame.columns:
            return {}

        column = next(
            (c for c in SINA_CASHFLOW_COLUMNS if c in frame.columns), None
        )
        if column is None:
            return {}

        result: dict[str, float] = {}
        for _, row in frame.head(max(periods, 1) * 2).iterrows():
            raw_date = str(row.get("报告日", "")).strip()
            if not re.match(r"^\d{8}$", raw_date):
                continue
            iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            value = parse_cn_number(row.get(column))
            if value is not None:
                result[iso] = value
        return result


class FinancialSource:
    """同花顺财务摘要适配器（按报告期），可选叠加新浪经营现金流。"""

    name = "akshare_ths"

    def __init__(self, *, with_cashflow: bool = True) -> None:
        self.with_cashflow = with_cashflow

    def fetch(self, code: str, *, periods: int = 8) -> list[PeriodFinancials]:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover
            raise AdapterError(
                '缺少 akshare，请执行：pip install -e ".[ingest]"'
            ) from exc

        try:
            frame = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取财务数据失败（{code}）：{exc}") from exc

        if frame is None or frame.empty:
            raise AdapterSchemaError(f"财务数据为空（{code}）")
        if "报告期" not in frame.columns:
            raise AdapterSchemaError(
                "同花顺财务摘要字段变化（缺少「报告期」列）",
                str(list(frame.columns)),
            )

        # 报告期升序，取最近 N 期
        frame = frame.sort_values("报告期").tail(periods)

        # 经营现金流净额（新浪）；拿不到就退回每股代理值，并在 facts 层标注
        ocf_by_date: dict[str, float] = {}
        if self.with_cashflow:
            ocf_by_date = CashFlowSource().fetch_ocf(code, periods=periods)

        results: list[PeriodFinancials] = []
        previous: dict[str, float | None] = {}
        for _, row in frame.iterrows():
            period = str(row["报告期"]).strip()
            values: dict[str, float | None] = {}
            yoy: dict[str, float | None] = {}

            for column, metric in THS_COLUMNS.items():
                values[metric] = parse_cn_number(row.get(column)) if column in frame.columns else None
            for column, metric in THS_YOY_COLUMNS.items():
                yoy[metric] = parse_cn_number(row.get(column)) if column in frame.columns else None

            # 覆盖成总额（若新浪取到了）
            period_end_iso = _period_to_iso(period)
            if period_end_iso in ocf_by_date:
                values["ocf"] = ocf_by_date[period_end_iso]

            # 毛利率 / 资产负债率没有自带同比 → 由相邻期自行计算变化率
            for metric in ("gross_margin", "debt_ratio"):
                current, prior = values.get(metric), previous.get(metric)
                if current is not None and prior not in (None, 0):
                    yoy[metric] = (current - prior) / abs(prior)
                else:
                    yoy.setdefault(metric, None)

            # 现金流改善：优先用总额，其次用每股代理值
            for key in ("ocf", "ocf_per_share"):
                current_ocf, prior_ocf = values.get(key), previous.get(key)
                if current_ocf is not None and prior_ocf not in (None, 0):
                    yoy[key] = (current_ocf - prior_ocf) / abs(prior_ocf)

            results.append(PeriodFinancials(
                period=period_label(period_end_iso),
                period_end=period_end_iso,
                metric="",
                values=values,
                yoy=yoy,
            ))
            previous = values

        return results


def _period_to_iso(period: str) -> str:
    """``2026-06-30`` / ``2026中报`` / ``2025年报`` → ISO 日期字符串。"""
    text = period.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    year_match = re.match(r"^(\d{4})", text)
    year = int(year_match.group(1)) if year_match else 1970
    if "06-30" in text or "半年" in text or "中报" in text:
        return f"{year}-06-30"
    if "03-31" in text or "一季" in text:
        return f"{year}-03-31"
    if "09-30" in text or "三季" in text:
        return f"{year}-09-30"
    return f"{year}-12-31"


def report_type_of(period_end: str) -> str:
    return {
        "-03-31": "q1", "-06-30": "semi", "-09-30": "q3", "-12-31": "annual",
    }.get(period_end[4:], "annual")


__all__ = [
    "METRIC_ORDER",
    "METRIC_UNITS",
    "SINA_CASHFLOW_COLUMNS",
    "THS_COLUMNS",
    "THS_YOY_COLUMNS",
    "CashFlowSource",
    "FinancialSource",
    "PeriodFinancials",
    "parse_cn_number",
    "period_label",
    "report_type_of",
    "sina_symbol",
]
