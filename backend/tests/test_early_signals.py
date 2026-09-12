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


# --------------------------------------------------------------------------- #
# 早期苗头的失效检测（本次修复的核心：比漏掉更糟的是挂着不放）
# --------------------------------------------------------------------------- #
TERMINAL_CASES = [
    ("关于法院裁定不予受理公司重整申请的公告", "法院不予受理"),
    ("关于债权人撤回对公司重整申请的公告", "撤回"),
    ("关于法院宣告公司破产的公告", "宣告破产"),
    ("关于终止重整程序的公告", "终止重整"),
    ("关于重大资产重组终止的公告", "重组终止"),
]

SEVERE_CASES = [
    ("关于重整计划未获法院批准的公告", "未获批准"),
    ("关于重整投资人终止投资协议的公告", "投资人退出"),
]

NOT_INVALIDATING = [
    # 早期苗头本身不是失效事件 —— 它只是确定性低
    "关于债权人申请对公司进行重整的公告",
    "关于法院裁定受理公司重整申请的公告",
    # 这些看起来含关键词，实际都是「重整推进中/已成功」
    "关于破产重整计划获得法院批准的公告",
    "关于法院宣告破产重整计划执行完毕的公告",
]


def test_failed_reorganization_paths_are_terminal():
    """★ 修复前这些**全部漏掉**：失效规则完全没有覆盖 BANKRUPTCY_REORGANIZATION。

    后果是：一个重整苗头死掉了，系统会永远把它挂在雷达上标着「待确认」——
    这比漏掉它更糟（漏掉是不作为，挂着不放是主动误导）。
    """
    from app.strategies.restructuring import invalidation

    for title, _hint in TERMINAL_CASES:
        _, stage, _ = ladder_of(title)
        facts = StrategyFacts(
            company=CompanyFacts(id=1),
            events=(EventFact(id=1, event_type=classifier.classify_announcement(title),
                              title=title),),
        )
        hits = STRATEGY.invalidation_hits(facts)
        assert hits, f"未命中失效：{title}"
        assert hits[0].severity == "terminal", f"{title} → {hits[0].severity}"
        assert invalidation.should_invalidate(hits), f"{title} 应触发状态迁移"
        assert stage.score == 0.0, "失效路径的催化强度必须归零"


def test_partial_failures_are_severe_not_terminal():
    for title, _hint in SEVERE_CASES:
        facts = StrategyFacts(
            company=CompanyFacts(id=1),
            events=(EventFact(id=1, event_type=classifier.classify_announcement(title),
                              title=title),),
        )
        hits = STRATEGY.invalidation_hits(facts)
        assert hits and hits[0].severity == "severe", f"{title} → {hits}"


def test_mid_flight_and_successful_events_are_not_invalidating():
    """★ 反向验证：推进中的、以及重整**成功**的公告都不得被判失效。

    「关于法院宣告破产重整计划执行完毕的公告」是重整成功，
    纯关键词 AND 匹配（宣告 + 破产）会误判 —— 必须靠排除词挡住。
    """
    for title in NOT_INVALIDATING:
        facts = StrategyFacts(
            company=CompanyFacts(id=1),
            events=(EventFact(id=1, event_type=classifier.classify_announcement(title),
                              title=title),),
        )
        assert STRATEGY.invalidation_hits(facts) == (), f"被误判为失效：{title}"


def test_early_stage_regulatory_attention_is_a_warning_not_a_kill():
    """★ 「早期的失效要更敏感」的正确做法：**提醒，但不判死**。

    早期待确认阶段收到监管问询往往是终止前兆，值得提醒；
    但对已经披露草案的机会，问询函是常规流程，报警就是噪声。
    """
    from app.strategies.restructuring import invalidation

    early_title = "关于收到交易所对公司重整事项问询函的公告"
    early = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=1, event_type=classifier.classify_announcement(early_title),
                          title=early_title),),
    )
    early_hits = STRATEGY.invalidation_hits(early)
    assert invalidation.warning_hits(early_hits), "早期苗头收到问询应产生预警"
    assert not invalidation.should_invalidate(early_hits), "预警不得改变状态"

    # 已推进：同样内容不再报警
    progressed = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(
            EventFact(id=1, event_type=classifier.classify_announcement(early_title),
                      title=early_title),
            EventFact(id=2, event_type=classifier.classify_announcement(
                "关于重大资产重组报告书（草案）的公告"), title="关于重大资产重组报告书（草案）的公告"),
        ),
    )
    assert invalidation.has_progress_evidence(progressed)
    assert invalidation.warning_hits(STRATEGY.invalidation_hits(progressed)) == (), (
        "已进入草案阶段，问询函是常规流程，不应报警"
    )


def test_event_level_flag_does_not_bake_in_conditional_rules():
    """事件层的 is_invalidating 是粗粒度展示标记，不得烤进有条件规则。

    ``early_only`` 的预警依赖机会所处阶段，在事件层无从判断 ——
    踩过的坑：问询函因此被标成失效事件，稀释了「失效事件」的含义。
    """
    from app.pipeline.event_writer import _mark_invalidating

    assert _mark_invalidating(
        classifier.classify_announcement("关于重大资产重组终止的公告"),
        "关于重大资产重组终止的公告",
    ) is True
    # 问询函只触发有条件预警 → 事件层不得标记
    assert _mark_invalidating(
        classifier.classify_announcement("关于收到交易所对重大资产重组事项问询函的公告"),
        "关于收到交易所对重大资产重组事项问询函的公告",
    ) is False


# --------------------------------------------------------------------------- #
# 公告主体：「重整」真实抽样暴露的最大一类假阳性（47%）
# --------------------------------------------------------------------------- #
SUBJECT_CASES = [
    # (标题, 是否第三方主体, 原因)
    ("关于法院裁定受理全资子公司破产重整的公告", True, "子公司重整"),
    ("关于孙公司破产重整事项的进展公告", True, "孙公司重整"),
    ("关于公司原控股股东破产重整进展的公告", True, "前控股股东重整"),
    ("三安光电股份有限公司关于控股股东债权人撤回破产重整申请的公告", True,
     "控股股东自己在破产重整"),
    ("关于实际控制人破产重整的进展公告", True, "实控人自己在破产重整"),
    ("关于原相对控股子公司重整事项进展暨完成股权变更的公告", True, "子公司"),
    # 含「公司及」→ 本公司也在主体内，不算错位
    ("关于法院决定对公司及全资子公司启动预重整的公告", False, "含本公司"),
    ("关于公司及全资子公司预重整债权申报的公告", False, "含本公司"),
    # 本公司自身
    ("西藏发展股份有限公司重整计划（草案）", False, "本公司"),
    ("龙元建设关于法院决定对公司进行预重整的公告", False, "本公司"),
    # ★ 控股股东要按谓语区分
    ("关于控股股东筹划重大事项停牌的公告", False, "现控股股东筹划，通常涉及上市公司"),
    ("关于控股股东拟协议转让公司股份的公告", False, "涉及上市公司股份"),
]


@pytest.mark.parametrize("title,third_party,why", SUBJECT_CASES)
def test_subject_is_third_party(title, third_party, why):
    """★ 「重整」真实抽样 15 条里 7 条（47%）的主体不是上市公司本身。

    子公司的重整不是母公司的重组预期 —— 不区分会持续制造幻影机会。
    """
    assert classifier.subject_is_third_party(title) is third_party, f"{title}（{why}）"


def test_third_party_subject_does_not_build_a_thesis():
    """主体错位的事件不得进入 C1，也不得给母公司定催化阶段。"""
    from app.models.enums import ReliabilityLevel
    from app.strategies.restructuring.rules import _condition_c1

    def measure(title: str):
        facts = StrategyFacts(
            company=CompanyFacts(id=1),
            events=(EventFact(id=1, event_type=EventType.BANKRUPTCY_REORGANIZATION,
                              title=title, evidence_level=ReliabilityLevel.A),),
        )
        return _condition_c1(facts).satisfaction, STRATEGY.catalyst_strength(facts).score

    third_sat, third_stage = measure("关于法院裁定受理全资子公司破产重整的公告")
    assert third_sat == 0.0, "子公司重整不得让母公司命中 C1"
    assert third_stage == 0.0, "子公司重整不得给母公司定阶段"

    own_sat, own_stage = measure("关于法院裁定受理公司重整申请的公告")
    assert own_sat > 0, "本公司重整必须命中 C1"
    assert own_stage > 0

    mixed_sat, mixed_stage = measure("关于法院决定对公司及全资子公司启动预重整的公告")
    assert mixed_sat > 0, "「公司及子公司」含本公司，必须命中"
    assert mixed_stage > 0


# --------------------------------------------------------------------------- #
# 跨类型失效覆盖：LLM 选任一类型都能命中
# --------------------------------------------------------------------------- #
BANKRUPTCY_FAILURES = [
    ("关于法院裁定不予受理公司重整申请的公告", "terminal"),
    ("关于终止重整程序的公告", "terminal"),
    ("关于法院宣告公司破产的公告", "terminal"),
    ("关于重整计划未获法院批准的公告", "severe"),
]


@pytest.mark.parametrize("title,severity", BANKRUPTCY_FAILURES)
def test_bankruptcy_failures_detected_whichever_type_llm_picks(title, severity):
    """★ RESTRUCTURING 与 BANKRUPTCY_REORGANIZATION 语义重叠，实测 LLM 会把
    「重整计划（草案）」判成 RESTRUCTURING（9/15 条）。

    失效规则按 event_type 精确匹配 —— 若不跨类型覆盖，
    一条「法院不予受理重整申请」被 LLM 判成 RESTRUCTURING 就会**漏掉失效**，
    等于失效检测被枚举选择绕过。
    """
    for event_type in (EventType.BANKRUPTCY_REORGANIZATION, EventType.RESTRUCTURING):
        facts = StrategyFacts(
            company=CompanyFacts(id=1),
            events=(EventFact(id=1, event_type=event_type, title=title),),
        )
        hits = STRATEGY.invalidation_hits(facts)
        assert hits, f"{title} 作为 {event_type.value} 时漏掉失效"
        assert hits[0].severity == severity, f"{title} / {event_type.value} → {hits[0].severity}"
