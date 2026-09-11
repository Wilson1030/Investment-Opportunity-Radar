"""规格 §5.6 十个示例的回归（docs/07 §4.2）。

用 mock 示例把「事件 → Thesis → 匹配 → 评分」链路在**不采集真实数据、不调用 LLM**
的前提下回归一遍。

其中 ``example_j`` 是最重要的一条：
> **投资策略应该决定股票为什么被发现，而不是股票标签决定投资策略。**
"""

from __future__ import annotations

import pytest

from app.engine import scope
from app.engine.scoring import compute_match_score, compute_rule_score
from app.ingest import mock
from app.models.enums import EventType, ThesisType
from app.strategies import get_strategy
from app.strategies.base import ConditionResult, NotImplementedStrategy, StrategyEvaluation

EXPECTED_FILES = [f"example_{c}" for c in "abcdefghij"]


def test_all_ten_examples_are_present():
    assert list(mock.available()) == EXPECTED_FILES


def test_index_declares_purpose():
    data = mock.index()
    assert data["source"].endswith("示例 A ~ J")
    assert "不构成任何投资建议" in data["note"]


@pytest.mark.parametrize("name", EXPECTED_FILES)
def test_every_example_loads_into_facts(name):
    payload, facts = mock.load(name)
    assert payload["thesis_type"] in {t.value for t in ThesisType}
    assert facts.company.name
    assert facts.events, f"{name} 应当至少包含一个事件"
    assert all(isinstance(e.event_type, EventType) for e in facts.events)


# --------------------------------------------------------------------------- #
# example_a：规范算例的另一个入口
# --------------------------------------------------------------------------- #
def test_example_a_reproduces_canonical_scores():
    payload, facts = mock.load("example_a")
    ratio = mock.profile_weight_ratio(payload, "restructuring")
    assert ratio == pytest.approx(1.0)

    result = compute_rule_score(facts, "restructuring", ratio)
    assert result.coverage == pytest.approx(0.94)
    assert result.match_score == pytest.approx(94.0)
    assert result.rule_score == pytest.approx(67.5875)
    assert result.rule_score_display == 68
    # 卡片应表达「待确认」
    assert payload["expect"]["status"] == "pending_confirmation"


def test_example_a_match_scales_with_profile_weight():
    payload, facts = mock.load("example_a")
    strong = compute_rule_score(facts, "restructuring", 1.0)
    weak = compute_rule_score(facts, "restructuring", 0.5)
    assert weak.match_score == pytest.approx(47.0)
    assert weak.rule_score < strong.rule_score


# --------------------------------------------------------------------------- #
# example_j ★ 最重要的一条：非 ST 也能被发现
# --------------------------------------------------------------------------- #
def test_example_j_company_is_not_st():
    payload, facts = mock.load("example_j")
    assert payload["company"]["is_st"] is False
    assert facts.company.is_st is False


def test_example_j_enters_candidate_pool_without_st_label():
    """★ INV-C1 在**候选池层**的验证：非 ST 公司凭重组类公告即可入池。"""
    payload, _ = mock.load("example_j")
    reasons = scope.classify_scope_reason(
        is_st=False,
        is_risk_warning=False,
        restructuring_hits=1,
        control_change_hits=0,
    )
    assert reasons, "非 ST 公司必须能凭公告进入候选池"
    assert scope.REASON_KEY_RESTRUCTURING == scope.reason_key(reasons[0])

    # 反过来：只有 ST 标签、没有任何公告 → 入池，但只是「ST 名单」这一条理由
    st_only = scope.classify_scope_reason(
        is_st=True, is_risk_warning=False
    )
    assert st_only == (scope.REASON_ST,)


def test_example_j_declares_full_coverage_without_st():
    payload = mock.load_raw("example_j")
    assert payload["expect"]["coverage"] == pytest.approx(0.80)
    assert payload["expect"]["is_st"] is False
    assert "标签不决定投资策略" in payload["expect"]["note"] or "标签决定" in payload["expect"]["note"]


def test_example_j_carries_all_improvement_signals():
    """困境反转所需的改善信号必须齐备 —— 实现该策略时无需再改 mock。"""
    _, facts = mock.load("example_j")
    assert facts.financials.revenue_improving_quarters >= 2
    assert facts.financials.margin_improving_quarters >= 2
    assert facts.financials.ocf_positive and facts.financials.ocf_improving
    assert facts.financials.loss_years >= 2
    # 剥离亏损业务 + 管理层变化 —— 规格示例 J 的「可归因举措」
    types = set(facts.event_types)
    assert EventType.M_AND_A in types
    assert EventType.MANAGEMENT_CHANGE in types


def test_example_j_turnaround_is_designed_but_not_implemented():
    """诚实标注：turnaround 的设计已完整，但实现排在 P10 第 2 位。"""
    implementation = get_strategy(ThesisType.TURNAROUND)
    assert isinstance(implementation, NotImplementedStrategy)


# --------------------------------------------------------------------------- #
# example_e / example_i：顺序门控在数据层就不可绕过
# --------------------------------------------------------------------------- #
def test_example_e_declares_gating_without_business_verification():
    payload = mock.load_raw("example_e")
    satisfaction = payload["core_condition_satisfaction"]
    assert satisfaction["C5"] == 0.0, "示例 E 恰恰是「只有政策相关、没有业务验证」"
    assert payload["coverage_cap"] == pytest.approx(0.45)

    evaluation = StrategyEvaluation(
        conditions=tuple(
            ConditionResult(key, key, 0.20 if key != "C5" else 0.25, value)
            for key, value in satisfaction.items()
        ),
        coverage_cap=payload["coverage_cap"],
    )
    assert evaluation.coverage <= 0.45
    assert evaluation.coverage < 0.5, "政策相关但无业务验证 → 结构上拿不到高匹配度"


def test_example_i_declares_sequential_gating():
    payload = mock.load_raw("example_i")
    satisfaction = payload["core_condition_satisfaction"]
    assert satisfaction["C3"] == 0.0, "示例 I 恰恰是「只有研发公告、没有客户验证」"
    # 门控语义：C3 = 0 → C4 / C5 一律计 0（即便数据层显示有订单/收入）
    assert satisfaction["C4"] == 0.0
    assert satisfaction["C5"] == 0.0

    evaluation = StrategyEvaluation(conditions=tuple(
        ConditionResult(key, key, 0.20, value) for key, value in satisfaction.items()
    ))
    assert evaluation.coverage <= 0.40, "「宣布研发成功」不得等同于「商业成功」"


# --------------------------------------------------------------------------- #
# example_f / example_g：必须调查清单
# --------------------------------------------------------------------------- #
def test_example_f_declares_eight_item_checklist():
    payload = mock.load_raw("example_f")
    questions = payload["expect"]["open_questions"]
    assert len(questions) == 4  # 示例文件里抽样声明了 4 项
    from app.strategies import get_def

    full = " ".join(get_def(ThesisType.MA_INTEGRATION).open_question_templates)
    for item in questions:
        assert item in full, f"策略定义缺少必查项：{item}"


def test_example_g_requires_contradictory_evidence():
    payload = mock.load_raw("example_g")
    assert "反向结论" in payload["expect"]["risk_trigger"]
    # 质押风险必须存在（示例 G 明确要求检查大股东质押）
    _, facts = mock.load("example_g")
    assert facts.shareholder.high_pledge is True
    from app.strategies import get_def

    risk_keys = " ".join(r.key for r in get_def(ThesisType.SHAREHOLDER_ACTION).risk_factors)
    assert "pledge" in risk_keys
    assert "action_failure" in risk_keys


# --------------------------------------------------------------------------- #
# example_c：事件可以是「组合」而不是重大新闻
# --------------------------------------------------------------------------- #
def test_example_c_shows_non_news_thesis():
    _, facts = mock.load("example_c")
    assert facts.financials.profitable_years >= 3
    assert facts.financials.ocf_positive
    assert facts.shareholder.buyback and facts.shareholder.buyback_scale_significant
    assert EventType.DIVIDEND_POLICY in facts.event_types
    # 估值处于较低区间（value 策略需要）
    assert facts.valuation_percentile is not None and facts.valuation_percentile < 0.3


def test_example_h_declares_industry_first_entry():
    payload = mock.load_raw("example_h")
    assert payload["expect"]["entry"] == "industry_first"
    assert payload["company"]["industry_chain"], "行业级入口需要产业链标签"


def test_example_b_and_d_carry_trend_evidence():
    _, b = mock.load("example_b")
    _, d = mock.load("example_d")
    assert b.financials.margin_improving_quarters >= 2
    assert d.financials.revenue_improving_quarters >= 2
    assert EventType.MAJOR_CONTRACT in d.event_types


def test_mock_profile_ratio_handles_missing_weight():
    payload, _ = mock.load("example_a")
    # 画像里没有配置的策略 → 权重比为 0 → 匹配度为 0（不会误报高匹配）
    assert mock.profile_weight_ratio(payload, "cycle") == 0.0
    assert compute_match_score(0.0, 0.94) == 0.0
