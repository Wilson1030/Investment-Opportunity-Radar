"""财务数据暴露到 API 的契约测试（P1-1）。

背景：后端已经采了 80 期结构化财务（480 个指标），三个维度
（C4 经营困境 / FUNDAMENTALS / RISK）全靠它，但详情接口**一个字段都没返回** ——
用户看到「基本面扣分」却无从核对。本文件锁定「采了就一定要暴露、且形状正确」。

★ 本文件**自己造财务数据**，不依赖线上采集结果 ——
测试库每次都是重建的空库，任何「依赖线上数据」的断言都会在测试里失败
（这是我自己踩过的坑：验证必须真的走到被测路径上，而不是靠外部状态）。

测试锁定的是**契约**（docs/05-API契约.md §3.3），不是实现细节：
金额用亿元（不是元）、比率不给单位、报告期用标签（不是日期）、
信号 key 必须能追溯到 engine/rules.py 的规则键。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import Session, select

from app.api import serializers
from app.facts import FinancialFacts
from app.ingest.financials import METRIC_ORDER, METRIC_UNITS, period_label
from app.models.knowledge import FinancialMetric, FinancialPeriod, Stock

#: engine/rules.py 里与财务相关的规则键 —— 信号 key 必须是它们的子集
FINANCIAL_RULE_KEYS = {
    "loss_years_gte_2",
    "not_profitable",
    "ocf_not_positive",
    "ocf_improving",
    "margin_declining",
    "revenue_improving",
    "receivable_problem",
    "debt_ratio_rising",
    "one_off_attributed",
}


# --------------------------------------------------------------------------- #
# 造数据
# --------------------------------------------------------------------------- #
def _seed_financials(session: Session, company_id: int) -> None:
    """写 3 个报告期 × 7 个指标（含 4 个季报/年报口径）。

    刻意让数据「有新有旧、有好有坏」，这样才能验证排序与 tone 的区分度。
    """
    periods = [
        # (period_end, report_type, values)
        (date(2026, 6, 30), "semi", {
            "revenue": 2.0e9, "net_profit": -1.2e8, "ocf": 8.0e7,
            "gross_margin": 0.18, "debt_ratio": 0.68,
            "ocf_per_share": 0.06, "receivable_days": 33.18,
        }),
        (date(2026, 3, 31), "q1", {
            "revenue": 9.0e8, "net_profit": -3.0e7, "ocf": 1.0e7,
            "gross_margin": 0.19, "debt_ratio": 0.65,
            "ocf_per_share": 0.01, "receivable_days": 30.1,
        }),
        (date(2025, 12, 31), "annual", {
            "revenue": 1.63e9, "net_profit": -2.4e8, "ocf": -5.0e7,
            "gross_margin": 0.20, "debt_ratio": 0.63,
            "ocf_per_share": -0.04, "receivable_days": 29.6,
        }),
    ]
    yoy = {"revenue": 0.23, "net_profit": None, "ocf": 0.41,
           "gross_margin": -0.042, "debt_ratio": 0.08,
           "ocf_per_share": 0.41, "receivable_days": 0.12}
    for period_end, report_type, values in periods:
        row = FinancialPeriod(
            company_id=company_id,
            period=period_label(period_end.isoformat()),
            period_end=period_end,
            report_type=report_type,
        )
        session.add(row)
        session.flush()
        for metric, value in values.items():
            session.add(FinancialMetric(
                period_id=int(row.id or 0), metric=metric, value=value,
                unit=METRIC_UNITS.get(metric), yoy=yoy.get(metric),
            ))
    session.commit()


@pytest.fixture()
def company_with_financials(session, seeded):
    """在 conftest 的 ``seeded`` 之上补财务数据（机会卡已存在）。"""
    _seed_financials(session, seeded["company_id"])
    return seeded


# --------------------------------------------------------------------------- #
# 纯函数层（不依赖数据库）
# --------------------------------------------------------------------------- #
def test_period_label_is_a_report_period_not_a_date() -> None:
    """报告期必须能被人一眼读懂。

    曾经库里存的是 ``2026-06-30`` —— 看日期会误以为是「某一天的数据」，
    而它其实是中报（半年累计）。
    """
    assert period_label("2026-06-30") == "2026H1"
    assert period_label("2026-03-31") == "2026Q1"
    assert period_label("2025-09-30") == "2025Q3"
    assert period_label("2025-12-31") == "2025A"


def test_every_metric_declares_a_unit_policy() -> None:
    """每个指标都要有明确的单位口径，包括「无量纲」这一种。

    ``METRIC_UNITS[x] is None`` 表示比率（无量纲），**不是漏配** ——
    所以这里断言的是「键存在」，而不是「值非空」。
    """
    for metric in METRIC_ORDER:
        assert metric in METRIC_UNITS, f"{metric} 没有声明单位口径"
    assert METRIC_UNITS["gross_margin"] is None
    assert METRIC_UNITS["debt_ratio"] is None
    assert METRIC_UNITS["revenue"] == "元"
    assert METRIC_UNITS["ocf"] == "元"


# --------------------------------------------------------------------------- #
# 序列化层
# --------------------------------------------------------------------------- #
def test_series_is_ordered_newest_first(session, company_with_financials) -> None:
    """最新期在前 —— 用户先看当期，而不是三年前。"""
    series = serializers.financial_series(session, company_with_financials["company_id"])
    assert [p["period"] for p in series] == ["2026H1", "2026Q1", "2025A"]


def test_money_becomes_yi_and_ratios_stay_unitless(session, company_with_financials) -> None:
    """金额换算成亿元；比率**不带**单位（否则 0.18 会被读成 0.18%）。"""
    series = serializers.financial_series(session, company_with_financials["company_id"])
    latest = series[0]["metrics"]

    assert latest["revenue"] == {"value": 20.0, "unit": "亿元", "yoy": 0.23}
    assert latest["net_profit"]["value"] == -1.2
    assert latest["net_profit"]["unit"] == "亿元"
    assert latest["ocf"]["value"] == 0.8

    for ratio in ("gross_margin", "debt_ratio"):
        assert "unit" not in latest[ratio], f"{ratio} 是无量纲比率，不应带 unit"
    assert latest["gross_margin"]["value"] == 0.18
    # 非金额、非比率的指标保留自己的单位
    assert latest["ocf_per_share"]["unit"] == "元/股"
    assert latest["receivable_days"]["unit"] == "天"


def test_metric_order_is_stable(session, company_with_financials) -> None:
    """指标顺序固定 —— 前端表格的列/行才不会每次刷新都跳。"""
    series = serializers.financial_series(session, company_with_financials["company_id"])
    order = list(series[0]["metrics"])
    assert order == [m for m in METRIC_ORDER if m in order]


def test_period_label_is_not_a_date(session, company_with_financials) -> None:
    """对外的 ``period`` 是标签，且与 ``period_end`` 自洽。"""
    series = serializers.financial_series(session, company_with_financials["company_id"])
    for period in series:
        assert "-" not in period["period"], f"period 还是日期：{period['period']}"
        assert period["period"] == period_label(period["period_end"])


def test_series_is_empty_for_unknown_company(session) -> None:
    """没有财务数据 → 返回空列表，**不用 0 填充**。

    缺失与「真的是 0」是两件事：营收 0 和「没采到营收」完全不同。
    """
    assert serializers.financial_series(session, 999_999) == []


# --------------------------------------------------------------------------- #
# 信号层
# --------------------------------------------------------------------------- #
def test_signals_are_traceable_to_rules() -> None:
    """每条信号都要能追溯到一个真实存在的规则键。

    「可解释」的前提是**可追溯** —— 给一个无法与规则对应的数字不算可解释。
    """
    signals = serializers.financial_signals(FinancialFacts())
    assert len(signals) == len(FINANCIAL_RULE_KEYS)
    for signal in signals:
        assert signal["key"] in FINANCIAL_RULE_KEYS, f"未知的规则键：{signal['key']}"
        assert signal["tone"] in {"bad", "good", "muted"}
        assert signal["label"] and signal["value_text"] and signal["impact"]


def test_impact_is_neutral_and_tone_carries_direction() -> None:
    """方向只能由 ``tone`` 表达 —— ``impact`` 必须是中性陈述。

    曾经把 ``impact`` 写成「推高 RISK」这种带方向的句子，
    结果条件不成立（tone=绿）时页面显示「✓ 应收增速未超过 → 推高 RISK」，自相矛盾。
    """
    for facts in (FinancialFacts(), FinancialFacts(
        loss_years=3, profitable_years=0, ocf_positive=False,
        ocf_improving=False, margin_improving_quarters=0,
        revenue_improving_quarters=0, receivable_growth_exceeds_revenue=True,
        debt_ratio_rising=True,
    )):
        for signal in serializers.financial_signals(facts):
            assert "推高" not in signal["impact"], (
                f"impact 带了方向词：{signal['impact']}（tone={signal['tone']}）"
            )


def test_tone_reflects_condition_both_ways() -> None:
    """tone 必须**双向**判定，而不是只看条件是否成立。

    「经营现金流不为正」不成立 = 现金流为正 = 好消息（绿），
    而「现金流同比改善」不成立只是**没有好消息**（灰），不是坏消息。
    """
    healthy = serializers.financial_signals(FinancialFacts(
        loss_years=0, profitable_years=3, ocf_positive=True, ocf_improving=True,
        margin_improving_quarters=2, revenue_improving_quarters=2,
        receivable_growth_exceeds_revenue=False, debt_ratio_rising=False,
        deteriorating_attributed_to_one_off=True,
    ))
    by_key = {s["key"]: s for s in healthy}
    assert by_key["ocf_not_positive"]["tone"] == "good"      # 不利条件不成立 → 绿
    assert by_key["debt_ratio_rising"]["tone"] == "good"
    assert by_key["ocf_improving"]["tone"] == "good"          # 有利条件成立 → 绿
    # 有利条件**不成立**只是没有好消息，不该判成坏
    assert by_key["one_off_attributed"]["tone"] == "good"

    distressed = serializers.financial_signals(FinancialFacts(
        loss_years=3, profitable_years=0, ocf_positive=False, ocf_improving=False,
        margin_improving_quarters=0, revenue_improving_quarters=1,
        receivable_growth_exceeds_revenue=True, debt_ratio_rising=True,
    ))
    by_key = {s["key"]: s for s in distressed}
    assert by_key["loss_years_gte_2"]["tone"] == "bad"
    assert by_key["ocf_not_positive"]["tone"] == "bad"
    assert by_key["receivable_problem"]["tone"] == "bad"
    # 有利条件不成立 → 灰（不是坏）
    assert by_key["ocf_improving"]["tone"] == "muted"
    assert by_key["one_off_attributed"]["tone"] == "muted"


def test_ocf_proxy_is_disclosed() -> None:
    """用每股值代理时必须**明说** —— 否则会被当成经营现金流总额。"""
    proxied = {s["key"]: s for s in serializers.financial_signals(
        FinancialFacts(ocf_is_proxy=True)
    )}
    assert "代理值" in proxied["ocf_not_positive"]["value_text"]
    assert "代理值" in proxied["ocf_improving"]["value_text"]

    real = {s["key"]: s for s in serializers.financial_signals(
        FinancialFacts(ocf_is_proxy=False)
    )}
    assert "代理值" not in real["ocf_not_positive"]["value_text"]


# --------------------------------------------------------------------------- #
# 接口契约层
# --------------------------------------------------------------------------- #
def test_detail_exposes_financials(client, company_with_financials) -> None:
    """详情接口必须返回财务数据（本文件存在的全部理由）。"""
    data = client.get(
        f"/api/opportunities/{company_with_financials['opportunity_id']}"
    ).json()["data"]
    assert data["financials"], "详情接口没有返回 financials"
    assert data["financial_signals"], "详情接口没有返回 financial_signals"


def test_contract_shape_of_one_period(client, company_with_financials) -> None:
    """契约形状：``{period, period_end, report_type, metrics: {...}}``。"""
    data = client.get(
        f"/api/opportunities/{company_with_financials['opportunity_id']}"
    ).json()["data"]
    latest = data["financials"][0]
    assert set(latest) >= {"period", "period_end", "report_type", "metrics"}
    assert latest["report_type"] == "semi"
    for name, entry in latest["metrics"].items():
        assert set(entry) >= {"value", "yoy"}
        expected = METRIC_UNITS.get(name)
        if entry["value"] is None or expected is None:
            assert "unit" not in entry or expected is not None
        elif expected == "元":
            assert entry["unit"] == "亿元"


def test_financials_belong_to_this_opportunitys_company(
    session, client, company_with_financials
) -> None:
    """★ 财务数据必须属于**这张卡的那家公司** —— 不能串到别家。

    这条守卫的由来：证据链曾经整体错位到别的公司（把 ``Event.id`` 当成了
    ``Evidence.id``），错一格就全错。跨表引用必须有归属校验。
    """
    company_id = company_with_financials["company_id"]
    data = client.get(
        f"/api/opportunities/{company_with_financials['opportunity_id']}"
    ).json()["data"]
    assert data["card"]["company"]["id"] == company_id

    owned = session.exec(
        select(FinancialPeriod).where(FinancialPeriod.company_id == company_id)
    ).all()
    assert {p.period_end.isoformat() for p in owned} == {
        p["period_end"] for p in data["financials"]
    }
    # 该公司的股票代码也要对得上（采集是按 code 走的）
    stock = session.exec(select(Stock).where(Stock.company_id == company_id)).first()
    assert stock is not None


def test_money_magnitude_is_sane(client, company_with_financials) -> None:
    """用「数量级合理性」兜住重复换算（元→亿 只做一次）。"""
    data = client.get(
        f"/api/opportunities/{company_with_financials['opportunity_id']}"
    ).json()["data"]
    for period in data["financials"]:
        revenue = period["metrics"].get("revenue", {}).get("value")
        if revenue is None:
            continue
        # 造数据是 20 亿元级别；若被再除一次 1e8 会变成 2e-7
        assert 0.01 <= abs(revenue) <= 1e5, f"{period['period']} 营收 {revenue} 亿元不合理"
