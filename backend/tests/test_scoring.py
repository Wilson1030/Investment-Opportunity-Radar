"""评分引擎算例回归（docs/04 §8，OQ-01 已确认「以 §12 权重为准」）。

**本文件锁死规范算例的数值。** 任何改动导致这些数字变化，都必须先解释清楚
是规则语义变了还是权重变了（并同步更新 docs/04 与 docs/00 的决策记录）。
"""

from __future__ import annotations

import pytest

from app.engine.scoring import (
    RULESET_VERSION,
    SEMANTIC_DIVERGENCE_THRESHOLD,
    compute_divergence,
    compute_match_score,
    compute_rule_score,
    is_divergence_flagged,
    verify_internal_consistency,
)
from app.facts import FinancialFacts, ShareholderFacts, StrategyFacts
from app.models.enums import ScoreDimension
from app.strategies import POSITIVE_WEIGHT_TOTAL, RISK_PENALTY_MAX, weights_for
from app.strategies.registry import _weights

# --------------------------------------------------------------------------- #
# 规范算例
# --------------------------------------------------------------------------- #
EXPECTED_DIMENSIONS = {
    ScoreDimension.THESIS_MATCH: 94.0,
    ScoreDimension.EVENT_CATALYST: 90.0,
    ScoreDimension.CATALYST_STRENGTH: 40.0,
    ScoreDimension.CERTAINTY: 45.0,
    ScoreDimension.FUNDAMENTALS: 50.0,
    ScoreDimension.SHAREHOLDER_STRUCTURE: 90.0,
    ScoreDimension.MARKET_ATTENTION: 60.0,
    ScoreDimension.HISTORY_CASE: 0.0,
}


def test_canonical_example_dimension_values(facts):
    result = compute_rule_score(facts, "restructuring", profile_weight_ratio=1.0)
    for dimension, expected in EXPECTED_DIMENSIONS.items():
        assert result.dimension(dimension).raw_value == pytest.approx(expected), dimension


def test_canonical_example_risk_and_total(facts):
    result = compute_rule_score(facts, "restructuring", profile_weight_ratio=1.0)
    assert result.risk_score == pytest.approx(40.75)
    assert result.risk_penalty == pytest.approx(6.1125)
    assert result.match_score == pytest.approx(94.0)
    assert result.coverage == pytest.approx(0.94)
    # 28.20 + 22.50 + 4.00 + 4.50 + 2.50 + 9.00 + 3.00 + 0.00 - 6.1125 = 67.5875
    assert result.rule_score == pytest.approx(67.5875)
    assert result.rule_score_display == 68


def test_internal_consistency_holds(facts):
    result = compute_rule_score(facts, "restructuring")
    assert verify_internal_consistency(result) == []
    assert sum(d.weighted_value for d in result.dimensions) == pytest.approx(result.rule_score)


def test_dimension_weight_table(facts):
    """策略级覆盖后的 weight 必须与 docs/04 §3.1 一致。"""
    result = compute_rule_score(facts, "restructuring")
    assert result.dimension(ScoreDimension.THESIS_MATCH).weight == pytest.approx(0.30)
    assert result.dimension(ScoreDimension.EVENT_CATALYST).weight == pytest.approx(0.25)
    assert result.dimension(ScoreDimension.FUNDAMENTALS).weight == pytest.approx(0.05)
    # 风险不在正向权重表内，而是以扣分形式出现
    assert result.dimension(ScoreDimension.RISK).weight == pytest.approx(-RISK_PENALTY_MAX / 100)
    assert result.dimension(ScoreDimension.RISK).direction == "negative"
    assert result.dimension(ScoreDimension.RISK).direction_note is not None


def test_score_items_traceable_to_rules_and_evidence(facts):
    """规格 §13：每一项加减分都能追溯到规则与证据。"""
    result = compute_rule_score(facts, "restructuring")
    items = result.score_items
    assert items, "必须产出逐项拆解"
    assert all(hit.rule_id for _, hit in items), "每个 ScoreItem 必须有 rule_id"
    event_items = [h for d, h in items if d is ScoreDimension.EVENT_CATALYST]
    assert any(h.evidence_ids for h in event_items), "事件催化项必须绑定证据"


def test_event_catalyst_decay_applied(facts):
    """时效衰减只作用于 EVENT_CATALYST 与 MARKET_ATTENTION。"""
    from dataclasses import replace

    fresh = compute_rule_score(facts, "restructuring")
    stale = compute_rule_score(replace(facts, newest_evidence_age_days=45.0), "restructuring")
    assert stale.dimension(ScoreDimension.EVENT_CATALYST).raw_value == pytest.approx(90.0 * 0.30)
    assert stale.dimension(ScoreDimension.MARKET_ATTENTION).raw_value == pytest.approx(60.0 * 0.30)
    # 不受衰减影响：事实不因时间久远而降低确定性
    assert stale.dimension(ScoreDimension.CERTAINTY).raw_value == fresh.dimension(
        ScoreDimension.CERTAINTY
    ).raw_value
    assert stale.dimension(ScoreDimension.SHAREHOLDER_STRUCTURE).raw_value == (
        fresh.dimension(ScoreDimension.SHAREHOLDER_STRUCTURE).raw_value
    )


def test_one_off_attribution_does_not_deduct_fundamentals(facts):
    """INV-F1：单指标恶化不得直接判为利空 —— 归因于一次性因素时不额外扣分。"""
    from dataclasses import replace

    base = replace(
        facts,
        financials=FinancialFacts(loss_years=2, ocf_positive=False,
                                 receivable_growth_exceeds_revenue=True,
                                 debt_ratio_rising=True),
    )
    with_penalties = compute_rule_score(base, "restructuring")
    attributed = compute_rule_score(
        replace(base, financials=replace(base.financials,
                                        deteriorating_attributed_to_one_off=True)),
        "restructuring",
    )
    assert with_penalties.dimension(ScoreDimension.FUNDAMENTALS).raw_value < 50.0
    # 归因只影响风险维度（降低严重度），基本面维度仍按实际指标计算
    assert attributed.risk_score < with_penalties.risk_score


def test_e_evidence_only_affects_market_attention():
    """★ docs/04 §4.7：C/D/E 类证据唯一合法的去处是 MARKET_ATTENTION。"""
    from app.models.enums import ReliabilityLevel

    from tests.conftest import bare_facts

    soft = bare_facts(
        evidence_levels=(ReliabilityLevel.E,),
        market=__import__("app.facts", fromlist=["MarketFacts"]).MarketFacts(social_buzz=True),
    )
    result = compute_rule_score(soft, "restructuring")
    # E 类证据无法把确定性推到高分
    assert result.dimension(ScoreDimension.CERTAINTY).raw_value <= 20.0
    # 但可以在市场关注里计分
    assert result.dimension(ScoreDimension.MARKET_ATTENTION).raw_value > 40.0
    # 且逻辑匹配不会因为「有人讨论」而提高
    assert result.match_score == pytest.approx(0.0)


def test_risk_direction_semantics(facts):
    """风险分越高代表风险越大 —— 且确实在扣分。"""
    healthy = compute_rule_score(
        StrategyFacts(
            company=facts.company,
            events=facts.events,
            financials=FinancialFacts(loss_years=0, ocf_positive=True, profitable_years=5),
            shareholder=ShareholderFacts(controlling_shareholder_changed=True),
            evidence_levels=facts.evidence_levels,
            newest_evidence_age_days=0.5,
        ),
        "restructuring",
    )
    risky = compute_rule_score(facts, "restructuring")
    assert healthy.risk_score < risky.risk_score
    assert healthy.rule_score > risky.rule_score


# --------------------------------------------------------------------------- #
# 匹配度与分歧
# --------------------------------------------------------------------------- #
def test_match_score_formula():
    assert compute_match_score(1.0, 0.94) == pytest.approx(94.0)
    assert compute_match_score(1.0, 0.0) == pytest.approx(0.0)
    assert compute_match_score(0.5, 1.0) == pytest.approx(50.0)
    # 越界输入被夹紧，不会产生 >100 或负数
    assert compute_match_score(2.0, 2.0) == pytest.approx(100.0)
    assert compute_match_score(-1.0, 0.5) == pytest.approx(0.0)


def test_divergence_threshold(facts):
    result = compute_rule_score(facts, "restructuring")
    divergence = compute_divergence(result.rule_score, 76.0)
    assert divergence == pytest.approx(8.41, abs=0.01)
    assert not is_divergence_flagged(divergence)

    big = compute_divergence(result.rule_score, 20.0)
    assert big is not None and big > SEMANTIC_DIVERGENCE_THRESHOLD
    assert is_divergence_flagged(big)
    assert is_divergence_flagged(None) is False


# --------------------------------------------------------------------------- #
# 权重表本身的约束
# --------------------------------------------------------------------------- #
def test_positive_weights_sum_to_0_95():
    weights = weights_for("restructuring")
    assert sum(weights.values()) == pytest.approx(POSITIVE_WEIGHT_TOTAL)


def test_weights_override_rejects_wrong_total():
    """策略级覆盖后权重合计必须仍为 0.95 —— 算错就当场报错，不静默漂移。"""
    with pytest.raises(ValueError):
        _weights(thesis_match=0.99)


def test_score_version_present(facts):
    result = compute_rule_score(facts, "restructuring")
    assert RULESET_VERSION in result.score_version
    assert "restructuring" in result.score_version
