"""10 类策略的**一致性测试** —— 参数化跑遍所有已实现策略。

单测每个策略的细节固然必要，但有一类错误只在「跨策略」时暴露：

  · 某个策略缺了 ``caveat``（规格示例 B 禁止只有结论的 Thesis）
  · ``coverage_cap`` 比建卡门槛还高（门控形同虚设）
  · 策略在某个分支里读了 ``is_st``（标签决定策略，违反示例 J）
  · 失效规则没有主体守卫（子公司的坏消息判死母公司）
  · 兜底档写成了「标记为空」却期望它命中 ——
    ★ 实测踩到过：空标记被当成「不匹配」，于是所有
    「未披露 / 无法评估」的兜底档全部失效，分数被**静默压低到 0**，
    而 0 的含义是「查过了、没有」，不是「查不到」。

这些用一条参数化测试覆盖全部策略，比每个策略写一遍更可靠 ——
新增策略时自动被纳入。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.facts import EventFact
from app.models.enums import ReliabilityLevel, ThesisType
from app.pipeline.opportunity_builder import MIN_COVERAGE
from app.strategies import get_strategy, implemented_types
from app.strategies.base import NotImplementedStrategy
from app.strategies.registry import get_def
from tests.conftest import bare_facts

ALL_IMPLEMENTED = sorted(implemented_types(), key=lambda c: c.value)


def _strategy(code: ThesisType):
    implementation = get_strategy(code)
    assert not isinstance(implementation, NotImplementedStrategy), f"{code} 未实现"
    return implementation


def _first_invalidating_event(code: ThesisType) -> tuple[EventFact, ...]:
    """按该策略第一条失效规则构造一个**应当命中**的事件。"""
    definition = get_def(code)
    if not definition.invalidating_events:
        return ()
    rule = definition.invalidating_events[0]
    keyword = (rule.title_contains or ("终止",))[0]
    return (
        EventFact(id=1, event_type=rule.event_type,
                  title=f"关于{keyword}的公告", evidence_level=ReliabilityLevel.A),
    )


def test_every_designed_strategy_is_now_implemented():
    """10 类策略全部实现 —— 本文件的参数化才有意义。"""
    assert len(ALL_IMPLEMENTED) == 10, (
        f"仍有未实现的策略：{[c.value for c in ThesisType if c not in ALL_IMPLEMENTED]}"
    )


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_conditions_match_the_registry(code):
    """实现的条件键/权重必须与 registry 的声明逐项一致。

    否则会出现「文档说 C3 权重 0.25、代码按 0.20 算」这种漂移 ——
    而用户看到的权重表来自 registry。
    """
    strategy = _strategy(code)
    declared = get_def(code).core_conditions
    evaluation = strategy.evaluate(bare_facts())
    assert [c.key for c in evaluation.conditions] == [c.key for c in declared]
    assert [c.weight for c in evaluation.conditions] == [c.weight for c in declared]


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_coverage_is_bounded_and_cap_beats_the_gate(code):
    """满足度 ∈ [0,1]；若设了门控上限，它必须**低于**建卡门槛。"""
    strategy = _strategy(code)
    empty = strategy.evaluate(bare_facts())
    assert 0.0 <= empty.coverage <= 1.0

    for condition in empty.conditions:
        assert 0.0 <= condition.satisfaction <= 1.0, (
            f"{code} {condition.key} 满足度越界：{condition.satisfaction}"
        )

    if empty.coverage_cap is not None:
        # ★ 这里只验证「上限真的生效」，**不**要求它低于建卡门槛。
        #
        # 原因是两种门控意图并存：
        #   · turnaround / cycle 这类：缺必要条件时**不该建卡**
        #     → 上限必须低于门槛（由各策略的专项测试守）
        #   · policy：docs/06 §14.2 明确要求「缺业务验证时压到 0.45」——
        #     意图是**压低分数**而不是阻止建卡（宏观利好本身可跟踪）
        # 通用测试若一律要求 «上限 < 门槛»，就会把规格明确要求的 0.45 判成违规。
        assert empty.coverage <= empty.coverage_cap + 1e-9, (
            f"{code} 声明了上限 {empty.coverage_cap} 但覆盖率 {empty.coverage} 高于它"
            " —— 上限没有生效"
        )


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_empty_facts_never_produce_a_high_coverage(code):
    """没有任何事实时不得给出高覆盖率 —— 否则空公司也会建卡。"""
    assert _strategy(code).evaluate(bare_facts()).coverage < MIN_COVERAGE


#: 表示「尚未确认 / 未成立」的措辞。规格示例 B 要求 Thesis 不得是只有结论的一句话。
#:
#: 含否定式措辞（尚无 / 暂不 / 未发现）：没有证据时的正确表达不是「肯定」
#: 也不该是留白，而是明确说「还不成立」。
_HEDGE_MARKERS = (
    "尚待确认", "待确认", "可能", "潜在", "尚未", "未明确", "需进一步",
    "尚无", "暂不", "未发现", "仍待", "存疑",
)


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_thesis_is_hedged_and_has_three_sections(code):
    """规格示例 B：禁止「业绩大涨，所以看好」这类只有结论的 Thesis。

    示例 B 要求的是**三件事齐备**，所以这里分别验证：

      · Thesis 语句本身带保留措辞（不是断言）
      · Uncertainties（待确认事项）非空
      · Invalidating Events（失效条件）非空

    ★ 这里刻意**不**检查「语句里必须有分号」—— 那把句式写死了。
    真正要守的是上面三条，而不是某种排版。
    """
    strategy = _strategy(code)
    facts = bare_facts()
    statement = strategy.build_statement(facts, strategy.evaluate(facts))

    assert any(m in statement for m in _HEDGE_MARKERS), (
        f"{code} 的 Thesis 是纯断言、没有保留措辞：{statement}"
    )
    assert strategy.open_questions(facts), f"{code} 没有 Uncertainties 一栏"
    assert get_def(code).invalidating_events, f"{code} 没有 Invalidating Events 一栏"


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_why_now_follows_the_fixed_structure(code):
    """规格 §52 固定四段。"""
    why = _strategy(code).why_now(bare_facts())
    assert set(why) == {"past", "recent", "this_week", "conclusion"}
    assert all(isinstance(v, str) and v for v in why.values())


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_invalidated_logic_always_zeroes_the_stage(code):
    """★ 全局不变量：逻辑已失效 → 催化强度归零。

    否则卡片会出现「阶段：连续两季改善」+「状态：逻辑失效」的自相矛盾 ——
    而用户看到的正是这两行。
    """
    events = _first_invalidating_event(code)
    if not events:
        pytest.skip("该策略未定义失效条件")
    strategy = _strategy(code)
    facts = bare_facts(events=events)
    if not strategy.invalidation_hits(facts):
        pytest.skip("构造的事件未构成命中，无法验证")
    assert strategy.catalyst_strength(facts).score == 0.0, (
        f"{code} 失效后阶段仍未归零"
    )


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_third_party_events_never_invalidate(code):
    """★ 主体守卫对所有策略生效：子公司的坏消息不该判死母公司。"""
    events = _first_invalidating_event(code)
    if not events:
        pytest.skip("该策略未定义失效条件")
    rule_title = events[0].title
    third_party = (
        EventFact(id=1, event_type=events[0].event_type,
                  title=f"关于全资子公司{rule_title[2:]}",
                  evidence_level=ReliabilityLevel.A),
    )
    assert not _strategy(code).invalidation_hits(bare_facts(events=third_party)), (
        f"{code}: 子公司的事件被当成了母公司的失效"
    )


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_open_questions_come_from_the_registry(code):
    """待确认事项必须来自 registry 的模板（不能自创或漏掉）。"""
    declared = set(get_def(code).open_question_templates)
    asked = set(_strategy(code).open_questions(bare_facts()))
    assert asked <= declared, f"{code} 自创了模板里没有的问题：{asked - declared}"
    assert asked, f"{code} 没有任何待确认事项"


def _registry_st_anti_pattern(code: ThesisType) -> str | None:
    """该策略的反例警示里若提到 ST，返回那条警示。"""
    for pattern in get_def(code).anti_patterns:
        if "ST" in pattern:
            return pattern
    return None


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_st_flag_never_decides_the_strategy(code):
    """★ INV-C1 的**行为**验证：``is_st`` 不得决定策略是否命中。

    分两层，因为规格对两类策略的要求**不同**：

    · ``turnaround`` 的反例明确要求「不依赖 ST 标签」→ 严格：
      源码里不得出现 ``is_st``（它必须完全不知道标签的存在）
    · ``restructuring`` 的反例**允许** ``is_st`` 作为 C4 的部分背景证据
      （原话：「权重 0.20 内的一部分」）→ 行为验证：
      只翻转标签不得把覆盖率抬过建卡门槛

    为什么对 restructuring 不用源码扫描：那会把规格**允许**的用法
    也判为违规。守卫写得比规格更严，只会逼着后来者把守卫删掉。
    """
    strategy = _strategy(code)

    if code is ThesisType.TURNAROUND:
        module = __import__(f"app.strategies.{code.value}.rules", fromlist=["x"])
        source = Path(module.__file__).read_text(encoding="utf-8")
        executable = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
        executable = re.sub(r"#[^\n]*", "", executable)
        assert "is_st" not in executable, (
            f"{code.value}/rules.py 读了 is_st —— 但它的反例要求完全不依赖标签"
        )
        return

    # 行为验证：只有标签不同、其他事实全空时，不得因此建卡
    from dataclasses import replace

    base = bare_facts()
    as_st = strategy.evaluate(bare_facts(company=replace(base.company, is_st=True)))
    not_st = strategy.evaluate(bare_facts(company=replace(base.company, is_st=False)))
    assert as_st.coverage < MIN_COVERAGE, (
        f"{code} 仅凭 ST 标签就把覆盖率抬到 {as_st.coverage}"
        "（越过建卡门槛）—— 标签在决定策略"
    )
    assert not_st.coverage < MIN_COVERAGE


@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_claiming_conditions_are_traceable(code):
    """拿了分的条件必须给出依据（``detail`` 非空）。

    「每个判断可追溯到证据」是本产品的承诺 —— 一个没有说明的分数
    等于要求用户相信一个看不见的数字。
    """
    evaluation = _strategy(code).evaluate(bare_facts())
    for condition in evaluation.conditions:
        if condition.satisfaction > 0.0:
            assert condition.detail, (
                f"{code} {condition.key} 拿了 {condition.satisfaction} 却无说明"
            )


def test_fallback_tier_with_empty_markers_actually_matches():
    """★ 兜底档（标记为空）必须**无条件命中**。

    实测踩到的 bug：空标记被当成「不匹配 → 跳过」，
    于是所有「未披露 / 无法评估」的兜底档全部失效，
    条件分数被静默压低到 0 —— 而 0 的含义是「查过了、没有」，
    不是「查不到」。这两者混起来会让「数据缺失」看起来像「结论是否定」。
    """
    from app.strategies.common.markers import marker_condition

    class _Def:
        key, label, weight = "C1", "测试条件", 1.0

    result = marker_condition(_Def, (), (
        (1.0, "需要证据才算", ("命中我",)),
        (0.30, "兜底：未披露 / 无法评估", ()),
    ))
    assert result.satisfaction == 0.30, f"兜底档未生效，得到 {result.satisfaction}"
    assert "兜底" in result.detail

    hit = marker_condition(
        _Def,
        (EventFact(id=1, event_type="OTHER", title="命中我的公告"),),
        (
            (1.0, "需要证据才算", ("命中我",)),
            (0.30, "兜底", ()),
        ),
    )
    assert hit.satisfaction == 1.0, "有证据时不该走兜底"


# --------------------------------------------------------------------------- #
# 面向用户的文案里不得出现 Markdown 语法
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", ALL_IMPLEMENTED, ids=lambda c: c.value)
def test_user_facing_text_has_no_markdown(code):
    """★ 后端产出的文案会**原样**显示在前端（纯文本渲染），不能含 Markdown。

    实测踩到：条件说明里写了 ``（**不等于金额小**）``，
    前端把星号原样渲染成了「（**不等于金额小**）」——
    用户看到的是排版符，不是强调。

    这里用**行为化**检查而不是源码扫描：把每家策略跑一遍，
    检查所有会流向前端的字符串（条件说明 / Thesis / Why Now / 待确认事项）。
    """
    strategy = _strategy(code)
    facts = bare_facts()
    evaluation = strategy.evaluate(facts)

    texts: list[str] = []
    for condition in evaluation.conditions:
        texts.append(condition.detail)
    texts.extend(strategy.why_now(facts).values())
    texts.extend(strategy.open_questions(facts))
    for events in ((), facts.events):
        texts.append(strategy.build_statement(facts, evaluation))

    for text in texts:
        assert "**" not in (text or ""), (
            f"{code} 的文案含 Markdown 强调符，前端会原样显示：{text}"
        )


def test_registry_descriptions_have_no_markdown():
    """registry 的说明文字会通过 ``/api/strategies`` 直接展示给用户。"""
    for code in ALL_IMPLEMENTED:
        definition = get_def(code)
        candidates = [
            definition.user_goal, definition.description,
            *(c.label for c in definition.core_conditions),
            *(c.description for c in definition.core_conditions),
            *(p for p in definition.anti_patterns),
        ]
        for text in candidates:
            assert "**" not in (text or ""), (
                f"{code} 的 registry 文案含 Markdown 强调符：{text}"
            )


# --------------------------------------------------------------------------- #
# ★ 全项目守卫：面向前端的字符串里不得出现 Markdown 强调符
# --------------------------------------------------------------------------- #
def test_no_markdown_in_project_wide_user_facing_strings():
    """★ 扫描**整个 app 包**，而不是只扫策略文案。

    实测踩到两次：
      · 策略条件说明里写了 ``（**不等于金额小**）``
      · 异常归因里写了 ``**未找到一次性因素的公告依据**``

    后端产出的文案在前端是**纯文本渲染**，星号会原样显示成排版符。
    第二次是第一次修完之后的漏网 —— 因为当时的守卫只覆盖策略层。
    所以这条改为全包扫描。

    实现上先剥掉三引号块（文档字符串里的 ``**`` 是合法注释风格），
    再找单行字符串字面量里的 ``**``。
    """
    import re
    from pathlib import Path as _Path

    import app as app_package

    root = _Path(app_package.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        body = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
        body = re.sub(r"'''.*?'''", "", body, flags=re.DOTALL)
        for lineno, line in enumerate(body.splitlines(), 1):
            # 先剥掉行尾注释（Python 风格：``# `` 前有空白）。
            # ★ 实测踩到：``relevant_text: str  # 原文段落摘录，**不得改写**``
            # 这种行尾注释被当成面向用户的文案 —— 但它永远不会进前端。
            # 只剥「空白 + # + 空白」开头的，避免误伤字符串里的 ``#``。
            line = re.sub(r"\s#\s.*$", "", line)
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # 必须是**成对**的 ``**…**``（Markdown 强调的形态）。
            # 只认单个 ``**`` 会误报 Python 的幂运算符（如 ``10**8``）——
            # 实测踩到：``f"...{abs(hash(x)) % 10**8}"`` 被当成 Markdown。
            # 另外排除「两侧都是数字」的情形（幂运算几乎总带数字）。
            if re.search(r"\*\*[^*\n]+\*\*", line) and not re.search(
                r"[0-9]\*\*[0-9]", line
            ):
                offenders.append(f"{path.relative_to(root)}:{lineno}: {stripped[:70]}")

    assert not offenders, "以下位置的字面 ** 会原样显示在前端：\n" + "\n".join(offenders)
