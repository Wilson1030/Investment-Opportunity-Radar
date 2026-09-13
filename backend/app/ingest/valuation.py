"""估值数据适配器 —— 百度股市通（规格 §46 的辅助信息层）。

## 为什么需要它

``value``（价值发现 / 高股息）策略的 C5「估值处于历史较低区间」权重 0.20，
但本项目原本**没有任何估值数据源** —— 该条件只能永远返回「未采集」。
一个价值策略拿不到估值，就像一把没有刻度的尺子。

## 数据源

``akshare.stock_zh_valuation_baidu(symbol, indicator, period)`` 实测可达（0.2s），
返回**时间序列**（近一年 365 条 / 近三年 / 近五年 / 近十年），
指标有：总市值 / 市盈率(TTM) / 市盈率(静) / 市净率 / 市现率。

东方财富的估值接口在本环境不可达，所以这是当前唯一可用的选择。

## 分位的方向约定（★ 必须写死）

``percentile`` 越小 = 估值越低（0 = 窗口内最便宜）。
这个约定错了不会报错，只会**静默输出相反结论** ——
「低估」被判成「高估」，而且看起来完全正常。
所以方向由 ``compute_percentile`` 单独实现并单测。

## 诚实边界

  · 亏损公司的 PE 为负 / 无意义 → **PE 分位不计算**，回退到 PB；
    两者都不可用时返回 ``None``（而不是 0 —— 0 表示「最便宜」）
  · 百度不提供股息率 → 股息相关的判断仍由分红公告承担
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.ingest.base import AdapterError

#: 百度股市通支持的估值指标
BAIDU_INDICATORS: dict[str, str] = {
    "总市值": "market_cap",
    "市盈率(TTM)": "pe_ttm",
    "市净率": "pb",
}

#: 默认窗口：近三年（与「历史较低区间」的语义匹配 —— 一年太短、十年太旧）
DEFAULT_PERIOD = "近三年"
DEFAULT_WINDOW_DAYS = 1095


def compute_percentile(series: list[float], current: float) -> float | None:
    """``current`` 在 ``series`` 中的分位 ∈ [0,1]；**越小越便宜**。

    ★ 方向约定：返回 0 表示 current 是窗口内最小值（最便宜）。
    实现用「小于等于 current 的比例」——
    这样「当前值就是窗口最低点」时返回接近 0，而不是接近 1。

    数据不足（少于 30 个点）时返回 ``None``：
    分位是个统计量，样本太少没有意义，而给一个有意义的数字比给一个
    「看起来有意义的数字」更重要。
    """
    values = [v for v in series if v is not None]
    if len(values) < 30 or current is None:
        return None
    below_or_equal = sum(1 for v in values if v <= current)
    return round(below_or_equal / len(values), 4)


@dataclass(frozen=True)
class ValuationSnapshotData:
    """一天的估值快照。

    ``None`` 表示该指标不可得 —— **不要用 0 代替**：
    PE=0 与「PE 无意义（亏损）」是两件完全不同的事。
    """

    as_of: date
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    window_days: int = DEFAULT_WINDOW_DAYS

    @property
    def valuation_percentile(self) -> float | None:
        """给 ``StrategyFacts.valuation_percentile`` 用的综合分位。

        优先 PE(TTM)（盈利口径最常用）；PE 无意义（亏损）时回退 PB。
        两者都不可用 → ``None``（「未采集」，不是「便宜」）。
        """
        if self.pe_percentile is not None:
            return self.pe_percentile
        return self.pb_percentile

    @property
    def is_empty(self) -> bool:
        return all(
            v is None for v in (self.market_cap, self.pe_ttm, self.pb,
                                self.pe_percentile, self.pb_percentile)
        )


class ValuationSource:
    """百度股市通估值适配器。"""

    name = "baidu_gushitong"
    base_url = "https://gushitong.baidu.com/stock/ab-{code}"

    def __init__(self, *, period: str = DEFAULT_PERIOD) -> None:
        self.period = period

    def _series(self, code: str, indicator: str) -> list[float]:
        """取一个指标的时间序列（升序）。失败返回空列表。"""
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover
            raise AdapterError('缺少 akshare，请执行：pip install -e ".[ingest]"') from exc
        try:
            frame = ak.stock_zh_valuation_baidu(
                symbol=code, indicator=indicator, period=self.period
            )
        except Exception:  # noqa: BLE001 - 非致命，交由调用方回退
            return []
        if frame is None or frame.empty or "value" not in frame.columns:
            return []
        out: list[float] = []
        for raw in frame["value"].tolist():
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            out.append(value)
        return out

    def fetch(self, code: str) -> ValuationSnapshotData:
        """取最新一天的估值与分位。全部指标都拿不到时抛 ``AdapterError``。"""
        series = {field: self._series(code, indicator)
                  for indicator, field in BAIDU_INDICATORS.items()}

        latest: dict[str, float | None] = {}
        percentiles: dict[str, float | None] = {}
        for indicator, field in BAIDU_INDICATORS.items():
            values = series[field]
            current = values[-1] if values else None
            latest[field] = current
            if field in ("pe_ttm", "pb"):
                # 亏损公司的 PE 为负 → 分位无意义，返回 None 而不是硬算
                usable = [v for v in values if v > 0]
                percentiles[field] = (
                    compute_percentile(usable, current)
                    if current is not None and current > 0 else None
                )

        snapshot = ValuationSnapshotData(
            as_of=date.today(),
            market_cap=latest.get("market_cap"),
            pe_ttm=latest.get("pe_ttm"),
            pb=latest.get("pb"),
            pe_percentile=percentiles.get("pe_ttm"),
            pb_percentile=percentiles.get("pb"),
            window_days=DEFAULT_WINDOW_DAYS,
        )
        if snapshot.is_empty:
            raise AdapterError(f"未取到估值数据（{code}）")
        return snapshot

    def source_url(self, code: str) -> str:
        return self.base_url.format(code=code)


__all__ = [
    "BAIDU_INDICATORS",
    "DEFAULT_PERIOD",
    "DEFAULT_WINDOW_DAYS",
    "ValuationSnapshotData",
    "ValuationSource",
    "compute_percentile",
]
