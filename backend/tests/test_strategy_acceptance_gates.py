"""docs/06 §16 指定的**验收闸门** —— 每类策略一条，逐条对应。

共享不变量（覆盖率边界、失效归零、主体守卫、标签解耦…）由
``test_strategy_conformance.py`` 参数化覆盖；本文件守的是
**每类策略特有的那条**：

| 策略 | 验收闸门 |
|---|---|
| `restructuring` | 事件文本特征最明显 / 生命周期最清晰 / 待确认·失效最好表达 |
| `turnaround` | 策略与 ST 标签解耦（示例 J） |
| `event_driven` | **不依赖行情数据**也能产出机会 |
| `ma_integration` | 「催化剂 + 交易质量 + 风险」三维评分 |
| `shareholder_action` | 「计划」不等于「已实施」 |
| `policy` | Macro → Industry → Company 下行链路 + 缺业务验证门控 |
| `cycle` | 行业数据缺失时**不给假分数** |
| `growth` | 单季增长不等于成长 |
| `product` | 认证 ≠ 订单（阶段细分不能合并） |
| `value` | 估值分位真判 + 价值陷阱提示 |

（`restructuring` 与 `turnaround` 的闸门已在各自的专项测试文件里，
本文件只补充它们的**跨策略**部分。）
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.facts import CompanyFacts, EventFact, FinancialFacts, MarketFacts, ShareholderFacts
from app.models.enums import EventType, ReliabilityLevel, ThesisType
from app.pipeline.opportunity_builder import MIN_COVERAGE
from app.strategies import get_strategy
from tests.conftest import bare_facts

T0 = None


def _ev(title: str, etype: EventType = EventType.OTHER, level: ReliabilityLevel = ReliabilityLevel.A,
        amount_ratio: float = 0.0) -> EventFact:
    return EventFact(id=1, event_type=etype, title=title, evidence_level=level,
                     amount_ratio=amount_ratio)


def _facts(**kwargs):
    base = bare_facts()
    return replace(base, **kwargs)


def _strategy(code: ThesisType):
    return get_strategy(code)


# --------------------------------------------------------------------------- #
# event_driven：★ 不依赖行情数据也能产出机会
# --------------------------------------------------------------------------- #
def test_event_driven_works_without_any_market_data():
    """★ 验收闸门：不依赖行情数据也能产出机会。

    这条对本项目特别关键：东方财富接口不可达，行情数据本来就拿不到。
    如果这个策略依赖行情，它就永远出不了机会 —— 而不是「偶尔出得少」。

    所以这里把 ``market`` 与 ``valuation_percentile`` 全部置空，
    断言仍然能拿到达标覆盖率。
    """
    strategy = _strategy(ThesisType.EVENT_DRIVEN)
    facts = _facts(
        market=MarketFacts(),                 # 无新闻聚类、无龙虎榜、无研报
        valuation_percentile=None,            # 无估值数据
        events=(
            _ev("关于签署重大合同的公告", EventType.MAJOR_CONTRACT, amount_ratio=0.45),
            _ev("关于重大合同实施进展的公告", EventType.MAJOR_CONTRACT),
            _ev("股东大会审议通过该合同事项", EventType.MAJOR_CONTRACT),
        ),
    )
    evaluation = strategy.evaluate(facts)
    assert evaluation.coverage >= MIN_COVERAGE, (
        f"没有行情数据就出不了机会（coverage={evaluation.coverage}）"
        f" —— 违反「不依赖行情数据」"
    )
    assert strategy.catalyst_strength(facts).score > 0


def test_event_driven_penalises_vague_wording():
    """规格点名要排掉「可能筹划」这类表述。"""
    strategy = _strategy(ThesisType.EVENT_DRIVEN)
    vague = strategy.evaluate(_facts(events=(
        _ev("关于可能筹划重大事项的提示性公告", EventType.M_AND_A),
    )))
    explicit = strategy.evaluate(_facts(events=(
        _ev("关于已签署股权收购协议的公告", EventType.M_AND_A),
    )))
    assert explicit.coverage > vague.coverage, "模糊表述没有被打折"


# --------------------------------------------------------------------------- #
# ma_integration：★ 三维评分（催化剂 + 交易质量 + 风险）
# --------------------------------------------------------------------------- #
def test_ma_integration_requires_trade_quality_not_just_an_announcement():
    """★ 验收闸门：不能只看「有没有并购公告」——交易质量必须独立成条件。

    两份「并购公告」：一份披露了作价与评估，一份只有意向。
    后者的覆盖率必须**显著低于**前者，否则策略退化成「公告数量排序」。
    """
    strategy = _strategy(ThesisType.MA_INTEGRATION)

    quality = strategy.evaluate(_facts(events=(
        _ev("关于收购某某公司 100% 股权暨关联交易的公告", EventType.M_AND_A),
        _ev("标的资产作价与评估结果公告", EventType.M_AND_A),
        _ev("关于收购标的业绩承诺的公告", EventType.M_AND_A),
        _ev("关于收购完成过户的公告", EventType.M_AND_A),
    )))
    vague = strategy.evaluate(_facts(events=(
        _ev("关于拟收购某某公司股权的意向协议公告", EventType.M_AND_A),
    )))

    assert quality.coverage > vague.coverage + 0.20, (
        f"交易质量没有区分度：{quality.coverage:.2f} vs {vague.coverage:.2f}"
    )
    by_key = {c.key: c for c in quality.conditions}
    assert by_key["C3"].satisfaction == 1.0, "披露作价/评估时 C3 应满分"
    assert by_key["C5"].satisfaction == 1.0, "已过户时 C5 应满分"

    vague_by_key = {c.key: c for c in vague.conditions}
    assert vague_by_key["C3"].satisfaction <= 0.15, (
        "仅有意向时交易质量不该被评估为高"
    )


# --------------------------------------------------------------------------- #
# shareholder_action：★ 「计划」不等于「已实施」
# --------------------------------------------------------------------------- #
def test_shareholder_action_distinguishes_plan_from_execution():
    """★ 把「公告了增持计划」当成「已经增持」是本策略最容易犯的错。

    「拟增持不超过 2%」与「已增持 2%」是完全不同的事。
    """
    strategy = _strategy(ThesisType.SHAREHOLDER_ACTION)

    planned = strategy.evaluate(_facts(events=(
        _ev("关于控股股东增持计划的公告", EventType.SHAREHOLDER_BUY),
    )))
    done = strategy.evaluate(_facts(events=(
        _ev("关于控股股东增持计划实施完成的公告", EventType.SHAREHOLDER_BUY),
    )))

    plan_c1 = next(c for c in planned.conditions if c.key == "C1")
    done_c1 = next(c for c in done.conditions if c.key == "C1")
    assert plan_c1.satisfaction < done_c1.satisfaction
    assert plan_c1.satisfaction <= 0.55, "「计划」不该拿到已实施的分数"
    assert done_c1.satisfaction == 1.0
    assert "计划 ≠ 已买入" in plan_c1.detail or "计划" in plan_c1.detail

    assert done.coverage > planned.coverage


# --------------------------------------------------------------------------- #
# policy：★ Macro → Industry → Company 下行链路 + 门控
# --------------------------------------------------------------------------- #
def test_policy_requires_business_verification_to_pass_the_gate():
    """★ 验收闸门：缺业务验证时覆盖率被压到 0.45（docs/06 §14.2）。

    宏观政策对**整个行业**成立，对**某一家公司**不一定成立。
    没有业务验证，一条宏观利好可以被套到几十家公司头上。
    """
    strategy = _strategy(ThesisType.POLICY)
    from app.strategies.policy.rules import BUSINESS_VERIFICATION_CAP

    no_business = strategy.evaluate(_facts(
        company=CompanyFacts(id=1, name="测试公司", industry="光伏设备"),
        events=(
            _ev("国务院关于印发某某产业发展规划的通知", EventType.POLICY_CATALYST),
        ),
    ))
    assert no_business.coverage <= BUSINESS_VERIFICATION_CAP, (
        f"缺业务验证却拿到 {no_business.coverage}"
    )
    assert no_business.coverage_cap == BUSINESS_VERIFICATION_CAP

    with_business = strategy.evaluate(_facts(
        company=CompanyFacts(id=1, name="测试公司", industry="光伏设备"),
        financials=FinancialFacts(periods_with_data=4, revenue_improving_quarters=2),
        events=(
            _ev("国务院关于印发某某产业发展规划的通知", EventType.POLICY_CATALYST),
            _ev("关于中标某某项目订单的公告", EventType.MAJOR_CONTRACT),
        ),
    ))
    assert with_business.coverage > no_business.coverage, "业务验证没有起作用"


def test_policy_chain_penalises_vague_industry_wording():
    """规格否定「相关行业」这种笼统表述 —— 它无法变成可跟踪的标的。"""
    strategy = _strategy(ThesisType.POLICY)
    chain = strategy.evaluate(_facts(events=(
        _ev("关于政策支持上游原材料环节的通知", EventType.POLICY_CATALYST),
    )))
    vague = strategy.evaluate(_facts(events=(
        _ev("关于政策支持相关行业的通知", EventType.POLICY_CATALYST),
    )))
    assert chain.coverage > vague.coverage


# --------------------------------------------------------------------------- #
# cycle：★ 行业数据缺失时不给假分数
# --------------------------------------------------------------------------- #
def test_cycle_never_fakes_industry_conclusions():
    """★ 只有公司公告、没有行业数据时，行业类条件必须明说「未采集」。

    cycle 的覆盖率因此天然偏低 —— **这是对的**：
    一个行业周期判断本来就不该由一家公司的公告单独支撑。
    """
    strategy = _strategy(ThesisType.CYCLE)
    evaluation = strategy.evaluate(_facts(
        financials=FinancialFacts(periods_with_data=8, revenue_declining_run_max=4),
        events=(_ev("关于公司产品提价的公告", EventType.OTHER),),
    ))
    by_key = {c.key: c for c in evaluation.conditions}

    # 行业类条件即便给了分，也必须带「行业数据未采集」的说明
    for key in ("C2", "C3", "C4"):
        detail = by_key[key].detail
        assert "未采集" in detail or "不等于" in detail, (
            f"{key} 在没有行业数据时没有说明边界：{detail}"
        )
    assert by_key["C3"].satisfaction <= 0.70, (
        "公司提价 ≠ 行业价格反弹，不该给满分"
    )
    assert evaluation.coverage < MIN_COVERAGE, (
        "只有公司公告时 cycle 不该越过建卡门槛"
    )


# --------------------------------------------------------------------------- #
# growth：★ 单季增长不等于成长
# --------------------------------------------------------------------------- #
def test_growth_rejects_single_quarter_growth():
    """一份「这一季涨了」不叫成长股 —— 必须连续多季 + 新订单 / 新产能。"""
    strategy = _strategy(ThesisType.GROWTH)

    single = strategy.evaluate(_facts(
        financials=FinancialFacts(periods_with_data=8, revenue_improving_quarters=1),
        events=(_ev("关于业绩预增的公告", EventType.EARNINGS_TURNAROUND),),
    ))
    sustained = strategy.evaluate(_facts(
        financials=FinancialFacts(periods_with_data=8, revenue_improving_quarters=3),
        events=(
            _ev("关于新签订单的公告", EventType.MAJOR_CONTRACT),
            _ev("关于新产能投产的公告", EventType.NEW_PRODUCT),
        ),
    ))
    assert sustained.coverage > single.coverage + 0.20, (
        f"「单季增长」与「持续成长」没有区分度："
        f"{single.coverage:.2f} vs {sustained.coverage:.2f}"
    )
    assert single.coverage < MIN_COVERAGE, "单季增长不该建卡"
    assert sustained.coverage >= MIN_COVERAGE


# --------------------------------------------------------------------------- #
# product：★ 认证 ≠ 订单（阶段细分）
# --------------------------------------------------------------------------- #
def test_product_stages_are_not_merged():
    """「拿到认证」与「拿到订单」之间距离极远，不能混成一个「技术突破」。

    规格把五步做成五个条件，就是要让这两者拉开分差。
    """
    strategy = _strategy(ThesisType.PRODUCT)

    certified = strategy.evaluate(_facts(events=(
        _ev("关于产品取得注册认证的公告", EventType.NEW_PRODUCT),
    )))
    ordered = strategy.evaluate(_facts(events=(
        _ev("关于产品取得注册认证的公告", EventType.NEW_PRODUCT),
        _ev("关于签署批量供货订单的公告", EventType.MAJOR_CONTRACT),
        _ev("关于新产品开始贡献收入的公告", EventType.NEW_PRODUCT),
    )))

    assert ordered.coverage > certified.coverage + 0.25, (
        f"认证与订单没有拉开分差：{certified.coverage:.2f} vs {ordered.coverage:.2f}"
    )
    assert certified.coverage < MIN_COVERAGE, "只有认证不该建卡"
    assert ordered.coverage >= MIN_COVERAGE
    # 分数重心必须压在「能不能变成钱」上
    by_key = {c.key: c for c in ordered.conditions}
    assert by_key["C4"].weight + by_key["C5"].weight == pytest.approx(0.50)
    assert by_key["C1"].weight == pytest.approx(0.10)


# --------------------------------------------------------------------------- #
# value：★ 估值分位真判 + 价值陷阱提示
# --------------------------------------------------------------------------- #
def test_value_uses_real_valuation_percentile():
    """★ C5 用**真实估值分位**判定，而不是「没有数据就当中性」。

    分位来自百度股市通估值序列（见 ``app/ingest/valuation.py``）。
    """
    strategy = _strategy(ThesisType.VALUE)
    base = dict(
        financials=FinancialFacts(
            periods_with_data=8, profitable_years=4, ocf_positive=True,
        ),
        events=(
            _ev("关于提高分红比例的公告", EventType.DIVIDEND_POLICY),
        ),
    )

    cheap = strategy.evaluate(_facts(valuation_percentile=0.12, **base))
    neutral = strategy.evaluate(_facts(valuation_percentile=0.50, **base))
    expensive = strategy.evaluate(_facts(valuation_percentile=0.88, **base))

    cheap_c5 = next(c for c in cheap.conditions if c.key == "C5")
    expensive_c5 = next(c for c in expensive.conditions if c.key == "C5")
    assert cheap_c5.satisfaction == 1.0, "低分位应为满分"
    assert expensive_c5.satisfaction == 0.0, "高分位不该给分"
    assert cheap.coverage > neutral.coverage > expensive.coverage

    assert cheap.coverage >= MIN_COVERAGE, "低估 + 高分红应当能建卡"


def test_value_without_valuation_data_says_so():
    """★ 没有估值数据时必须明说，**不能**把「不知道」当成「估值合理」。"""
    strategy = _strategy(ThesisType.VALUE)
    evaluation = strategy.evaluate(_facts(
        financials=FinancialFacts(periods_with_data=8, profitable_years=4, ocf_positive=True),
        events=(_ev("关于提高分红比例的公告", EventType.DIVIDEND_POLICY),),
        valuation_percentile=None,
    ))
    c5 = next(c for c in evaluation.conditions if c.key == "C5")
    assert c5.satisfaction == 0.0
    assert "未采集" in c5.detail
    assert "不等于" in c5.detail, "必须说明「未采集 ≠ 估值合理」"


def test_value_rewards_low_attention():
    """C6 是唯一的反向项：高股息策略的超额收益常来自「没人看」。"""
    strategy = _strategy(ThesisType.VALUE)
    base = dict(
        financials=FinancialFacts(periods_with_data=8, profitable_years=4, ocf_positive=True),
        events=(_ev("关于提高分红比例的公告", EventType.DIVIDEND_POLICY),),
        valuation_percentile=0.20,
    )
    quiet = strategy.evaluate(_facts(market=MarketFacts(news_cluster_count=0, social_buzz=False), **base))
    noisy = strategy.evaluate(_facts(market=MarketFacts(news_cluster_count=9, social_buzz=True), **base))
    assert quiet.coverage > noisy.coverage, "低关注度没有加分"
    quiet_c6 = next(c for c in quiet.conditions if c.key == "C6")
    assert quiet_c6.satisfaction == 1.0
    # 权重最低，不参与门控
    assert quiet_c6.weight == pytest.approx(0.05)


def test_value_caveat_mentions_the_value_trap():
    """C5 给满分也只说明「便宜」——必须提示可能是价值陷阱。"""
    strategy = _strategy(ThesisType.VALUE)
    facts = _facts(
        financials=FinancialFacts(periods_with_data=8, profitable_years=4, ocf_positive=True),
        valuation_percentile=0.10,
    )
    why = strategy.why_now(facts)
    assert "价值陷阱" in why["conclusion"] or "价值陷阱" in strategy.spec.narrative.caveat
