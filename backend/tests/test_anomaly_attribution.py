"""INV-F1 异常归因测试（M2-03 / M2-11）—— 补上一条睡着的规则的写入端。

背景：``facts_builder`` 一直会读 ``FinancialMetric.is_anomaly`` / ``anomaly_note``
来汇总 ``deteriorating_attributed_to_one_off``，但**从来没有代码写过这两列**。
于是：

  · ``is_anomaly`` 恒为 False
  · ``deteriorating_attributed_to_one_off`` 恒为 False
  · INV-F1 的「已归因则不扣分」从未生效过

规则写了却不生效比没写更糟 —— 它会让人以为这件事已经被处理了。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.engine.anomaly import (
    ONE_OFF_KEYWORDS,
    THRESHOLDS,
    attribute_latest,
    attribute_period,
    detect_anomalies,
    find_attribution,
)
from app.ingest.normalizer import upsert_financials
from app.models.knowledge import Announcement, Company, FinancialMetric, FinancialPeriod

T0 = datetime(2026, 4, 1, tzinfo=timezone.utc)


class _Period:
    """最小报告期 stub（``find_attribution`` 只用到 ``period_end``）。"""

    def __init__(self, period_end: date) -> None:
        self.period_end = period_end


# --------------------------------------------------------------------------- #
# 异常判定
# --------------------------------------------------------------------------- #
def test_loss_making_period_is_an_anomaly():
    signals = detect_anomalies({"net_profit": (-1.0e8, None)})
    assert [s.metric for s in signals] == ["net_profit"]
    assert "为负" in signals[0].reason


def test_large_drop_is_an_anomaly_but_small_drop_is_not():
    """阈值决定敏感度 —— 小幅波动不该被标成异常（那会让异常失去信息量）。"""
    big = detect_anomalies({
        "net_profit": (1.0e7, THRESHOLDS["net_profit_drop"] - 0.05),
    })
    small = detect_anomalies({"net_profit": (1.0e7, -0.05)})
    assert big and not small


def test_improvement_is_never_an_anomaly():
    """★ 只有下滑方向才需要归因 ——「为什么赚多了」不是风险问题。"""
    assert detect_anomalies({
        "net_profit": (1.0e8, 0.80),
        "revenue": (2.0e9, 0.60),
        "gross_margin": (0.35, 0.25),
    }) == []


def test_missing_data_is_not_an_anomaly():
    """没有数据 ≠ 异常。缺失与恶化是两件事。"""
    assert detect_anomalies({}) == []
    assert detect_anomalies({"net_profit": (None, None)}) == []


def test_negative_ocf_is_an_anomaly():
    signals = detect_anomalies({"ocf": (-5.0e7, None)})
    assert [s.metric for s in signals] == ["ocf"]


# --------------------------------------------------------------------------- #
# 归因检索
# --------------------------------------------------------------------------- #
def _company_with_announcement(session, title: str, when: datetime) -> int:
    company = Company(name="归因测试公司", is_st=False, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)
    session.add(Announcement(
        company_id=company_id, document_id=f"DOC-{abs(hash(title)) % 10**8}",
        title=title, publication_time=when, source_url="http://x",
        parse_status="ok", paragraph_count=0,
    ))
    session.commit()
    return company_id


def test_attribution_is_found_when_a_one_off_announcement_exists(session):
    company_id = _company_with_announcement(
        session, "关于计提商誉减值准备的公告", T0,
    )
    note = find_attribution(
        session, company_id, _Period(date(2026, 3, 31)), "net_profit",
    )
    assert note is not None
    assert "商誉减值" in note
    assert "公告" in note, "归因必须能追溯到具体公告"


def test_attribution_is_none_without_evidence(session):
    """★ 找不到归因就返回 ``None`` —— **不猜**。

    猜「大概是一次性」会让所有恶化都被免除扣分，
    而 INV-F1 的原意恰恰是「只有有依据的才免除」。
    """
    company_id = _company_with_announcement(
        session, "关于日常经营事项的公告", T0,
    )
    assert find_attribution(
        session, company_id, _Period(date(2026, 3, 31)), "net_profit",
    ) is None


def test_attribution_respects_the_time_window(session):
    """★ 时间窗口：用三年前的减值公告解释今年的下滑是不成立的。

    没有窗口约束的话，任何一家历史上计提过减值的公司
    都会永久享有「恶化可归因」的豁免。
    """
    from app.engine.anomaly import ATTRIBUTION_WINDOW_DAYS

    old = T0 - timedelta(days=ATTRIBUTION_WINDOW_DAYS + 60)
    company_id = _company_with_announcement(session, "关于计提商誉减值的公告", old)
    assert find_attribution(
        session, company_id, _Period(date(2026, 3, 31)), "net_profit",
    ) is None, "窗口外的公告不该被用来归因"


def test_every_one_off_keyword_is_matchable(session):
    """词表里的每个词都必须真的能被匹配到（防止写了永不命中的词）。"""
    for keyword in ONE_OFF_KEYWORDS:
        company_id = _company_with_announcement(
            session, f"关于{keyword}事项的公告", T0,
        )
        assert find_attribution(
            session, company_id, _Period(date(2026, 3, 31)), "net_profit",
        ) is not None, f"关键词「{keyword}」未被匹配到"


# --------------------------------------------------------------------------- #
# 落库与事实层
# --------------------------------------------------------------------------- #
def test_attribute_period_writes_both_columns(session):
    """★ 这是原先完全缺失的一环：``is_anomaly`` / ``anomaly_note`` 的写入端。"""
    company_id = _company_with_announcement(
        session, "关于计提商誉减值准备的公告", T0,
    )
    upsert_financials(session, company_id, [type("P", (), {
        "period": "2026Q1", "period_end": "2026-03-31", "metric": "",
        "values": {"net_profit": -1.0e8, "revenue": 5.0e8},
        "yoy": {"net_profit": -0.45, "revenue": -0.35},
        "is_empty": False, "label": "2026Q1",
    })()], commit=True)

    period = session.exec(
        select(FinancialPeriod).where(FinancialPeriod.company_id == company_id)
    ).first()
    assert period is not None
    rows = session.exec(
        select(FinancialMetric).where(FinancialMetric.period_id == int(period.id or 0))
    ).all()
    metrics = {r.metric: (r.value, r.yoy) for r in rows}

    marked = attribute_period(session, company_id, period, metrics)
    session.commit()
    assert marked >= 1, "亏损 + 营收下滑应当至少标出一个异常"

    refreshed = session.exec(
        select(FinancialMetric).where(FinancialMetric.period_id == int(period.id or 0))
    ).all()
    net_profit = next(r for r in refreshed if r.metric == "net_profit")
    assert net_profit.is_anomaly is True
    assert net_profit.anomaly_note
    assert "归因" in net_profit.anomaly_note
    assert "商誉减值" in net_profit.anomaly_note


def test_unattributed_anomaly_says_so_explicitly(session):
    """找不到依据时**必须明说**「未找到公告依据」，而不是留空。

    留空会被读成「没有异常」，而真相是「有异常、但没找到原因」——
    后者照常扣分（INV-F1）。
    """
    company_id = _company_with_announcement(session, "关于日常经营的公告", T0)
    upsert_financials(session, company_id, [type("P", (), {
        "period": "2026Q1", "period_end": "2026-03-31", "metric": "",
        "values": {"net_profit": -1.0e8},
        "yoy": {"net_profit": -0.45},
        "is_empty": False, "label": "2026Q1",
    })()], commit=True)

    attribute_latest(session, company_id)
    session.commit()

    period = session.exec(
        select(FinancialPeriod).where(FinancialPeriod.company_id == company_id)
    ).first()
    row = session.exec(
        select(FinancialMetric).where(
            FinancialMetric.period_id == int(period.id or 0),
            FinancialMetric.metric == "net_profit",
        )
    ).first()
    assert row is not None and row.is_anomaly is True
    assert "未找到" in row.anomaly_note


def test_attribution_makes_inv_f1_take_effect(session):
    """★ INV-F1 的端到端效果：**有依据**的恶化不再被当作未归因扣分。

    这是这条不变量的全部意义所在 —— 在此之前它从未生效过。
    """
    from app.pipeline.facts_builder import build_financial_facts

    company_id = _company_with_announcement(
        session, "关于计提资产减值准备的公告", T0,
    )
    upset = [type("P", (), {
        "period": "2026Q1", "period_end": "2026-03-31", "metric": "",
        "values": {"net_profit": -1.0e8, "revenue": 5.0e8,
                   "gross_margin": 0.20, "debt_ratio": 0.70},
        "yoy": {"net_profit": -0.55, "revenue": -0.10,
                "gross_margin": -0.10, "debt_ratio": 0.05},
        "is_empty": False, "label": "2026Q1",
    })()]
    upsert_financials(session, company_id, upset, commit=True)

    before = build_financial_facts(session, company_id)
    assert before.deteriorating_attributed_to_one_off is False, (
        "归因之前不该被认定为「已归因」"
    )

    attribute_latest(session, company_id)
    session.commit()

    after = build_financial_facts(session, company_id)
    assert after.deteriorating_attributed_to_one_off is True, (
        "有公告依据的恶化应当被认定为「已归因」（INV-F1：不据此扣分）"
    )


def test_writer_and_reader_share_the_same_keyword_list():
    """★ 写入端与读取端必须用**同一份**关键词表。

    各写一份的话，某天写入端加了新词、读取端没加，
    就会出现「标了异常但不认归因」这种静默不一致 ——
    异常标了却仍按未归因扣分，且没有任何报错。
    """
    import inspect

    from app.pipeline import facts_builder

    source = inspect.getsource(facts_builder)
    assert "from app.engine.anomaly import ONE_OFF_KEYWORDS" in source, (
        "facts_builder 应当导入 engine.anomaly 的词表，而不是自己写一份"
    )
    assert "一次性\", \"减值\"" not in source, "facts_builder 里不应再有一份硬编码词表"
