"""新闻采集与聚类测试（规格 §42 / §43）。

规格原文：**50 条原始新闻 → 去重 → 聚类 → 识别事件 → 提炼 3~5 条要点**。

本文件的重点在三件事，每件都对应一个容易静默出错的判断：

  1. **近重复的判定阈值** —— 太松会把不同事件并在一起，太紧则去重失败
  2. **「没采到新闻」≠「关注度低」** —— 实测踩到：采了 40 条电报、
     没一条提到候选公司 → 簇数 0 → value 策略的 C6「低关注度」给满分。
     而真相是我们只看了几小时新闻，不足以断言「没人讨论」
  3. **聚类的确定性** —— 聚类结果会进入市场关注度维度并影响评分，
     所以它必须可复现（同一份输入两次运行结果相同）
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import select

from app.ingest.news import (
    NewsSource,
    RawNews,
    cluster_news,
    is_duplicate,
    match_companies,
    normalize_title,
    similarity,
)
from app.models.enums import EventType

T0 = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


def _news(title: str, summary: str = "", minutes: int = 0) -> RawNews:
    from datetime import timedelta

    return RawNews(
        source_name="测试源",
        external_id=f"id-{abs(hash(title)) % 10**8}-{minutes}",
        title=title,
        summary=summary or title,
        url="http://example.com/n",
        published_at=T0 + timedelta(minutes=minutes),
    )


# --------------------------------------------------------------------------- #
# 标题归一化与相似度
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("【财联社】某公司申请破产重整", "某公司申请破产重整"),
    ("财联社9月13日电，某公司被申请重整", "某公司被申请重整"),
    ("证券时报·某公司控制权变更", "某公司控制权变更"),
])
def test_normalize_strips_source_noise(raw, expected):
    """去来源 / 日期前缀 —— 否则同一件事的两条新闻相似度会被前缀拉低。"""
    assert normalize_title(raw) == expected


def test_normalize_fixes_duplicate_miss():
    """★ 归一化的实际作用：带不同来源前缀的同一件事必须被判为近重复。

    这条如果不过，去重就白做了 —— 用户会看到同一件事的多个版本。
    """
    a = "【财联社】某某股份关于法院受理重整申请的公告"
    b = "证券时报·某某股份关于法院受理重整申请的公告"
    assert similarity(a, b) > 0.9
    assert is_duplicate(a, b)


def test_different_events_are_not_duplicates():
    """★ 阈值不能太松：不同的事不能被并成一件。"""
    a = "某某股份关于法院受理重整申请的公告"
    b = "某某股份关于股东减持股份的公告"
    assert not is_duplicate(a, b)


def test_similarity_is_symmetric_and_bounded():
    a = "某公司获得政府补助"
    b = "某公司获得大额政府补助"
    assert similarity(a, b) == pytest.approx(similarity(b, a))
    assert 0.0 <= similarity(a, b) <= 1.0
    assert similarity("", a) == 0.0


# --------------------------------------------------------------------------- #
# 公司匹配
# --------------------------------------------------------------------------- #
COMPANIES = [(1, "ST XXX", "600xxx"), (2, "*ST YYY", "000yyy"), (3, "ZZZ 科技", "300zzz")]


def test_company_match_strips_the_st_prefix():
    """★ 新闻里很少写「ST」前缀 —— 只匹配全名会大量漏掉。"""
    assert match_companies("XXX 公司今日公告拟重整", COMPANIES) == [1]
    assert match_companies("YYY 股份被债权人申请重整", COMPANIES) == [2]


def test_company_match_by_code():
    assert match_companies("代码 300zzz 出现异动", COMPANIES) == [3]


def test_company_match_can_return_multiple():
    """一条新闻可以同时涉及多家公司（例：A 收购 B）。"""
    hits = match_companies("XXX 公司拟收购 YYY 股份全部股权", COMPANIES)
    assert set(hits) == {1, 2}


def test_company_match_finds_nothing_for_unrelated_news():
    assert match_companies("德国汽车零部件制造商申请破产", COMPANIES) == []


# --------------------------------------------------------------------------- #
# 聚类
# --------------------------------------------------------------------------- #
def test_cluster_groups_by_company_and_counts_members():
    items = [
        _news("XXX 公司关于法院受理重整申请的公告", minutes=1),
        _news("XXX 公司重整进展：法院指定管理人", minutes=2),
        _news("机构点评：XXX 公司重整对债权人的影响", minutes=3),
        _news("无关新闻：某地出台楼市新政", minutes=4),
    ]
    drafts = cluster_news(items, COMPANIES)
    assert set(drafts) == {1}, "只应聚出 XXX 公司的簇"
    assert drafts[1].member_count == 3
    assert drafts[1].event_type is EventType.BANKRUPTCY_REORGANIZATION
    assert "3 条报道" in drafts[1].label


def test_cluster_key_points_are_deduped_and_capped():
    """要点来自**真实标题**、去近重复、最多 5 条。

    为什么不用 LLM 概括：这一栏的作用是让用户快速核对「报道都在说什么」，
    概括会引入无法核对的说法。
    """
    items = [
        _news("XXX 公司关于法院受理重整申请的公告", minutes=1),
        _news("【财联社】XXX 公司关于法院受理重整申请的公告", minutes=2),  # 近重复
        _news("XXX 公司重整进展公告", minutes=3),
        _news("XXX 公司管理人公告", minutes=4),
        _news("XXX 公司债权申报公告", minutes=5),
        _news("XXX 公司第一次债权人会议公告", minutes=6),
        _news("XXX 公司重整计划草案公告", minutes=7),
    ]
    drafts = cluster_news(items, COMPANIES)
    points = drafts[1].key_points
    assert len(points) <= 5, "要点最多 5 条"
    assert len(points) >= 5, "样本够时应取满 5 条"
    # 近重复只留一条
    assert not any(is_duplicate(points[0], p) for p in points[1:])


def test_cluster_key_points_come_from_real_titles():
    """要点直接取真实标题（可核对），而不是 LLM 概括。"""
    drafts = cluster_news([_news("XXX 公司关于重大资产重组的公告")], COMPANIES)
    points = drafts[1].key_points
    assert points and "重大资产重组" in points[0]


def test_cluster_picks_the_dominant_event_type():
    """事件类型取出现次数最多的那个（与公告分类器同一套关键词）。"""
    items = [
        _news("XXX 公司关于重大资产重组的公告", minutes=1),
        _news("XXX 公司重大资产重组进展公告", minutes=2),
        _news("XXX 公司股东减持公告", minutes=3),
    ]
    drafts = cluster_news(items, COMPANIES)
    assert drafts[1].event_type is not None
    assert drafts[1].event_type is not EventType.SHAREHOLDER_SELL


def test_cluster_is_deterministic():
    """★ 同一份输入两次运行结果必须相同。

    聚类结果进入市场关注度维度并影响评分 ——
    不可复现的分组等于不可复现的分数。
    """
    items = [
        _news("XXX 公司关于法院受理重整申请的公告", minutes=1),
        _news("XXX 公司重整进展公告", minutes=2),
        _news("YYY 股份控制权变更公告", minutes=3),
    ]
    first = cluster_news(items, COMPANIES)
    second = cluster_news(items, COMPANIES)
    assert {k: (v.label, v.member_count, v.key_points) for k, v in first.items()} == {
        k: (v.label, v.member_count, v.key_points) for k, v in second.items()
    }


def test_cluster_handles_empty_input():
    assert cluster_news([], COMPANIES) == {}


# --------------------------------------------------------------------------- #
# ★ 「没采到新闻」≠「关注度低」
# --------------------------------------------------------------------------- #
def test_small_news_sample_yields_unknown_not_zero(session):
    """★ 实测踩到的假结论：采了几十条电报、没一条提到候选公司 →
    簇数 0 → value 策略 C6「低关注度」给满分。

    真相是**我们只看了几小时新闻**，不足以断言「没人讨论这家公司」。
    样本不足时必须返回 ``None``（不知道），而不是 0（确实没有）。
    """
    from app.ingest.normalizer import upsert_news
    from app.models.knowledge import Company
    from app.pipeline.facts_builder import (
        MIN_NEWS_SAMPLE_FOR_ATTENTION,
        build_event_facts,
        build_market_facts,
    )

    company = Company(name="样本测试公司", is_st=False, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)

    # 采了 40 条，但一条都没提到这家公司
    for i in range(40):
        upsert_news(session, _news(f"宏观新闻第 {i} 条", minutes=i), commit=False)
    session.commit()

    market = build_market_facts(session, company_id, build_event_facts(session, company_id))
    assert market.news_cluster_count is None, (
        f"样本仅 40 条（阈值 {MIN_NEWS_SAMPLE_FOR_ATTENTION}）却断言「0 个新闻簇」"
        " —— 这是拿缺失当结论"
    )


def test_value_c6_refuses_to_claim_low_attention_without_news_data():
    """value 策略的 C6 在没有新闻数据时**不给分**。"""
    from app.facts import MarketFacts
    from app.models.enums import ThesisType
    from app.strategies import get_strategy
    from tests.conftest import bare_facts

    strategy = get_strategy(ThesisType.VALUE)
    evaluation = strategy.evaluate(bare_facts(market=MarketFacts(news_cluster_count=None)))
    c6 = next(c for c in evaluation.conditions if c.key == "C6")
    assert c6.satisfaction == 0.0, "没有新闻数据却给「关注度低」的分"
    assert "未采集" in c6.detail
    assert "不等于关注度低" in c6.detail


def test_market_attention_never_penalises_missing_news(session):
    """★ 市场关注度维度对 ``None`` 与 ``0`` 给**相同**的总分。

    没有新闻数据既不该加分、也不该扣分 —— 只给基线。
    （若给 0 加权，没接新闻的环境会让所有公司的关注度分数凭空偏低。）
    """
    from app.engine.rules import compute_market_attention_dimension
    from app.facts import MarketFacts
    from tests.conftest import bare_facts

    unknown = compute_market_attention_dimension(
        bare_facts(market=MarketFacts(news_cluster_count=None)), decay=1.0
    )
    zero = compute_market_attention_dimension(
        bare_facts(market=MarketFacts(news_cluster_count=0)), decay=1.0
    )
    assert unknown.raw_value == zero.raw_value

    many = compute_market_attention_dimension(
        bare_facts(market=MarketFacts(news_cluster_count=12)), decay=1.0
    )
    assert many.raw_value > unknown.raw_value, "确实有报道时应当加分"


def test_missing_news_does_not_drop_other_attention_signals():
    """★ 新闻缺失不能把「异常波动」等无关信号一起丢掉。

    实测踩到的实现错误：在 ``news_cluster_count is None`` 时提前 ``return``，
    返回类型从 ``(hits, total)`` 变成 ``hits`` ——
    下游 ``result.dimension`` 直接 AttributeError，
    而且异常波动 / 龙虎榜 / 研报的信号全部丢失。
    """
    from app.engine.rules import compute_market_attention_dimension
    from app.facts import MarketFacts
    from tests.conftest import bare_facts

    with_volatility = compute_market_attention_dimension(
        bare_facts(market=MarketFacts(news_cluster_count=None, abnormal_volatility=True)),
        decay=1.0,
    )
    without = compute_market_attention_dimension(
        bare_facts(market=MarketFacts(news_cluster_count=None)), decay=1.0
    )
    assert with_volatility.raw_value > without.raw_value, "异常波动信号被新闻缺失吞掉了"
   # 返回值必须是维度计算结果（有 dimension 属性），而不是裸 list
    assert hasattr(with_volatility, "dimension")


# --------------------------------------------------------------------------- #
# 采集器
# --------------------------------------------------------------------------- #
def test_news_source_normalizes_external_ids():
    """幂等键必须稳定 —— 同一件事重复采集不该产生新行。

    财联社没有稳定 id，所以用「发布时间 + 归一化标题哈希」——
    归一化必须包含在键里，否则带不同来源前缀的同一条新闻会有两个键。
    """
    items = NewsSource().fetch(limit=5) if _network_ok() else []
    if not items:
        pytest.skip("网络不可用")
    ids = [i.external_id for i in items]
    assert len(ids) == len(set(ids)), "同一批次内 id 应当唯一"
    for item in items:
        assert item.published_at is not None
        assert item.source_name
        assert item.url


def _network_ok() -> bool:
    try:
        return bool(NewsSource().fetch_sina(limit=1))
    except Exception:  # noqa: BLE001
        return False
