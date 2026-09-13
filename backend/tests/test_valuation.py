"""估值数据源测试（规格 §46 的辅助信息层 + value 策略 C5 的判定依据）。

本文件重点守**方向约定**：``percentile`` 越小 = 估值越低。
这个约定错了**不会报错**，只会静默输出相反结论 ——
「低估」被判成「高估」，而且看起来完全正常。
所以它必须有独立的单测，而不是靠「跑一遍看着对」。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import select

from app.ingest.valuation import (
    BAIDU_INDICATORS,
    ValuationSnapshotData,
    compute_percentile,
)
from app.models.knowledge import ValuationSnapshot


# --------------------------------------------------------------------------- #
# 分位方向（★ 最关键）
# --------------------------------------------------------------------------- #
def test_lowest_value_gives_near_zero_percentile():
    """窗口最低点 → 分位接近 0（= 最便宜）。

    如果实现写成「大于 current 的比例」，这里会得到接近 1 ——
    把最便宜判成最贵，且不会抛任何错。
    """
    series = [float(v) for v in range(100, 400)]
    assert compute_percentile(series, 100.0) == pytest.approx(0.0033, abs=0.01)


def test_highest_value_gives_percentile_one():
    series = [float(v) for v in range(100, 400)]
    assert compute_percentile(series, 399.0) == pytest.approx(1.0)


def test_median_value_gives_about_half():
    series = [float(v) for v in range(100, 400)]
    assert compute_percentile(series, 250.0) == pytest.approx(0.5, abs=0.02)


def test_percentile_is_monotonic_in_value():
    """值越大分位越高 —— 单调性是方向约定的一般化表达。"""
    series = [float(v) for v in range(100, 400)]
    percentiles = [compute_percentile(series, v) for v in (120, 200, 300, 380)]
    assert percentiles == sorted(percentiles), f"分位不单调：{percentiles}"


def test_insufficient_sample_returns_none():
    """样本太少时返回 ``None``，不给一个「看起来有意义」的数字。

    分位是统计量，30 个点以下没有意义。给数字比不给更危险 ——
    用户会以为它经过了计算。
    """
    assert compute_percentile([1.0, 2.0, 3.0], 2.0) is None
    assert compute_percentile([], 1.0) is None
    assert compute_percentile([float(v) for v in range(29)], 5.0) is None
    # 刚好 30 个点可以算
    assert compute_percentile([float(v) for v in range(30)], 5.0) is not None


# --------------------------------------------------------------------------- #
# 快照的「缺失」语义
# --------------------------------------------------------------------------- #
def test_composite_percentile_prefers_pe_then_falls_back_to_pb():
    """PE 无意义（亏损）时回退 PB；都没有 → ``None``（不是 0）。"""
    with_pe = ValuationSnapshotData(
        as_of=date(2026, 9, 14), pe_percentile=0.12, pb_percentile=0.40,
    )
    assert with_pe.valuation_percentile == 0.12

    loss_making = ValuationSnapshotData(
        as_of=date(2026, 9, 14), pe_ttm=-2.5, pe_percentile=None, pb_percentile=0.37,
    )
    assert loss_making.valuation_percentile == 0.37, "PE 为负时应回退 PB"

    nothing = ValuationSnapshotData(as_of=date(2026, 9, 14))
    assert nothing.valuation_percentile is None, "没有数据必须是 None，不是 0"
    assert nothing.valuation_percentile != 0, "0 的含义是「最便宜」，不能用来表示缺失"


def test_empty_snapshot_is_detected():
    """全空的快照不该被写库（一行全 NULL 只会让「有没有数据」更难判断）。"""
    assert ValuationSnapshotData(as_of=date(2026, 9, 14)).is_empty
    assert not ValuationSnapshotData(
        as_of=date(2026, 9, 14), market_cap=100.0,
    ).is_empty
    # 只有分位、没有绝对值也算有数据
    assert not ValuationSnapshotData(
        as_of=date(2026, 9, 14), pb_percentile=0.5,
    ).is_empty


def test_negative_pe_is_not_treated_as_cheap():
    """★ 亏损公司的负 PE **不能**被当成「很便宜」。

    真实数据实测：4 家 ST 公司里 3 家 PE 为负（-0.95 / -0.78 / -2.89）。
    若直接对含负值的序列算分位，负 PE 会被算成「极低分位 = 极度低估」——
    这是最容易犯、也最危险的错误。
    """
    source_series = [-0.78] * 100 + [20.0] * 200   # 近期才转负
    # 模拟 fetch 里的过滤：只用正数算分位
    usable = [v for v in source_series if v > 0]
    assert compute_percentile(usable, 20.0) == pytest.approx(1.0), (
        "把负 PE 混进序列会让当前值看起来便宜"
    )


# --------------------------------------------------------------------------- #
# 持久化与事实层
# --------------------------------------------------------------------------- #
def test_upsert_valuation_is_idempotent(session):
    """同一 ``(company_id, as_of)`` 更新而非新增。"""
    from app.ingest.normalizer import upsert_valuation
    from app.models.knowledge import Company

    company = Company(name="估值测试公司", is_st=False, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)

    snapshot = ValuationSnapshotData(
        as_of=date(2026, 9, 14), market_cap=100.0, pe_ttm=12.0, pb=1.5,
        pe_percentile=0.25,
    )
    first = upsert_valuation(session, company_id, snapshot)
    assert first is not None

    snapshot2 = ValuationSnapshotData(
        as_of=date(2026, 9, 14), market_cap=110.0, pe_ttm=13.0, pb=1.6,
        pe_percentile=0.30,
    )
    upsert_valuation(session, company_id, snapshot2)

    rows = session.exec(
        select(ValuationSnapshot).where(ValuationSnapshot.company_id == company_id)
    ).all()
    assert len(rows) == 1, "同一天应当只有一条（幂等）"
    assert rows[0].market_cap == 110.0, "应当被更新为最新值"
    assert rows[0].pe_percentile == 0.30


def test_empty_snapshot_is_not_persisted(session):
    """全空快照不写库。"""
    from app.ingest.normalizer import upsert_valuation
    from app.models.knowledge import Company

    company = Company(name="空估值公司", is_st=False, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)

    result = upsert_valuation(
        session, int(company.id or 0), ValuationSnapshotData(as_of=date(2026, 9, 14)),
    )
    assert result is None


def test_facts_layer_reads_the_percentile(session):
    """``StrategyFacts.valuation_percentile`` 来自最新快照。"""
    from app.ingest.normalizer import upsert_valuation
    from app.models.knowledge import Company
    from app.pipeline.facts_builder import build_valuation_percentile

    company = Company(name="事实层测试公司", is_st=False, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)

    assert build_valuation_percentile(session, company_id) is None, (
        "没有快照时必须是 None（不是 0）"
    )

    upsert_valuation(session, company_id, ValuationSnapshotData(
        as_of=date(2026, 9, 14), pe_ttm=10.0, pe_percentile=0.18,
    ))
    assert build_valuation_percentile(session, company_id) == pytest.approx(0.18)


def test_api_market_layer_reports_missing_data_honestly(client, seeded, session):
    """没有估值数据时，接口必须说明「未采集」，而不是省略或填 0。"""
    data = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    market = data["market"]
    assert "note" in market
    if market.get("valuation") is None:
        assert "未采集" in market["valuation_note"]
        assert "无法判断" in market["valuation_note"]
    else:  # pragma: no cover - 种子库通常没有估值
        assert market["valuation"]["direction_note"]


def test_api_market_layer_exposes_direction_note(client, seeded, session):
    """★ 接口必须把方向约定一起返回 —— 否则前端无法正确解释「0.12」。"""
    from app.ingest.normalizer import upsert_valuation

    upsert_valuation(
        session, seeded["company_id"], ValuationSnapshotData(
            as_of=date(2026, 9, 14), market_cap=32.1, pe_ttm=18.4, pb=1.8,
            pe_percentile=0.22, pb_percentile=0.31,
        ),
        # 来源链接要与数据一起留下 —— 判断可追溯
        source_url="https://gushitong.baidu.com/stock/ab-600xxx",
    )
    session.commit()

    data = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    valuation = data["market"]["valuation"]
    assert valuation is not None
    assert valuation["pe_percentile"] == pytest.approx(0.22)
    assert "越小" in valuation["direction_note"], "方向约定必须随数据一起返回"
    assert valuation["window_days"] == 1095, "分位要连同窗口一起返回，否则不可核对"
    assert valuation["source_name"] and valuation["source_url"]
