"""INV-C1 的源码级守卫：禁止「标签 → 策略」的直接映射。

规格 §5.6 示例 J：
> **投资策略应该决定股票为什么被发现，而不是股票标签决定投资策略。**

这条约束**不能靠 prompt 提醒**，所以用源码扫描来守。
"""

from __future__ import annotations

import pytest

from app.engine import guard
from app.facts import CompanyFacts
from app.models.enums import EventType, ReliabilityLevel, ThesisType
from app.strategies import STRATEGIES
from app.strategies.restructuring.rules import STRATEGY
from tests.conftest import bare_facts


def _rule_sources() -> list[tuple[str, str]]:
    backend = guard.__file__.rsplit("app", 1)[0]
    from pathlib import Path

    root = Path(backend)
    found: list[tuple[str, str]] = []
    for pattern in guard.RULE_SOURCE_GLOBS:
        for path in root.glob(pattern):
            found.append((str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    return found


def test_rule_sources_are_found():
    sources = _rule_sources()
    assert len(sources) >= 3, f"未扫描到规则层源码，glob 配置可能失效：{sources}"


def test_no_forbidden_tag_mapping_helpers():
    """规则层不得出现 match_by_tag / TAG_TO_THESIS 之类的接口。"""
    violations: list[str] = []
    for relative_path, text in _rule_sources():
        for pattern in guard.FORBIDDEN_TAG_MAPPING_PATTERNS:
            if pattern in text:
                violations.append(f"{relative_path} 含被禁模式 {pattern!r}")
    assert not violations, violations


def test_no_strategy_mapping_keyed_on_st_flag():
    """不允许出现「标签 → 策略」的映射入口（INV-C1）。

    注意：``is_st`` 作为**复合条件的一部分**是合法的（C4 经营困境背景，
    见 docs/06 §3），因此这里不扫 ``if is_st`` 这类会误伤的字符串；
    「仅凭 ST 标签能拿多少分」由 ``test_st_flag_only_feeds_condition_c4_partially``
    从功能层面验证。
    """
    forbidden = ("match_st", "st_to_thesis", "if_only_st", "ST_ONLY_THESIS")
    violations: list[str] = []
    for relative_path, text in _rule_sources():
        for needle in forbidden:
            if needle in text:
                violations.append(f"{relative_path} 含以 ST 标签为条件的策略判定：{needle}")
    assert not violations, violations


def test_classifier_never_returns_thesis():
    """分类器只产出 EventType —— 它不得触碰策略（职责分离，规格 §28）。"""
    from app.engine import classifier

    result = classifier.classify_announcement("关于重大资产重组进展的公告")
    assert isinstance(result, EventType)
    assert not isinstance(result, ThesisType)

    # 即使标题里带 ST，也只会得到事件类型
    assert classifier.classify_announcement("ST 公司重大资产重组公告") is EventType.RESTRUCTURING


def test_st_flag_only_feeds_condition_c4_partially():
    """is_st 只进入 C4，且单独命中只给 0.35 满足度。"""
    st_only = bare_facts(company=CompanyFacts(id=1, name="ST 公司", is_st=True))
    evaluation = STRATEGY.evaluate(st_only)
    hits = {c.key: c.satisfaction for c in evaluation.conditions}
    assert hits["C1"] == 0.0
    assert hits["C2"] == 0.0
    assert hits["C3"] == 0.0
    assert hits["C4"] == pytest.approx(0.35)
    # 0.20 × 0.35 = 0.07 → 不会因为带 ST 就进入候选机会
    assert evaluation.coverage == pytest.approx(0.07)


def test_scope_reasons_are_not_strategy_mappings():
    """候选池理由只说明「为什么被扫到」，不说明「命中哪个策略」。"""
    from app.engine import scope

    reasons = scope.classify_scope_reason(
        is_st=True, is_risk_warning=False, restructuring_hits=2, control_change_hits=1
    )
    joined = " ".join(reasons)
    for code in ThesisType:
        assert code.value not in joined
    assert "ST" in joined


def test_event_type_to_thesis_mapping_exists_only_in_nl_parsing():
    """事件类型与策略的关联由 LLM 推导，而不是规则层写死的映射表。

    唯一允许的静态映射是自然语言解析里的关键词 → **事件**（人工可审的输入解析）。
    """
    from app.api import profile as profile_api

    assert hasattr(profile_api, "_EVENT_TO_THESIS")
    violations: list[str] = []
    for relative_path, text in _rule_sources():
        if "_EVENT_TO_THESIS" in text:
            violations.append(f"{relative_path} 引用了标签映射表")
    assert not violations, violations


# --------------------------------------------------------------------------- #
# 限售股解禁类公告：确定性可识别的假阳性
# --------------------------------------------------------------------------- #
def test_lockup_release_is_not_classified_as_restructuring():
    """★ 抽样实测（15 条真实公告）暴露的假阳性。

    标题带「重大资产重组」但正文讲的是**限售股解禁**：
        「中信证券……关于河北中瓷电子科技股份有限公司重大资产重组部分
          限售股份上市流通的核查意见」

    这类公告在每单重组完成后会**连续产生数年**（每批限售股解禁一次），
    若被判成重组催化，会持续制造幻影机会。
    """
    from app.engine import classifier

    negative_cases = [
        "中信证券股份有限公司关于河北中瓷电子科技股份有限公司重大资产重组部分限售股份上市流通的核查意见",
        "关于重大资产重组部分限售股份上市流通的提示性公告",
        "关于重大资产重组限售股解除限售的公告",
    ]
    for title in negative_cases:
        assert not classifier.is_non_restructuring("公司关于增持股份的公告")
        assert classifier.is_non_restructuring(title), title
        assert EventType.RESTRUCTURING not in classifier.classify_all(title), title
        assert classifier.classify_announcement(title) is not EventType.RESTRUCTURING, title


def test_genuine_restructuring_announcements_still_pass():
    """负向规则不得误伤真正的重组公告。"""
    from app.engine import classifier

    positive_cases = [
        "关于重大资产重组进展的公告",
        "关于筹划重大资产重组暨签订《股权投资意向协议》的提示性公告",
        "关于重大资产重组获得湖南省国资委批复的公告",
        "关于重大资产重组报告书（草案）的公告",
        "关于终止重大资产重组的公告",
    ]
    for title in positive_cases:
        assert classifier.classify_announcement(title) is EventType.RESTRUCTURING, title


def test_lockup_release_can_still_match_another_type():
    """若限售股公告同时涉及减持，应归到减持而不是被整体丢弃。"""
    from app.engine import classifier

    title = "关于重大资产重组限售股上市流通暨股东减持计划的公告"
    assert classifier.classify_announcement(title) is EventType.SHAREHOLDER_SELL
