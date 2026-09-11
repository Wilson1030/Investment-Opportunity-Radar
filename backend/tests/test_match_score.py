"""规格 §5.8 回归：**同一个事件对不同用户意义不同**。

> 回购事件：高股息用户 55% · 股东回报用户 94% · 重组预期用户 18%

这三个数字必须能复现，否则「个性化」就是空话（规格 §58 原则 6）。
"""

from __future__ import annotations

import pytest

from app.engine.scoring import compute_match_score, compute_rule_score
from app.strategies import STRATEGY_TEMPLATES
from app.strategies.restructuring.rules import STRATEGY

#: 规格 §5.8 的三个用户画像与「回购事件」对各逻辑的条件覆盖度
REPURCHASE_CASE = (
    ("高股息", 0.55, 55.0),
    ("股东回报", 0.94, 94.0),
    ("重组预期", 0.18, 18.0),
)


@pytest.mark.parametrize("user,coverage,expected", REPURCHASE_CASE)
def test_repurchase_event_differs_by_user(user, coverage, expected):
    """权重比都为 1（各自画像里该逻辑都是最高权重），差异全部来自 coverage。"""
    assert compute_match_score(1.0, coverage) == pytest.approx(expected), user


def test_ratio_scales_match_score():
    """画像中权重不是最大项时，匹配度按相对权重线性缩放。"""
    assert compute_match_score(0.5, 0.94) == pytest.approx(47.0)
    assert compute_match_score(0.25, 0.94) == pytest.approx(23.5)


def test_same_company_different_profiles_get_different_scores(facts):
    """同一家公司，权重比不同 → 规则分不同（不是所有用户看到同一个 Dashboard）。"""
    strong = compute_rule_score(facts, "restructuring", profile_weight_ratio=1.0)
    weak = compute_rule_score(facts, "restructuring", profile_weight_ratio=0.3)
    assert strong.match_score > weak.match_score
    assert strong.rule_score > weak.rule_score


def test_templates_are_wellformed():
    """预设模板必须只引用已注册的策略，且权重为正。"""
    from app.models.enums import ThesisType

    for name, weights in STRATEGY_TEMPLATES.items():
        assert weights, name
        for thesis_type, weight in weights.items():
            assert isinstance(thesis_type, ThesisType), name
            assert weight > 0, f"{name} 的 {thesis_type} 权重必须为正"


def test_strategy_coverage_reflects_conditions(facts):
    """coverage 来自核心条件的加权满足度，而不是「标签命中」布尔值。"""
    evaluation = STRATEGY.evaluate(facts)
    assert evaluation.coverage == pytest.approx(0.94)
    # C4（经营困境背景）是部分满足，不是满命中 —— 这是 0.94 而不是 1.0 的原因
    c4 = next(c for c in evaluation.conditions if c.key == "C4")
    assert 0 < c4.satisfaction < 1.0
    assert c4.full is False
