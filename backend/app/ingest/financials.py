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


@dataclass(frozen=True)
class PeriodFinancials:
    """一个报告期的结构化财务数据。``None`` 表示该字段未能取到。"""

    period: str
    period_end: str          # ISO 日期
    metric: str              # 为兼容导入而保留，实际用 values
    values: dict[str, float | None]
    yoy: dict[str, float | None]

    @property
    def is_empty(self) -> bool:
        return not any(v is not None for v in self.values.values())


class FinancialSource:
    """同花顺财务摘要适配器（按报告期）。"""

    name = "akshare_ths"

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

            # 毛利率 / 资产负债率没有自带同比 → 由相邻期自行计算变化率
            for metric in ("gross_margin", "debt_ratio"):
                current, prior = values.get(metric), previous.get(metric)
                if current is not None and prior not in (None, 0):
                    yoy[metric] = (current - prior) / abs(prior)
                else:
                    yoy.setdefault(metric, None)

            # 每股经营现金流的变化率（用于判断现金流是否改善）
            current_ocf, prior_ocf = values.get("ocf_per_share"), previous.get("ocf_per_share")
            if current_ocf is not None and prior_ocf not in (None, 0):
                yoy["ocf_per_share"] = (current_ocf - prior_ocf) / abs(prior_ocf)

            results.append(PeriodFinancials(
                period=period,
                period_end=_period_to_iso(period),
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
    "THS_COLUMNS",
    "THS_YOY_COLUMNS",
    "FinancialSource",
    "PeriodFinancials",
    "parse_cn_number",
    "report_type_of",
]
