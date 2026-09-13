"""策略注册表自检（D13：设计全量、实现逐个）。"""

from __future__ import annotations

import pytest

from app.models.enums import ScoreDimension, StrategyStatus, ThesisType
from app.strategies import (
    IMPLEMENTATION_ORDER,
    STRATEGIES,
    designed_types,
    get_def,
    get_strategy,
    implementation_status,
    implemented_types,
    validate_registry,
    weights_for,
)
from app.strategies.base import NotImplementedStrategy

ALL_TEN = set(ThesisType)


def test_all_ten_strategies_are_registered():
    """规格 §5.6 的 10 个示例全部正式化为 ThesisTypeDef。"""
    assert set(STRATEGIES) == ALL_TEN
    assert len(IMPLEMENTATION_ORDER) == 10


def test_registry_validate_returns_no_problems():
    assert validate_registry() == []


def test_implemented_types_track_the_declaration_exactly():
    """已实现集合 == 声明为 implemented 的集合，且**没有任何策略被漏掉**。

    ★ 这条原先写的是「恰好只有 restructuring 被实现」（MVP 阶段的约束）。
    实现第 2 个策略 turnaround 后，那个断言的**字面**失效了，
    但它真正要守的东西没变：**声明的实现状态必须与现实一致**。

    为什么这件事值得单独立一条测试：
    实测踩到过 —— turnaround 的实现类已经能跑，registry 里却还写着
    ``DESIGNED``，于是 ``implemented_types()`` 不遍历它，
    机会生成**静默跳过**这个策略。既没有报错，也没有空卡，
    只是它永远不出现在雷达上 —— 最难发现的一类失效。
    """
    from app.strategies import _IMPLEMENTATIONS  # noqa: PLC0415

    declared = {c for c, d in STRATEGIES.items() if d.status is StrategyStatus.IMPLEMENTED}
    actual = set(implemented_types())
    assert actual == declared, f"声明 {declared} ≠ 实际 {actual}"
    assert actual == set(_IMPLEMENTATIONS), "implemented_types() 与实现加载器不一致"

    # 两个方向都不能有洞
    assert ThesisType.RESTRUCTURING in actual
    assert ThesisType.TURNAROUND in actual, "turnaround 已实现，必须在列表里"
    assert len(designed_types()) == 10 - len(actual)
    # 顺序必须与设计文档一致（docs/06 §16）
    assert IMPLEMENTATION_ORDER[:2] == (ThesisType.RESTRUCTURING, ThesisType.TURNAROUND)


def test_implementation_order_starts_with_restructuring():
    assert IMPLEMENTATION_ORDER[0] is ThesisType.RESTRUCTURING


@pytest.mark.parametrize("code", sorted(ALL_TEN, key=lambda c: c.value))
def test_every_strategy_has_complete_design(code):
    definition = get_def(code)
    assert definition.display_name
    assert definition.user_goal, f"{code} 缺少用户目标（规格 §5.6）"
    assert definition.description
    assert len(definition.core_conditions) >= 3, f"{code} 核心条件过少"
    assert definition.support_event_types, f"{code} 缺少支持事件类型"
    assert definition.invalidating_events, f"{code} 缺少失效条件（INV-TT1）"
    assert definition.open_question_templates, f"{code} 缺少待确认模板（规格 §24）"
    assert definition.catalyst_ladder, f"{code} 缺少催化剂阶梯"
    assert definition.risk_factors, f"{code} 缺少风险因素"
    assert definition.evidence_requirements is not None


@pytest.mark.parametrize("code", sorted(ALL_TEN, key=lambda c: c.value))
def test_condition_and_risk_weights_sum_to_one(code):
    definition = get_def(code)
    assert definition.condition_weight_total == pytest.approx(1.0)
    assert definition.risk_weight_total == pytest.approx(1.0)


@pytest.mark.parametrize("code", sorted(ALL_TEN, key=lambda c: c.value))
def test_catalyst_ladder_is_monotonic(code):
    scores = [stage.score for stage in get_def(code).catalyst_ladder]
    assert scores == sorted(scores), f"{code} 的催化剂阶梯分数必须单调不减"
    assert max(scores) <= 100 and min(scores) >= 0


@pytest.mark.parametrize("code", sorted(ALL_TEN, key=lambda c: c.value))
def test_weights_cover_all_positive_dimensions(code):
    weights = weights_for(code)
    assert ScoreDimension.RISK not in weights, "RISK 必须是扣分项，不在正向权重表内"
    assert sum(weights.values()) == pytest.approx(0.95)


# --------------------------------------------------------------------------- #
# 三条跨策略硬约束（docs/06 §14）
# --------------------------------------------------------------------------- #
def test_restructuring_documents_its_anti_patterns():
    """规格 §5.6 示例 A / J 的反例必须写在策略定义里，而不是只存在于文档。"""
    anti = " ".join(get_def(ThesisType.RESTRUCTURING).anti_patterns)
    assert "ST" in anti and "标签" in anti or "INV-C1" in anti


def test_policy_strategy_documents_gating():
    anti = " ".join(get_def(ThesisType.POLICY).anti_patterns)
    assert "政策相关行业" in anti
    assert "0.45" in anti, "policy 的顺序门控上限必须显式记录"


def test_product_strategy_documents_sequential_gating():
    anti = " ".join(get_def(ThesisType.PRODUCT).anti_patterns)
    assert "研发成功" in anti and "商业成功" in anti
    assert "C3" in anti and "计 0" in anti


def test_ma_integration_documents_eight_item_checklist():
    """规格 §5.6 示例 F：八项调查清单不是可选项。"""
    questions = " ".join(get_def(ThesisType.MA_INTEGRATION).open_question_templates)
    for expected in ("收购价格", "商誉风险", "业绩承诺", "整合难度",
                     "标的资产质量", "融资方式", "并购标的盈利能力", "历史并购表现"):
        assert expected in questions, f"并购策略缺少必查项：{expected}"


def test_shareholder_action_requires_contradictory_evidence():
    anti = " ".join(get_def(ThesisType.SHAREHOLDER_ACTION).anti_patterns)
    assert "反证" in anti or "不应只做正向判断" in anti


def test_value_strategy_documents_repurchase_not_equal_dividend():
    anti = " ".join(get_def(ThesisType.VALUE).anti_patterns)
    assert "55" in anti and "94" in anti, "回购 ≠ 股息 的匹配度差异必须显式记录"


def test_every_strategy_has_risk_covering_event_failure():
    """docs/06 §14.3：「发生 X」≠「X 会成功」—— 每个策略都必须有事件失败类风险。"""
    for code in ALL_TEN:
        keys = " ".join(
            f.key + f.label for f in get_def(code).risk_factors
        )
        assert (
            "failure" in keys or "失败" in keys or "不可持续" in keys
            or "误判" in keys or "落地" in keys or "商业化" in keys
            or "价值陷阱" in keys or "增值" in keys
        ), f"{code} 的风险因素未覆盖「事件可能失败」"


# --------------------------------------------------------------------------- #
# 实现状态与占位
# --------------------------------------------------------------------------- #
def test_implementation_status_matches_registry():
    """``implementation_status()`` 必须与 registry 的声明逐项一致。

    这条不写死「谁被实现了」—— 写死的话每实现一个策略都要改测试，
    而测试真正的职责是**交叉校验两个来源**（声明 vs 加载器）。
    """
    statuses = implementation_status()
    for code, definition in STRATEGIES.items():
        assert statuses[code] is definition.status, (
            f"{code} 声明 {definition.status.value}，实际 {statuses[code].value}"
        )
    assert statuses[ThesisType.RESTRUCTURING] is StrategyStatus.IMPLEMENTED
    assert statuses[ThesisType.TURNAROUND] is StrategyStatus.IMPLEMENTED


def test_designed_strategies_return_placeholder_not_fake_result():
    for code in designed_types():
        implementation = get_strategy(code)
        assert isinstance(implementation, NotImplementedStrategy)
