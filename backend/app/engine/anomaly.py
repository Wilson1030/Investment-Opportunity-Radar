"""财务异常识别与归因（INV-F1 / M2-03 / M2-11）。

## 这条不变量在说什么

    INV-F1：单指标恶化**不得**直接判定为利空 —— 异常必须走 ``anomaly_note`` 归因。

原因很实在：净利润下降 40% 可能是因为**商誉减值**（一次性、下年不再发生），
也可能是因为**主业真的不行了**。两者对判断的意义完全相反，
而「净利润同比 -40%」这个数字本身看不出区别。

所以：**只承认有公告依据的恶化**。找不到归因的异常按「未归因」处理
（照常扣分），而不是默认「大概是一次性」。

## 之前的状态：一条睡着的规则

``facts_builder`` 会读 ``FinancialMetric.is_anomaly`` / ``anomaly_note``
并把结果汇总成 ``deteriorating_attributed_to_one_off`` ——
但**从来没有任何代码写过这两列**。于是：

  · ``is_anomaly`` 恒为 False
  · ``deteriorating_attributed_to_one_off`` 恒为 False
  · INV-F1 的「已归因则不扣分」从未生效过

规则写了却不生效，比没写更糟 —— 它会让人以为这件事已经被处理了。
本模块补上写入端。

## 判据（阈值是**策略选择**，但必须写在一处并显式）

阈值本身没有绝对正确的取值，可调。但「调到多少」不该散落在多处 ——
所以全部集中在 ``THRESHOLDS`` 里，并由测试锁定「阈值改变会改变判定」，
避免出现「改了阈值但没人发现行为变了」。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.knowledge import Announcement, FinancialMetric, FinancialPeriod

#: 触发异常判定的阈值。**改这里即可调整敏感度**（勿在多处各写一份）
THRESHOLDS: dict[str, float] = {
    #: 净利润同比下滑幅度（相对）
    "net_profit_drop": -0.30,
    #: 营业收入同比下滑幅度
    "revenue_drop": -0.30,
    #: 毛利率同比变化（相对变化率，不是百分点）
    "margin_drop": -0.20,
}

#: 可归因于「一次性因素」的公告关键词。
#:
#: ★ 这份词表同时是 ``facts_builder`` 判定 ``deteriorating_attributed_to_one_off``
#: 的依据（那边读的是 ``anomaly_note`` 里的词）——
#: 两处必须一致，所以词表定义在这里、由那边导入使用。
ONE_OFF_KEYWORDS: tuple[str, ...] = (
    "商誉减值", "资产减值", "计提减值", "减值准备", "计提",
    "一次性", "非经常性", "资产处置", "处置资产",
    "重组费用", "重组成本", "诉讼", "罚款", "赔偿",
    "投资收益", "公允价值变动", "政府补助", "债务重组",
)

#: 归因检索窗口：报告期前后各多少天内的公告可用来解释该期异常
ATTRIBUTION_WINDOW_DAYS = 120


@dataclass(frozen=True)
class AnomalySignal:
    """一个被判定为「异常」的指标变动。"""

    metric: str
    value: float | None
    yoy: float | None
    reason: str


def detect_anomalies(metrics: dict[str, tuple[float | None, float | None]]) -> list[AnomalySignal]:
    """从「本期指标」判定异常。``metrics`` 是 ``{metric: (value, yoy)}``。

    只在下滑方向判定 —— 变好不需要归因（「为什么赚多了」不是风险问题）。
    """
    signals: list[AnomalySignal] = []

    net_profit, net_profit_yoy = metrics.get("net_profit", (None, None))
    if net_profit is not None and net_profit < 0:
        signals.append(AnomalySignal(
            "net_profit", net_profit, net_profit_yoy, "本期净利润为负",
        ))
    elif net_profit_yoy is not None and net_profit_yoy <= THRESHOLDS["net_profit_drop"]:
        signals.append(AnomalySignal(
            "net_profit", net_profit, net_profit_yoy,
            f"净利润同比 {net_profit_yoy:.1%}",
        ))

    revenue, revenue_yoy = metrics.get("revenue", (None, None))
    if revenue_yoy is not None and revenue_yoy <= THRESHOLDS["revenue_drop"]:
        signals.append(AnomalySignal(
            "revenue", revenue, revenue_yoy, f"营收同比 {revenue_yoy:.1%}",
        ))

    margin, margin_yoy = metrics.get("gross_margin", (None, None))
    if margin_yoy is not None and margin_yoy <= THRESHOLDS["margin_drop"]:
        signals.append(AnomalySignal(
            "gross_margin", margin, margin_yoy, f"毛利率同比变化 {margin_yoy:.1%}",
        ))

    ocf, _ = metrics.get("ocf", (None, None))
    if ocf is not None and ocf < 0:
        signals.append(AnomalySignal("ocf", ocf, None, "本期经营现金流为负"))

    return signals


def find_attribution(
    session: Session,
    company_id: int,
    period: FinancialPeriod,
    metric: str,
    *,
    window_days: int = ATTRIBUTION_WINDOW_DAYS,
) -> str | None:
    """在该期报告前后窗口内的公告里找一次性因素的解释。

    找到 → 返回 ``"「关键词」：公告标题"``（可追溯）；
    找不到 → ``None``（**不猜**）。
    """
    from datetime import timedelta

    start = period.period_end - timedelta(days=window_days)
    end = period.period_end + timedelta(days=window_days)
    rows = session.exec(
        select(Announcement.title, Announcement.publication_time)
        .where(Announcement.company_id == company_id)
    ).all()

    for title, published in rows:
        if not title:
            continue
        when = published.date() if hasattr(published, "date") else published
        if when is None or not (start <= when <= end):
            continue
        for keyword in ONE_OFF_KEYWORDS:
            if keyword in title:
                return f"「{keyword}」：{title[:60]}"
    return None


def attribute_period(
    session: Session,
    company_id: int,
    period: FinancialPeriod,
    metrics: dict[str, tuple[float | None, float | None]],
) -> int:
    """给一个报告期打异常标记与归因，返回**新增的异常数**。

    调用方负责 ``commit``。
    """
    signals = detect_anomalies(metrics)
    if not signals:
        return 0

    anomalies = {s.metric: s for s in signals}
    rows = session.exec(
        select(FinancialMetric).where(FinancialMetric.period_id == int(period.id or 0))
    ).all()

    marked = 0
    for row in rows:
        signal = anomalies.get(row.metric)
        if signal is None:
            continue
        note = find_attribution(session, company_id, period, row.metric)
        row.is_anomaly = True
        row.anomaly_note = (
            f"{signal.reason}；归因：{note}" if note
            else f"{signal.reason}；未找到一次性因素的公告依据（按未归因处理）"
        )
        session.add(row)
        marked += 1
    return marked


def attribute_latest(session: Session, company_id: int) -> int:
    """给该公司**最新一期**做异常归因（采集完财务后调用）。

    只处理最新一期：历史期的异常在采集当时就已标记，
    重复全量重算会让「当时怎么判的」这个信息消失。
    """
    period = session.exec(
        select(FinancialPeriod)
        .where(FinancialPeriod.company_id == company_id)
        .order_by(FinancialPeriod.period_end.desc())  # type: ignore[attr-defined]
    ).first()
    if period is None:
        return 0

    rows = session.exec(
        select(FinancialMetric).where(FinancialMetric.period_id == int(period.id or 0))
    ).all()
    metrics = {row.metric: (row.value, row.yoy) for row in rows}
    return attribute_period(session, company_id, period, metrics)


__all__ = [
    "ATTRIBUTION_WINDOW_DAYS",
    "AnomalySignal",
    "ONE_OFF_KEYWORDS",
    "THRESHOLDS",
    "attribute_latest",
    "attribute_period",
    "detect_anomalies",
    "find_attribution",
]
