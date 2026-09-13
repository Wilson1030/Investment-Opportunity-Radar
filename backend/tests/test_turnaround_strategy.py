"""``turnaround``（困境反转）专项测试 —— 重点是 docs/06 §16 的验收闸门。

> | 2 | ``turnaround`` | 验证**策略与 ST 标签解耦**（示例 J） |

规格示例 J 的原话：

> **投资策略应该决定股票为什么被发现，而不是股票标签决定投资策略。**

这条约束不能靠「记得别这么写」，所以本文件用三种方式守它：

  1. **源码扫描**：``turnaround/rules.py`` 的可执行代码里不得出现 ``is_st``
  2. **行为等价**：只翻转 ``is_st``，coverage 必须**完全相同**
     —— 这是最直接的解耦证明（标签对结论零影响）
  3. **双向反例**：非 ST + 真实改善 → 建卡；ST + 无改善 → 不建卡

另外守两条 ``restructuring`` 上没有的判断：

  · **C1 / C2 是必要条件**：没有「困境」或没有「改善」都不是反转
    （实测踩到：过去从未恶化的公司拿到 0.49 coverage，足以越过建卡门槛）
  · **失效要看财务**：反转逻辑常死于「悄悄又亏了一个季度」，
    而不是死于某条「终止」公告 —— 只看事件会漏掉
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from app.facts import CompanyFacts, EventFact, FinancialFacts, StrategyFacts
from app.models.enums import EventType, InvalidationSeverity, ReliabilityLevel, ThesisType
from app.pipeline.opportunity_builder import MIN_COVERAGE
from app.strategies import get_strategy
from app.strategies.turnaround import invalidation, questions
from app.strategies.turnaround.rules import (
    STRATEGY,
    _condition_c1,
    _condition_c2,
    _condition_c5,
)
from tests.conftest import bare_facts

#: 一个「真反转」的财务画像：**先恶化，后改善** —— 两个方向同时成立
TURNAROUND_FINANCIALS = FinancialFacts(
    periods_with_data=8,
    # 过去：明确恶化（C1）
    revenue_declining_run_max=5,
    margin_declining_run_max=3,
    loss_run_max=4,
    # 最近：连续改善（C2）
    revenue_improving_quarters=2,
    margin_improving_quarters=2,
    ocf_positive=True,
    ocf_improving=True,
    # 最近仍在下降的期数归零（改善已经发生）
    revenue_declining_quarters=0,
    margin_declining_quarters=0,
)


def _facts(*, is_st: bool, financials: FinancialFacts = TURNAROUND_FINANCIALS,
           events: tuple[EventFact, ...] = ()) -> StrategyFacts:
    return bare_facts(
        company=CompanyFacts(id=1, name="测试公司", code="000001", is_st=is_st),
        financials=financials,
        events=events,
    )


def _evaluation(**kwargs):
    return STRATEGY.evaluate(_facts(**kwargs))


# --------------------------------------------------------------------------- #
# ★ 验收闸门：策略与 ST 标签解耦
# --------------------------------------------------------------------------- #
def test_rules_source_never_reads_the_st_flag():
    """源码扫描：``turnaround/rules.py`` 的可执行代码里不得出现 ``is_st``。

    ★ 为什么用源码扫描而不是「跑几组数据看看」：
    标签依赖最典型的形式是「顺手加一条兜底」——
    ``if is_st and 没别的信号: 给个基础分``。这种代码在多数样例上看不出问题，
    只有在标签与数据不一致的公司上才暴露。扫描能直接拦住它。

    文档字符串里提到 ``is_st`` 是允许的（那是**解释为什么不用它**），
    所以这里先剥掉注释与字符串。
    """
    from app.strategies.turnaround import rules as rules_module

    source = open(rules_module.__file__, encoding="utf-8").read()

    # 剥掉三引号块与行注释，只留可执行代码
    without_docstrings = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    without_comments = re.sub(r"#[^\n]*", "", without_docstrings)
    executable = re.sub(r"'''[^']*'''", "", without_comments)

    assert "is_st" not in executable, (
        "turnaround/rules.py 的可执行代码里出现了 is_st —— "
        "违反示例 J（标签不得决定策略）"
    )


#: 覆盖各个**分支边界**的财务画像 —— 只在「正常路径」上验等价是不够的。
#:
#: ★ 教训：最早这组测试只用了「真反转」一个画像（C1/C2 都 > 0）。
#: 故意在 C1 == 0 的分支里植入 ``if is_st: 给 0.30 兜底分`` 之后，
#: **源码扫描抓到了，但这个等价测试没抓到** —— 因为那个分支根本没被执行到。
#: 守卫只测顺利路径，等于没测。
_FLIP_PROFILES: dict[str, FinancialFacts] = {
    "真反转": TURNAROUND_FINANCIALS,
    "无困境（C1=0 分支）": replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_run_max=0, margin_declining_run_max=0, loss_run_max=0,
    ),
    "无改善（C2=0 分支）": replace(
        TURNAROUND_FINANCIALS,
        revenue_improving_quarters=0, margin_improving_quarters=0,
        ocf_improving=False, ocf_positive=False,
    ),
    "数据不足（periods<2 分支）": FinancialFacts(periods_with_data=1),
    "财务侧失效": replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_quarters=2, margin_declining_quarters=2,
    ),
}


@pytest.mark.parametrize("label", sorted(_FLIP_PROFILES))
def test_flipping_only_the_st_flag_changes_nothing(label):
    """★ 只翻转 ``is_st``，coverage 与阶段必须**完全相同**。

    这是解耦最直接的证明：如果标签对结论有**任何**影响，
    这两个结果就不可能相等。

    参数化覆盖每个分支边界 —— 只测一个画像会漏掉「只在某个分支里读标签」的写法。
    """
    financials = _FLIP_PROFILES[label]
    as_st = STRATEGY.evaluate(_facts(is_st=True, financials=financials))
    not_st = STRATEGY.evaluate(_facts(is_st=False, financials=financials))

    assert as_st.coverage == pytest.approx(not_st.coverage), (
        f"[{label}] 翻转 ST 标签改变了 coverage —— 标签在影响策略"
    )
    assert as_st.coverage_raw == pytest.approx(not_st.coverage_raw)
    assert [c.satisfaction for c in as_st.conditions] == [
        c.satisfaction for c in not_st.conditions
    ], f"[{label}] 某个核心条件的满足度被标签改变了"

    stage_st = STRATEGY.catalyst_strength(_facts(is_st=True, financials=financials))
    stage_not = STRATEGY.catalyst_strength(_facts(is_st=False, financials=financials))
    assert stage_st == stage_not, f"[{label}] 翻转 ST 标签改变了催化阶段"

    hits_st = invalidation.detect(_facts(is_st=True, financials=financials))
    hits_not = invalidation.detect(_facts(is_st=False, financials=financials))
    assert len(hits_st) == len(hits_not), f"[{label}] 失效判定被标签影响了"


def test_non_st_company_with_real_improvement_gets_high_coverage():
    """非 ST 公司只要经营数据支持，就该拿到高分（示例 J 的正向要求）。"""
    evaluation = _evaluation(is_st=False)
    assert evaluation.coverage >= MIN_COVERAGE
    assert evaluation.coverage >= 0.60, f"真反转画像的覆盖率仅 {evaluation.coverage}"

    statement = STRATEGY.build_statement(_facts(is_st=False), evaluation)
    assert "ST" not in statement, f"叙事里不该以标签为主语：{statement}"


def test_st_company_without_improvement_is_rejected():
    """ST 公司若没有改善数据 → 不建卡（标签不能替代策略条件）。"""
    no_improvement = replace(
        TURNAROUND_FINANCIALS,
        revenue_improving_quarters=0,
        margin_improving_quarters=0,
        ocf_improving=False,
        ocf_positive=False,
    )
    evaluation = _evaluation(is_st=True, financials=no_improvement)
    assert evaluation.coverage < MIN_COVERAGE, (
        f"ST + 无改善却拿到 {evaluation.coverage} —— 标签在起作用"
    )


# --------------------------------------------------------------------------- #
# C1 / C2 是必要条件（顺序门控）
# --------------------------------------------------------------------------- #
def test_no_past_deterioration_caps_coverage():
    """没有「过去恶化」就不是反转 —— 盖上低于建卡门槛的上限。

    实测背景：南网能源过去从未连续恶化（C1 = 0.00），
    却凭改善侧的分数拿到 0.49 coverage，足以建出一张
    **用错逻辑解释这家公司**的「困境反转」卡。
    """
    only_improving = replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_run_max=0,
        margin_declining_run_max=0,
        loss_run_max=0,
    )
    c1 = _condition_c1(_facts(is_st=False, financials=only_improving))
    assert c1.satisfaction == 0.0

    evaluation = _evaluation(is_st=False, financials=only_improving)
    assert evaluation.coverage < MIN_COVERAGE, (
        f"没有困境却越过建卡门槛：{evaluation.coverage}"
    )
    assert evaluation.coverage_cap is not None


def test_no_improvement_caps_coverage():
    """没有「最近改善」也不是反转（只是还在恶化）。"""
    only_worse = replace(
        TURNAROUND_FINANCIALS,
        revenue_improving_quarters=0,
        margin_improving_quarters=0,
        ocf_improving=False,
        ocf_positive=False,
        revenue_declining_quarters=3,
        margin_declining_quarters=3,
    )
    evaluation = _evaluation(is_st=False, financials=only_worse)
    assert evaluation.coverage < MIN_COVERAGE
    assert evaluation.coverage_cap is not None


def test_coverage_cap_is_below_the_creation_gate():
    """门控上限必须**严格低于**建卡门槛 —— 否则这个上限形同虚设。

    两者一旦反了（门槛被调低），门控就失效了，而且不会有任何报错。
    """
    evaluation = _evaluation(
        is_st=False,
        financials=replace(TURNAROUND_FINANCIALS, revenue_declining_run_max=0,
                           margin_declining_run_max=0, loss_run_max=0),
    )
    assert evaluation.coverage_cap is not None
    assert evaluation.coverage_cap < MIN_COVERAGE, (
        f"门控上限 {evaluation.coverage_cap} ≥ 建卡门槛 {MIN_COVERAGE} —— "
        "上限比门槛还高，等于没门控"
    )


def test_insufficient_data_never_claims_deterioration_or_improvement():
    """财务期数不足时**不能**断言恶化或改善 —— 「不知道」不等于「有」。"""
    empty = FinancialFacts(periods_with_data=1)
    c1 = _condition_c1(_facts(is_st=False, financials=empty))
    c2 = _condition_c2(_facts(is_st=False, financials=empty))
    assert c1.satisfaction == 0.0
    assert c2.satisfaction == 0.0
    assert "不足" in c1.detail and "不足" in c2.detail


# --------------------------------------------------------------------------- #
# C5 反向计分
# --------------------------------------------------------------------------- #
def test_c5_scores_the_evidence_source_not_the_label():
    """C5 检查的是「改善依据来自经营数据」，而不是「公司是不是 ST」。"""
    strong = _evaluation(is_st=False)
    c5_strong = next(c for c in strong.conditions if c.key == "C5")
    assert c5_strong.satisfaction == 1.0
    assert "与 ST 标签无关" in c5_strong.detail

    weak = _condition_c5(
        _facts(is_st=False),
        _condition_c2(_facts(is_st=False, financials=FinancialFacts(periods_with_data=1))),
    )
    assert weak.satisfaction == 0.0


# --------------------------------------------------------------------------- #
# 失效：事件侧
# --------------------------------------------------------------------------- #
def _event(title: str, event_type: EventType = EventType.EARNINGS_TURNAROUND) -> EventFact:
    return EventFact(id=1, event_type=event_type, title=title,
                     evidence_level=ReliabilityLevel.A)


@pytest.mark.parametrize("title,expected", [
    ("关于公司经营现金流再次转负的公告", True),
    ("关于业绩预亏暨亏损扩大的提示性公告", True),
    ("关于毛利率下降的风险提示公告", True),
    ("关于公司中标重大项目并确认收入的公告", False),
    ("关于业绩预增的公告", False),
])
def test_event_side_invalidation(title, expected):
    facts = _facts(is_st=True, events=(_event(title),))
    hits = invalidation.detect(facts)
    assert invalidation.should_invalidate(hits) is expected, title


def test_third_party_deterioration_does_not_invalidate():
    """子公司 / 控股股东的经营变化不是母公司的反转失效（复用主体判定）。"""
    facts = _facts(is_st=True, events=(
        _event("关于全资子公司经营现金流再次转负的公告"),
    ))
    assert not invalidation.should_invalidate(invalidation.detect(facts))


# --------------------------------------------------------------------------- #
# 失效：★ 财务侧（restructuring 没有这条腿）
# --------------------------------------------------------------------------- #
def test_financial_deterioration_invalidates_without_any_announcement():
    """★ 反转逻辑常死于「悄悄又亏了一个季度」，而不是死于一条公告。

    只看事件的话，这个逻辑已经死了而雷达上还挂着它 ——
    所以财务侧必须有独立的失效判定。
    """
    deteriorating = replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_quarters=2,
        margin_declining_quarters=2,
    )
    facts = _facts(is_st=False, financials=deteriorating, events=())
    hits = invalidation.detect(facts)

    assert hits, "财务连续恶化却没有判失效 —— 死掉的逻辑会一直挂着"
    assert invalidation.should_invalidate(hits)
    assert hits[0].severity == InvalidationSeverity.TERMINAL.value
    # 这条失效**没有事件**，必须仍然可追溯到「依据是什么」
    assert "连续下降期数" in hits[0].reason


def test_single_declining_quarter_is_not_terminal():
    """单期回落不判死 —— 一个季度的波动是噪声，不是逻辑死亡。"""
    noisy = replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_quarters=1,
        margin_declining_quarters=0,
    )
    assert not invalidation.should_invalidate(
        invalidation.detect(_facts(is_st=False, financials=noisy))
    )


def test_insufficient_data_never_invalidates():
    """数据不足时**不判失效** —— 「不知道」不能当成「恶化」。"""
    assert not invalidation.detect(
        _facts(is_st=False, financials=FinancialFacts(periods_with_data=1))
    )


# --------------------------------------------------------------------------- #
# 催化剂阶梯
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("financials,states,expected_stage,early", [
    (TURNAROUND_FINANCIALS, {}, "连续两季改善", False),
    (replace(TURNAROUND_FINANCIALS, revenue_improving_quarters=1,
             margin_improving_quarters=1), {}, "单季改善", True),
    (replace(TURNAROUND_FINANCIALS, revenue_improving_quarters=0,
             margin_improving_quarters=0, ocf_turned_positive=False),
     {}, "弱改善迹象", True),
])
def test_financial_ladder(financials, states, expected_stage, early):
    stage = STRATEGY.catalyst_strength(_facts(is_st=False, financials=financials))
    assert stage.stage == expected_stage
    assert stage.early is early


def test_event_ladder_can_raise_the_stage():
    """业绩预告上调 / 年报确认由**公告**驱动，取两条腿里较高的那个。"""
    upgrade = STRATEGY.catalyst_strength(_facts(
        is_st=False,
        financials=replace(TURNAROUND_FINANCIALS, revenue_improving_quarters=1,
                           margin_improving_quarters=1),
        events=(_event("关于业绩预增的公告"),),
    ))
    assert upgrade.stage == "业绩预告上调"
    assert upgrade.score == 85.0


def test_invalidated_logic_never_keeps_a_positive_stage():
    """失效优先：已失效的机会不该还挂着「连续两季改善」（卡片会自相矛盾）。"""
    facts = _facts(
        is_st=False,
        financials=replace(TURNAROUND_FINANCIALS, revenue_declining_quarters=2,
                           margin_declining_quarters=2),
        events=(),
    )
    stage = STRATEGY.catalyst_strength(facts)
    assert stage.score == 0.0
    assert "失效" in stage.stage or "终止" in stage.stage


# --------------------------------------------------------------------------- #
# 待确认：诚实边界
# --------------------------------------------------------------------------- #
def test_industry_question_is_marked_unverifiable_not_confirmed():
    """★ 「行业景气是否能够持续」当前**无法判定**，必须明说。

    如果它某天「自动确认」了，那一定是假确认 —— 我们没有行业数据。
    """
    seeds = {s.question: s for s in questions.evaluate(_facts(is_st=False))}
    industry = next(q for q in seeds if "行业景气" in q)
    assert seeds[industry].status == "unverifiable"
    assert "无行业数据" in seeds[industry].detail

    # 它仍然算「未确认」（不能用「无法判定」冒充「已确认」）
    assert industry in questions.open_only(_facts(is_st=False))


def test_all_spec_templates_are_present():
    """规格示例 B 给的四个问题必须都在。"""
    asked = set(questions.templates())
    for fragment in ("改善是否具有持续性", "新订单", "行业景气", "一次性因素"):
        assert any(fragment in q for q in asked), f"缺少待确认事项：{fragment}"


# --------------------------------------------------------------------------- #
# Thesis 必须同时说清「先恶化」与「后改善」
# --------------------------------------------------------------------------- #
def test_statement_covers_both_directions():
    evaluation = _evaluation(is_st=False)
    statement = STRATEGY.build_statement(_facts(is_st=False), evaluation)
    assert "下降" in statement or "亏损" in statement, f"没讲历史恶化：{statement}"
    assert "增长" in statement or "改善" in statement or "转正" in statement, (
        f"没讲近期改善：{statement}"
    )
    assert "持续性" in statement, "必须声明改善的持续性尚待确认（示例 B）"


def test_statement_is_honest_when_there_is_no_deterioration():
    """只有改善、没有恶化时，措辞必须如实说「更像增长，不是反转」。

    不能硬套「困境反转」的模板 —— 那是用错的逻辑解释一家公司。
    """
    improving_only = replace(
        TURNAROUND_FINANCIALS,
        revenue_declining_run_max=0, margin_declining_run_max=0, loss_run_max=0,
    )
    facts = _facts(is_st=False, financials=improving_only)
    statement = STRATEGY.build_statement(facts, STRATEGY.evaluate(facts))
    assert "不是" in statement or "更接近" in statement, statement


def test_why_now_mentions_one_off_risk_when_improving():
    """规格示例 B 要求提示「改善是否依赖一次性因素」。"""
    why_now = STRATEGY.why_now(_facts(is_st=False))
    assert "一次性" in why_now["recent"]
    assert set(why_now) == {"past", "recent", "this_week", "conclusion"}


def test_strategy_is_registered_and_matches_the_registry():
    """注册的实例与 registry 的声明一致（防止改了 registry 忘了实现）。"""
    assert get_strategy(ThesisType.TURNAROUND) is STRATEGY
    assert STRATEGY.code is ThesisType.TURNAROUND
    definition = STRATEGY.evaluate(_facts(is_st=False)).conditions
    from app.strategies.registry import get_def

    declared = get_def(ThesisType.TURNAROUND).core_conditions
    assert [c.key for c in definition] == [c.key for c in declared]
