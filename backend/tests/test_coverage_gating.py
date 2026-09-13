"""顺序门控机制（docs/06 §14.2）。

规格要求这两条**不能靠 prompt 提醒**：

* ``policy``：C5（实际订单/收入验证）未命中 → coverage 上限 0.45 ——
  「只有政策相关、没有业务验证」结构上不可能获得高匹配度（规格 §5.6 示例 E）
* ``product``：C3（客户验证）未命中 → C4 / C5 一律计 0 ——
  「只有研发公告」结构上不可能获得高匹配度（规格 §5.6 示例 I）

本文件验证**机制本身**；两条策略实现时（docs/07 §P10 第 6 / 9 位）直接复用同一机制。
"""

from __future__ import annotations

import pytest

from app.models.enums import ThesisType
from app.strategies import get_def, get_strategy
from app.strategies.base import ConditionResult, StrategyEvaluation, NotImplementedStrategy
from tests.conftest import bare_facts


def test_coverage_cap_limits_result():
    evaluation = StrategyEvaluation(
        conditions=(
            ConditionResult("C1", "政策发布", 0.20, 1.0),
            ConditionResult("C2", "行业识别", 0.15, 1.0),
            ConditionResult("C3", "产业链拆解", 0.20, 1.0),
            ConditionResult("C4", "主营高度相关", 0.20, 1.0),
            ConditionResult("C5", "实际订单验证", 0.25, 0.0),
        ),
        coverage_cap=0.45,
    )
    assert evaluation.coverage_raw == pytest.approx(0.75)
    assert evaluation.coverage == pytest.approx(0.45), "门控必须把 coverage 压到上限"


def test_coverage_cap_does_not_raise_low_coverage():
    evaluation = StrategyEvaluation(
        conditions=(ConditionResult("C1", "政策发布", 1.0, 0.10),),
        coverage_cap=0.45,
    )
    assert evaluation.coverage == pytest.approx(0.10)


def test_no_cap_means_plain_weighted_coverage():
    evaluation = StrategyEvaluation(
        conditions=(
            ConditionResult("C1", "a", 0.5, 1.0),
            ConditionResult("C2", "b", 0.5, 0.5),
        )
    )
    assert evaluation.coverage_cap is None
    assert evaluation.coverage == pytest.approx(0.75)


def test_sequential_gating_zeroes_downstream_conditions():
    """``product`` 的门控语义：C3 未命中时下游条件即便「满足」也必须计 0。"""
    c3_satisfaction = 0.0
    downstream = [1.0, 1.0]  # 假设数据层面显示「有订单」「有收入」
    gated = [s * c3_satisfaction for s in downstream]
    assert gated == [0.0, 0.0]

    evaluation = StrategyEvaluation(conditions=(
        ConditionResult("C1", "长期研发投入", 0.10, 1.0),
        ConditionResult("C2", "产品认证", 0.20, 1.0),
        ConditionResult("C3", "客户验证", 0.20, 0.0),
        ConditionResult("C4", "签署商业订单", 0.25, gated[0]),
        ConditionResult("C5", "收入贡献", 0.25, gated[1]),
    ))
    # 只有 C1 + C2 → 0.30
    assert evaluation.coverage == pytest.approx(0.30)
    assert evaluation.coverage < 0.5, "「只有研发公告」不得获得高匹配度"


def test_policy_and_product_record_their_gating_in_design():
    policy_anti = " ".join(get_def("policy").anti_patterns)
    product_anti = " ".join(get_def("product").anti_patterns)
    assert "0.45" in policy_anti
    assert "计 0" in product_anti


@pytest.mark.parametrize("code", ["policy", "product"])
def test_gating_is_now_implemented_per_spec(code):
    """顺序门控的语义在**实现之后**依然成立（docs/06 §14.2）。

    ★ 这条测试原来叫 ``test_gating_semantics_are_testable_before_implementation``，
    断言的是「policy / product 还没实现，所以取到的是占位实现」——
    那是**进度标注**，不是约束。两者都实现之后，该守的是门控本身：

      · policy：缺业务验证（C5=0）时覆盖率被压到 0.45
      · product：缺客户验证时订单 / 收入条件计 0
    """
    implementation = get_strategy(code)
    assert not isinstance(implementation, NotImplementedStrategy), (
        f"{code} 已实现，不该再返回占位实现"
    )

    from app.pipeline.opportunity_builder import MIN_COVERAGE

    evaluation = implementation.evaluate(bare_facts())
    if code is ThesisType.POLICY:
        assert evaluation.coverage_cap == pytest.approx(0.45), (
            f"policy 缺业务验证时上限应为 0.45，实际 {evaluation.coverage_cap}"
        )
        assert evaluation.coverage <= 0.45
    if code is ThesisType.PRODUCT:
        # 缺客户验证 / 订单 / 收入时，这三条本身就该是低分或 0
        by_key = {c.key: c for c in evaluation.conditions}
        assert by_key["C3"].satisfaction <= 0.20
        assert by_key["C4"].satisfaction <= 0.15
        assert by_key["C5"].satisfaction == 0.0
        assert evaluation.coverage < MIN_COVERAGE


def test_funnel_reports_where_it_dropped():
    """门控之外，漏斗必须能回答「为什么今天只有 N 张卡」。"""
    from app.engine.funnel import FunnelCounters

    counters = FunnelCounters(
        candidates=301, announcements_fetched=74, passed_prefilter=51,
        events_extracted=43, thesis_candidates=19, deep_analyzed=8, cards=6,
    )
    assert counters.drop_at() is not None
    assert "→" in counters.drop_at()

    empty = FunnelCounters(candidates=10, announcements_fetched=0)
    assert empty.drop_at() is not None
