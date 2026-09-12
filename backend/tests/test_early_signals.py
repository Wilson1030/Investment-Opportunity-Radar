"""催化剂阶段阶梯 —— 含**早期苗头**（用户明确要求「提前布局」）。

## 需求背景

> 对于预重组、提请法院受理、法院受理、债权人提请重组这种**还不太确定但有苗头**的
> 也要查找，因为要**提前布局**。

在此之前，这些表述在关键词白名单里**完全不匹配** —— 也就是说它们根本不会进入系统，
无论后面有多少分析能力都没用。

## 设计决定：「早期」是**阶段**，不是事件类型

新增枚举值会带来新的语义重叠。实测已经吃过一次苦头：
`RESTRUCTURING` 与 `M&A` 语义重叠导致 9/15 误判、`C1` 静默不命中。
所以这里：

* 事件类型不变（`RESTRUCTURING` / `BANKRUPTCY_REORGANIZATION` / `M&A` / `CONTROL_CHANGE`）
* 新增的是**阶段阶梯**的下段
* 阶段由确定性关键词识别（规则层，规格 §28），落库到 `Opportunity.catalyst_stage`

## 阶梯（分数 = 离「价值兑现」的距离）

| 阶段 | 分数 | 早期？ | 含义 |
|---|:---:|:---:|---|
| 存量｜重组已完成 | 5 | ✗ | 限售解禁等后续手续 —— 存量信息，**不是**新催化 |
| 早期｜筹划 / 停牌 / 意向协议 | 10 | ✓ | 苗头 |
| 早期｜预重整 / 重整申请 | 15 | ✓ | 苗头 |
| 早期｜法院受理 / 指定管理人 | 28 | ✓ | 苗头，但已进入司法程序 |
| 早期｜阶段未知 | 20 | ✓ | 无法确认进展 → 保守按早期处理 |
| 进展｜预案披露 | 40 | ✗ | |
| 进展｜草案 + 评估 | 60 | ✗ | |
| 进展｜获批复 / 审核通过 | 72 | ✗ | |
| 进展｜股东大会通过 | 80 | ✗ | |
| 完成｜监管核准 / 实施完成 | 95 | ✗ | |
| 终止 / 失败 | 0 | ✗ | 触发失效检测 |
"""

from __future__ import annotations

import pytest

from app.engine import classifier
from app.facts import CompanyFacts, EventFact, StrategyFacts
from app.models.enums import EventType, ThesisType
from app.strategies import get_def
from app.strategies.restructuring.rules import STRATEGY


def ladder_of(title: str) -> tuple[EventType | None, object, bool]:
    """返回 ``(规则层事件类型, 催化剂阶段, 是否早期)``。"""
    event_type = classifier.classify_announcement(title)
    facts = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=1, event_type=event_type, title=title),),
    )
    return event_type, STRATEGY.catalyst_strength(facts), STRATEGY.is_early_signal(facts)


# --------------------------------------------------------------------------- #
# 早期苗头（用户的核心要求）
# --------------------------------------------------------------------------- #
EARLY_CASES = [
    # 用户点名的四类表述
    ("关于债权人申请对公司进行重整的公告", EventType.BANKRUPTCY_REORGANIZATION, "重整申请", 15.0),
    ("关于法院裁定受理公司重整申请的公告", EventType.BANKRUPTCY_REORGANIZATION, "法院受理", 28.0),
    ("关于预重整债权申报的公告", EventType.BANKRUPTCY_REORGANIZATION, "预重整", 15.0),
    ("关于被债权人申请破产重整的提示性公告", EventType.BANKRUPTCY_REORGANIZATION, "重整申请", 15.0),
    # 其他早期形态
    ("关于控股股东筹划重大事项停牌的公告", EventType.RESTRUCTURING, "筹划 / 停牌", 10.0),
    ("关于签订股权投资意向协议的公告", EventType.M_AND_A, "意向协议", 10.0),
    ("关于拟筹划重大资产重组的提示性公告", EventType.RESTRUCTURING, "筹划 / 停牌", 10.0),
    ("关于法院指定管理人的公告", EventType.BANKRUPTCY_REORGANIZATION, "指定管理人", 28.0),
]


@pytest.mark.parametrize("title,event_type,stage_keyword,score", EARLY_CASES)
def test_early_signals_are_recognized(title, event_type, stage_keyword, score):
    """★ 这些公告必须能被识别、且被标为早期 —— 否则「提前布局」无从谈起。"""
    got_type, stage, early = ladder_of(title)
    assert got_type is event_type, f"{title} → {got_type}"
    assert stage_keyword in stage.stage, f"{title} → {stage.stage}"
    assert stage.score == score, f"{title} → {stage.score}"
    assert early is True, f"{title} 必须被标为早期信号"


@pytest.mark.parametrize("title,event_type,stage_keyword,score", EARLY_CASES)
def test_early_signals_are_visible_not_filtered_out(title, event_type, stage_keyword, score):
    """★ 关键：早期信号必须能**进入候选池**，而不是在预筛阶段就被丢掉。"""
    assert classifier.passes_prefilter(title), f"{title} 被预筛丢弃了"


@pytest.mark.parametrize("title,event_type,stage_keyword,score", EARLY_CASES)
def test_early_signals_satisfy_c1_condition(title, event_type, stage_keyword, score):
    """早期信号必须能命中 C1（否则 coverage 上不去，机会卡根本不会生成）。"""
    from app.models.enums import ReliabilityLevel
    from app.strategies.restructuring.rules import _condition_c1

    facts = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=1, event_type=classifier.classify_announcement(title),
                          title=title, evidence_level=ReliabilityLevel.A),),
    )
    condition = _condition_c1(facts)
    if event_type is EventType.M_AND_A:
        # M&A（非重大收购）刻意不进 C1 —— 那属于 ma_integration 策略
        assert condition.satisfaction == 0.0
    else:
        assert condition.satisfaction > 0, f"{title} 未命中 C1"


def test_early_stage_is_documented_in_registry():
    """阶梯必须在注册表里看得见（设计全量、实现逐个，D13）。"""
    definition = get_def(ThesisType.RESTRUCTURING)
    early_stages = [s for s in definition.catalyst_ladder if s.early]
    assert len(early_stages) >= 3, "至少要有筹划/申请/受理三档早期"
    assert all("早期" in s.stage for s in early_stages)
    # 阶梯分数单调递增
    scores = [s.score for s in definition.catalyst_ladder]
    assert scores == sorted(scores)
    # 每档都要有说明（人工核对时要能理解）
    assert all(s.description for s in definition.catalyst_ladder)


# --------------------------------------------------------------------------- #
# 存量信息：按用户核对判断「召回保留」，但不冒充新催化
# --------------------------------------------------------------------------- #
def test_lockup_release_is_recalled_but_marked_as_stock_not_early():
    """★ 抽样核对时用户判定：限售股解禁**仍然是重组事件**（召回优先）。

    但它属于「存量｜重组已完成」——**不是早期**，也不是新催化。
    这样既保留了召回（用户要求），又不会让它排到前面冒充新机会（质量要求）。
    """
    title = "关于重大资产重组部分限售股份上市流通的提示性公告"
    got_type, stage, early = ladder_of(title)
    assert got_type is EventType.RESTRUCTURING, "召回保留"
    assert "存量" in stage.stage
    assert stage.score == 5.0, "存量信息催化强度接近零"
    assert early is False, "存量 ≠ 早期：它是已完成的事"


def test_post_deal_detector():
    assert classifier.is_post_deal("关于重大资产重组部分限售股份上市流通的提示性公告")
    assert classifier.is_post_deal("关于重大资产重组限售股解除限售的公告")
    assert not classifier.is_post_deal("关于重大资产重组进展的公告")
    # 现在**不再排除**任何公告（召回优先）
    assert classifier.is_non_restructuring("关于重大资产重组部分限售股份上市流通的提示性公告") is False


# --------------------------------------------------------------------------- #
# 后期阶段：不能被误标为早期
# --------------------------------------------------------------------------- #
LATE_CASES = [
    ("关于重大资产重组预案的公告", 40.0, "预案"),
    ("关于重大资产重组报告书（草案）的公告", 60.0, "草案"),
    ("关于重大资产重组获得湖南省国资委批复的公告", 72.0, "批复"),
    ("关于重大资产重组获得中国证监会核准的公告", 95.0, "核准"),
]


@pytest.mark.parametrize("title,floor,keyword", LATE_CASES)
def test_late_stages_are_not_early(title, floor, keyword):
    _, stage, early = ladder_of(title)
    assert stage.score >= floor, f"{title} → {stage.score}"
    assert keyword in stage.stage
    assert early is False, f"{title} 不该被标为早期"


def test_unknown_stage_is_conservative():
    """无法识别阶段时按偏低处理 —— 不能因为「不知道进展」就默认它推进得很深。"""
    _, stage, early = ladder_of("关于重大资产重组进展的公告")
    assert stage.score <= 30.0
    assert early is True
    assert "未知" in stage.stage


def test_terminated_deal_scores_zero_and_is_not_early():
    _, stage, early = ladder_of("关于终止重大资产重组的公告")
    assert stage.score == 0.0
    assert early is False


def test_ladder_is_monotonic_with_early_band_below_progress_band():
    """早期档不得高于进展档 —— 否则「苗头」会被排到「已推进」前面。"""
    title_score = {
        "关于债权人申请对公司进行重整的公告": 15.0,
        "关于法院裁定受理公司重整申请的公告": 28.0,
        "关于重大资产重组预案的公告": 40.0,
    }
    scores = []
    for title in title_score:
        _, stage, _ = ladder_of(title)
        scores.append(stage.score)
    assert scores == sorted(scores)


# --------------------------------------------------------------------------- #
# 机会落库：阶段必须写进 Opportunity（否则卡片无法标注）
# --------------------------------------------------------------------------- #
def test_opportunity_persists_catalyst_stage(pipeline_outcome, engine):
    from sqlmodel import Session, select

    from app.models.opportunity import Opportunity

    with Session(engine) as s:
        opportunities = s.exec(select(Opportunity)).all()
        assert opportunities
        for opportunity in opportunities:
            assert opportunity.catalyst_stage, "阶段必须落库并展示（§24 / §38）"
            assert isinstance(opportunity.is_early_signal, bool)


def test_profile_can_toggle_early_signals():
    """不同用户看不同东西（§58 原则 6）：不想看低确定性信号时可以关掉。"""
    from app.models.profile import InvestmentProfile

    profile = InvestmentProfile(investor_id=1, name="只看确定性")
    assert profile.accept_early_signals is True, "默认开启（宁可见到并标注，也不要静默漏掉）"
    profile.accept_early_signals = False
    assert profile.accept_early_signals is False
