"""Pipeline 端到端回归（docs/07 §P6）。

用 **mock 源**（确定性、不联网、不调用 LLM）跑完整的
「采集 → 事件抽取 → 证据闸门 → Thesis → 评分 → 机会卡 → 状态 → 提醒」，
把 docs/03 §3 漏斗的 Stage 2 ~ Stage 4 全部覆盖。

三家公司刻意覆盖三种结局::

    ST XXX (600xxx)  重组预案 + 置入资产 + 问询函  → 待确认（有卡片）
    公司 J (000jjj)  非 ST 业绩改善                → 门槛挡住（不产卡片）
    ST YYY (000yyy)  重组终止                      → 逻辑失效 + 提醒
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, func, select

from app.engine import guard
from app.models.enums import EventType, OpportunityStatus, ScoreDimension, ThesisType
from app.models.evidence import Evidence
from app.models.events import Event
from app.models.knowledge import Announcement, Company, Paragraph, Stock
from app.models.opportunity import Alert, OpenQuestion, Opportunity, OpportunityScore, ScoreItem
from app.models.thesis import Thesis
from app.pipeline.runner import PipelineOptions, run_pipeline


# --------------------------------------------------------------------------- #
# 全链路产出
# --------------------------------------------------------------------------- #
def test_pipeline_writes_the_whole_chain(pipeline_outcome, engine):
    with Session(engine) as s:
        counts = {
            "companies": len(s.exec(select(Company)).all()),
            "announcements": len(s.exec(select(Announcement)).all()),
            "paragraphs": len(s.exec(select(Paragraph)).all()),
            "evidence": len(s.exec(select(Evidence)).all()),
            "events": len(s.exec(select(Event)).all()),
            "theses": len(s.exec(select(Thesis)).all()),
            "opportunities": len(s.exec(select(Opportunity)).all()),
            "score_items": len(s.exec(select(ScoreItem)).all()),
            "open_questions": len(s.exec(select(OpenQuestion)).all()),
            "alerts": len(s.exec(select(Alert)).all()),
        }
    assert counts["companies"] == 4
    assert counts["announcements"] == 8
    assert counts["paragraphs"] > 10, "全文必须切分成段落（证据定位的前提）"
    assert counts["events"] == 8, "每条通过预筛的公告都应落库为事件"
    assert counts["theses"] == 3
    assert counts["opportunities"] == 3
    assert counts["score_items"] > 20, "每个机会都必须有逐项拆解"
    # 提醒至少包含「逻辑失效」那一条；规则分/语义分分歧大时还会额外产生分歧提醒
    assert counts["alerts"] >= 1, "失效必须产生提醒"


def test_funnel_reports_where_it_dropped(pipeline_outcome):
    report = pipeline_outcome.report
    funnel = report.funnel
    assert funnel.candidates == 4
    assert funnel.announcements_fetched == 8
    assert funnel.events_extracted == 8
    # ★ 不再写死 3：默认画像「全景均衡」覆盖 10 类策略后，
    #   同一批 mock 公司会命中更多策略。断言改为「与实际结果一致」——
    #   写死数字的测试在画像/策略变化时必然失效，而它要守的是
    #   「漏斗如实反映落库的卡片数」。
    created_count = sum(1 for r in pipeline_outcome.opportunities if r.created)
    assert funnel.cards == created_count

    # ★ 「提示指向掉得最狠的一级」——验证**机制**，不写死是哪一级。
    #
    # 原先这里写死 ``"events_extracted → thesis_candidates"``。
    # 实现全部 10 类策略后，4 家公司都至少命中一个策略，
    # 瓶颈从「策略命中」下移到「建卡门槛」，写死的字符串就失效了 ——
    # 但它真正要守的（提示指向最窄的那一级）没变。
    from app.engine.funnel import FunnelCounters

    chain = FunnelCounters.SEQUENTIAL_CHAIN
    ratios = [
        (f"{a} → {b}", getattr(funnel, b) / getattr(funnel, a))
        for a, b in zip(chain, chain[1:])
        if getattr(funnel, a) > 0
    ]
    worst_level = min(ratios, key=lambda kv: kv[1])[0]
    assert funnel.drop_at().startswith(worst_level), (
        f"drop_at 指向 {funnel.drop_at()}，但保留率最低的是 {worst_level}"
    )


# --------------------------------------------------------------------------- #
# 结果分布
# --------------------------------------------------------------------------- #
def _by_thesis_status(outcome):
    return {r.thesis_type: r for r in outcome.opportunities}


def test_four_companies_get_three_verdicts(pipeline_outcome):
    """四家公司 → 三个结局：健康（待确认）/ 早期苗头（待确认）/ 失效，另加一家被门槛拒绝。

    ★ 实现第 2 个策略（turnaround）后，结果从「每家公司 1 条」变成
    「每个 (公司, 策略) 1 条」。所以这里先按策略分组再断言 ——
    直接数总数的话，断言会因为「多了一个策略」而失效，
    而它真正要守的（重组策略上的三种结局）其实没变。
    """
    restructuring = [
        r for r in pipeline_outcome.opportunities if r.thesis_type == "restructuring"
    ]
    created = [r for r in restructuring if r.created]
    rejected = [r for r in restructuring if not r.created]

    assert len(created) == 3
    assert len(rejected) == 1
    assert "逻辑强度不足" in rejected[0].reason
    assert rejected[0].coverage == pytest.approx(0.11)


def test_pipeline_evaluates_every_implemented_strategy(pipeline_outcome):
    """★ 每个已实现的策略都必须被遍历到。

    为什么值得单独一条：实现 turnaround 时实测踩到 ——
    实现类能跑，但 registry 里的 status 还写着 ``DESIGNED``，
    于是 ``implemented_types()`` 不遍历它，机会生成**静默跳过**。
    没有报错、没有空卡，只是它永远不出现在雷达上。
    """
    from app.strategies import implemented_types

    expected = {code.value for code in implemented_types()}
    evaluated = {r.thesis_type for r in pipeline_outcome.opportunities}
    assert expected <= evaluated, f"这些策略没有被遍历：{expected - evaluated}"
    assert len(expected) == 10, f"应有 10 类策略参与遍历，实际 {len(expected)}"


def test_healthy_case_is_pending_confirmation(pipeline_outcome):
    """ST XXX：证据齐备但有关键信息未确认 → 待确认，而不是「逻辑成立」。"""
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    assert healthy.status == OpportunityStatus.PENDING_CONFIRMATION.value
    # 可逐项复算（见 test_healthy_case_breakdown_is_reproducible）
    assert healthy.coverage == pytest.approx(0.69)
    assert healthy.match_score == pytest.approx(69.0)
    assert healthy.rule_score == pytest.approx(56.5625)
    assert healthy.risk_score == pytest.approx(29.25)


def test_non_st_company_does_not_produce_restructuring_card(pipeline_outcome, engine):
    """★ 规格 §5.6 示例 J 的反向验证：非 ST 公司不会被硬塞进重组策略。

    公司 J 有业绩改善信号，但**没有重组类事件** —— 因此 C1/C2/C3 全部不命中，
    coverage 只有 C4 的部分分。它应该被门槛挡住，而不是因为「看起来像机会」而入池。
    """
    rejected = next(r for r in pipeline_outcome.opportunities if not r.created)
    with Session(engine) as s:
        company = s.exec(
            select(Company).join(Stock, Stock.company_id == Company.id)
            .where(Stock.code == "000jjj")
        ).first()
        assert company is not None
        assert company.is_st is False
        # 它确实有事件（不因非 ST 而被丢弃）
        assert s.exec(select(Event).where(Event.company_id == company.id)).first() is not None
    assert rejected.coverage < 0.35


def test_terminated_case_is_invalidated_with_alert(pipeline_outcome, engine):
    invalidated = next(r for r in pipeline_outcome.opportunities if r.invalidated)
    assert invalidated.status == OpportunityStatus.INVALIDATED.value

    with Session(engine) as s:
        opportunity = s.get(Opportunity, invalidated.opportunity_id)
        assert opportunity is not None
        alert = s.exec(
            select(Alert).where(Alert.opportunity_id == opportunity.id)
        ).first()
        assert alert is not None
        assert alert.title == "投资逻辑发生重大变化"
        assert "终止" in alert.message or "失败" in alert.message
        # 反事实对比：若无失效事件应是多少 → 实际（规格 §22 要的是可对比的叙事）
        assert alert.score_before is not None and alert.score_after is not None
        assert alert.score_before > alert.score_after


def test_invalidation_actually_lowers_the_score(pipeline_outcome):
    """★ 失效必须体现在分数上，而不只是状态上。

    实测发现过一个真实缺陷：终止公告本身是 A 类公告，会照拿
    「存在 A 类公告直接对应核心事件类型 +40」，导致「逻辑已失效但分数几乎没掉」。
    修法是 R-GEN-EV-INVALID：失效事件不计正向催化，并主动减分。
    """
    invalidated = next(r for r in pipeline_outcome.opportunities if r.invalidated)
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    assert invalidated.rule_score < healthy.rule_score * 0.5


def test_invalidating_event_is_excluded_from_positive_catalyst(pipeline_outcome, engine):
    invalidated = next(r for r in pipeline_outcome.opportunities if r.invalidated)
    with Session(engine) as s:
        items = s.exec(
            select(ScoreItem).where(ScoreItem.opportunity_id == invalidated.opportunity_id)
        ).all()
    event_items = [i for i in items if i.dimension == ScoreDimension.EVENT_CATALYST]
    rule_ids = {i.rule_id for i in event_items}
    assert "R-GEN-EV-INVALID" in rule_ids, "必须有一条明确的失效扣分项"
    assert "R-GEN-EV-01" not in rule_ids, "失效事件不得享受「A 类公告」的正向催化加分"
    # 事件催化维度必须被压到 0
    with Session(engine) as s:
        dimension = s.exec(
            select(OpportunityScore).where(
                OpportunityScore.opportunity_id == invalidated.opportunity_id,
                OpportunityScore.dimension == ScoreDimension.EVENT_CATALYST,
            )
        ).first()
    assert dimension is not None and dimension.raw_value == 0.0


def test_healthy_case_breakdown_is_reproducible(pipeline_outcome, engine):
    """把这条 mock 场景的**逐维度值**锁死 —— 每一格都能手工复算。

    ```
    coverage = 0.35×1.00(C1) + 0.25×0.00(C2) + 0.20×1.00(C3) + 0.20×0.70(C4) = 0.69
    match    = 100 × 1.00 × 0.69 = 69
    事件催化  = +40(A类公告) +25(2 个事件类型) +10(可识别产业方) = 75
    催化剂强度 = 草案 + 评估 → 60
    确定性    = 100(A 基线) − 30(5 项待确认，触顶) − 15(未回复问询) = 55
    基本面    = 50(基线) + 20(现金流为正且改善) − 10(资产负债率上升) = 60
    股东结构  = 50(基线)
    市场关注  = 40(基线)
    风险     = 0.30×0.30 + 0.25×0.45 + 0.20×0.20 + 0.15×0.20 + 0.10×0.20 = 0.2925
               ★ 基本面风险被压到 0.20：亏损已归因于一次性因素（INV-F1）
    rule     = 20.70+18.75+6.00+5.50+3.00+5.00+2.00 − 4.3875 = 56.5625
    ```
    """
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        rows = s.exec(
            select(OpportunityScore).where(
                OpportunityScore.opportunity_id == healthy.opportunity_id
            )
        ).all()
    raw = {str(r.dimension.value if hasattr(r.dimension, "value") else r.dimension): r.raw_value
           for r in rows}

    assert raw["thesis_match"] == pytest.approx(69.0)
    assert raw["event_catalyst"] == pytest.approx(75.0)
    assert raw["catalyst_strength"] == pytest.approx(60.0)
    assert raw["certainty"] == pytest.approx(55.0)
    assert raw["fundamentals"] == pytest.approx(60.0)
    assert raw["shareholder_structure"] == pytest.approx(50.0)
    assert raw["market_attention"] == pytest.approx(40.0)
    assert raw["risk"] == pytest.approx(29.25)


def test_evidence_decay_is_deterministic_not_wallclock_dependent(pipeline_outcome, engine):
    """★ 守卫：mock 场景的新鲜度必须固定在「≤1 天」，衰减系数恒为 1.00。

    曾经踩过的坑：mock 用硬编码绝对日期，而衰减用真实时钟算，
    于是随着日历推进，``EVENT_CATALYST`` 会从 75 悄悄变成 63.75（×0.85）。
    这种「期望值随日历漂移」的测试比没有测试更糟 —— 所以要显式锁住。
    """
    from app.engine.freshness import decay_factor

    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        opportunity = s.get(Opportunity, healthy.opportunity_id)
    assert opportunity is not None

    # 分档边界（docs/04 §5）
    assert decay_factor(0.25) == pytest.approx(1.00)
    assert decay_factor(1.5) == pytest.approx(0.85)
    assert decay_factor(5.0) == pytest.approx(0.70)
    assert decay_factor(45.0) == pytest.approx(0.30)

    with Session(engine) as s:
        rows = s.exec(
            select(OpportunityScore).where(
                OpportunityScore.opportunity_id == opportunity.id
            )
        ).all()
    raw = {str(r.dimension.value if hasattr(r.dimension, "value") else r.dimension): r.raw_value
           for r in rows}
    # 未衰减时的原始值：事件催化 75、市场关注 40
    assert raw["event_catalyst"] == pytest.approx(75.0), "衰减系数不是 1.00（时间戳可能又变成绝对日期了）"
    assert raw["market_attention"] == pytest.approx(40.0)


def test_one_off_attribution_reduces_risk_not_fundamentals(pipeline_outcome, engine):
    """INV-F1 在真实数据上的验证：亏损已归因于一次性因素 → 风险降低，基本面照实计算。"""
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        rows = s.exec(
            select(OpportunityScore).where(
                OpportunityScore.opportunity_id == healthy.opportunity_id
            )
        ).all()
    raw = {str(r.dimension.value if hasattr(r.dimension, "value") else r.dimension): r.raw_value
           for r in rows}
    # 基本面仍反映真实的现金流改善与负债率上升（60 = 50+20−10），没有被「一次性因素」抹平
    assert raw["fundamentals"] == pytest.approx(60.0)
    # 风险却因为归因而降低（0.40 → 0.20）
    assert raw["risk"] == pytest.approx(29.25)


def test_early_signal_case_is_labeled_end_to_end(pipeline_outcome, engine):
    """★ 用户要求「有苗头的也要找，因为要提前布局」—— 端到端验证。

    ST ZZZ 的公告是「债权人申请重整」+「法院裁定受理重整」，
    催化强度落在早期档（28 分），因此机会分明显低于「进展」阶段的机会。
    低分是**刻意**的：它表示确定性低、离价值兑现远 ——
    但机会本身必须被找到，并且**必须标注为早期**。
    """
    early = next(r for r in pipeline_outcome.opportunities if r.created and r.coverage < 0.55)
    with Session(engine) as s:
        opportunity = s.get(Opportunity, early.opportunity_id)
        assert opportunity is not None
        assert opportunity.is_early_signal is True, "必须标为早期信号"
        assert "早期" in opportunity.catalyst_stage
        assert "法院受理" in opportunity.catalyst_stage or "重整申请" in opportunity.catalyst_stage
        # 处于「待确认」而不是「逻辑成立」
        assert str(opportunity.status).lower() in {"pending_confirmation", "pendingconfirmation"}

        company = s.get(Company, opportunity.company_id)
        assert company is not None and company.name == "ST ZZZ"

        # 早期信号必须有证据链支撑（苗头可以低确定性，但不能是空穴来风）
        assert opportunity.supporting_evidence_ids, "早期信号同样必须有证据"
        for evidence_id in opportunity.supporting_evidence_ids:
            evidence = s.get(Evidence, evidence_id)
            assert evidence is not None
            assert str(evidence.reliability_level).endswith("A"), "早期信号同样要求 A 类证据"

    # 早期信号的机会分必须低于「进展」阶段那张（确定性差异必须体现在分数上）
    progressing = next(
        r for r in pipeline_outcome.opportunities
        if r.created and not r.invalidated and r.match_score == 69.0
    )
    assert early.rule_score < progressing.rule_score


# --------------------------------------------------------------------------- #
# 证据链（规格 §15 / §16）
# --------------------------------------------------------------------------- #
def test_every_evidence_passes_the_gate_and_points_at_real_text(pipeline_outcome, engine):
    with Session(engine) as s:
        evidence = s.exec(select(Evidence)).all()
        assert evidence
        for item in evidence:
            assert item.reliability_level == "A" or item.reliability_level.value == "A"
            assert item.announcement_id is not None          # INV-E3
            paragraph = s.get(Paragraph, item.paragraph_id)
            assert paragraph is not None
            # INV-E1：relevant_text 必须是原文片段，不得改写
            assert guard.relevance_substring_match(item.relevant_text, paragraph.text)
            # 可定位
            assert item.page is not None and item.para_index is not None


def test_no_evidence_rejected_in_mock_run(pipeline_outcome):
    assert pipeline_outcome.report.quality.evidence_rejected == 0


def test_event_times_respect_inv01(pipeline_outcome, engine):
    with Session(engine) as s:
        for event in s.exec(select(Event)).all():
            assert event.event_time <= event.discovery_time          # INV-EV1
            # event_time 缺失时回退为发布时间，并且必须标注来源
            assert event.attributes.get("event_time_source") in {
                "disclosed", "publication_time"
            }


def test_events_marked_invalidating_are_the_termination_ones(pipeline_outcome, engine):
    """被标为失效的事件必须真的能否定某个策略的前提。

    注意：早期苗头（重整申请 / 法院受理）**不是**失效事件 ——
    它们只是确定性低，不代表逻辑已经被否定。
    """
    with Session(engine) as s:
        invalidating = s.exec(select(Event).where(Event.is_invalidating == True)).all()  # noqa: E712
    assert invalidating, "mock 场景里有一单重组终止，必须被标为失效"
    for event in invalidating:
        assert any(kw in event.title for kw in ("终止", "失败", "撤回", "不予受理", "驳回", "宣告")), (
            f"被标为失效的事件标题看不出否定含义：{event.title}"
        )
    # 早期苗头不得被误标为失效
    early_titles = [
        "关于债权人申请对公司进行重整的公告",
        "关于法院裁定受理公司重整申请的公告",
    ]
    for title in early_titles:
        assert not any(event.title == title for event in invalidating), (
            f"早期苗头被误标为失效：{title}"
        )


# --------------------------------------------------------------------------- #
# 待确认 / Why Now / 关注池理由
# --------------------------------------------------------------------------- #
def test_open_questions_are_concrete_and_two_sided(pipeline_outcome, engine):
    """规格 §24：待确认必须具体，并且要能看到「已确认」一侧。"""
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        questions = s.exec(
            select(OpenQuestion).where(OpenQuestion.opportunity_id == healthy.opportunity_id)
        ).all()
    open_ones = [q for q in questions if q.status == "open"]
    confirmed = [q for q in questions if q.status == "confirmed"]

    assert open_ones and confirmed, "必须同时有 ✓ 已确认 与 ? 待确认 两侧"
    # 问题必须具体，不能是「待确认」这种标签
    assert all(len(q.question) >= 2 for q in questions)
    assert {"交易标的", "交易价格", "监管审核结果"} <= {q.question for q in open_ones}
    # 已确认项必须绑定证据（可核对）
    assert any(q.confirmed_evidence_id is not None for q in confirmed)


def test_why_in_radar_reflects_hit_conditions(pipeline_outcome, engine):
    """★ 规格 §17：「为什么进入你的关注池」是命中的核心条件，不是 Why Now 四段。"""
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        opportunity = s.get(Opportunity, healthy.opportunity_id)
        thesis = s.get(Thesis, opportunity.thesis_id)
    assert opportunity is not None and thesis is not None

    assert opportunity.why_in_radar, "必须有「为什么进入关注池」"
    assert any("重大资产重组" in r for r in opportunity.why_in_radar)
    # 与 why_now 是两件事
    assert opportunity.why_now
    assert opportunity.why_in_radar != opportunity.why_now


def test_thesis_has_why_now_and_invalidation(pipeline_outcome, engine):
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        opportunity = s.get(Opportunity, healthy.opportunity_id)
        thesis = s.get(Thesis, opportunity.thesis_id)

    assert thesis is not None
    assert thesis.why_now_past and thesis.why_now_recent and thesis.why_now_this_week
    assert thesis.why_now_conclusion
    assert thesis.invalidating_event_types, "INV-TT1：失效条件不得为空"      # INV-TT1
    assert EventType.RESTRUCTURING in thesis.invalidating_event_types
    # 语句必须是「因为 X 逻辑，所以关注 Y」的形态（规格 §21）
    assert "重组预期" in thesis.statement
    assert thesis.supporting_evidence_ids, "必须有支撑证据"


def test_next_events_to_watch_is_derived_from_ladder(pipeline_outcome, engine):
    healthy = next(r for r in pipeline_outcome.opportunities if r.created and not r.invalidated)
    with Session(engine) as s:
        opportunity = s.get(Opportunity, healthy.opportunity_id)
    assert opportunity is not None
    watch = opportunity.next_events_to_watch
    assert watch, "规格 §53：Next Watch 是固定字段"
    # 当前已到「草案 + 评估」阶段 → 后续**规则阶梯**应出现（且不含已完成阶段）
    # 注意：AI 分析会追加 deal 特定的观察项（如「重整计划草案」），
    # 那里面可能含「草案」二字 —— 所以只对**规则部分**做「不重复」断言。
    from app.models.enums import ThesisType
    from app.strategies import get_def

    ladder_names = {s.stage for s in get_def(ThesisType.RESTRUCTURING).catalyst_ladder}
    # 规则侧产出三类：阶梯阶段、未回复问询的提醒、失效后的观察项
    rule_like = [
        w for w in watch
        if w in ladder_names or w == "交易所问询回复" or w.startswith("该逻辑已失效")
    ]
    ai_part = [w for w in watch if w not in rule_like]

    assert rule_like, "规则侧应贡献观察项"
    assert any("股东大会" in w for w in rule_like)
    assert "进展｜草案 + 评估" not in rule_like, "已完成的阶段不该再列"
    # ★ 顺序：规则项（确定性）在前，AI 补充项在后
    assert watch[: len(rule_like)] == rule_like, "规则项必须排在 AI 补充项之前"
    assert ai_part, "AI 分析应补充 deal 特定的观察项"
    # 存在未回复问询 → 必须把「交易所问询回复」放在最前
    assert watch[0] == "交易所问询回复"


# --------------------------------------------------------------------------- #
# 幂等（INV-NF-04）
# --------------------------------------------------------------------------- #
def test_rerunning_pipeline_is_idempotent(pipeline_outcome, engine):
    """同一批数据跑两次，行数不变。"""
    with Session(engine) as s:
        before = {
            "announcements": int(s.exec(select(func.count()).select_from(Announcement)).one()),
            "paragraphs": int(s.exec(select(func.count()).select_from(Paragraph)).one()),
            "events": int(s.exec(select(func.count()).select_from(Event)).one()),
            "evidence": int(s.exec(select(func.count()).select_from(Evidence)).one()),
            "opportunities": int(s.exec(select(func.count()).select_from(Opportunity)).one()),
            "alerts": int(s.exec(select(func.count()).select_from(Alert)).one()),
        }

    second = run_pipeline(PipelineOptions(source="mock", stage="incremental"))
    assert second.report.funnel.events_extracted == 0, "事件应被幂等去重"

    with Session(engine) as s:
        after = {
            "announcements": int(s.exec(select(func.count()).select_from(Announcement)).one()),
            "paragraphs": int(s.exec(select(func.count()).select_from(Paragraph)).one()),
            "events": int(s.exec(select(func.count()).select_from(Event)).one()),
            "evidence": int(s.exec(select(func.count()).select_from(Evidence)).one()),
            "opportunities": int(s.exec(select(func.count()).select_from(Opportunity)).one()),
            "alerts": int(s.exec(select(func.count()).select_from(Alert)).one()),
        }
    assert before == after, f"重跑后行数变化：{before} → {after}"
    # 机会会被更新（分数/状态），但不得新建
    assert after["opportunities"] == 3


def test_second_run_does_not_duplicate_alerts(pipeline_outcome, engine):
    with Session(engine) as s:
        before = int(s.exec(select(func.count()).select_from(Alert)).one())
    run_pipeline(PipelineOptions(source="mock", stage="incremental"))
    with Session(engine) as s:
        after = int(s.exec(select(func.count()).select_from(Alert)).one())
    assert after == before, f"重复运行不应重复发提醒：{before} → {after}"


# --------------------------------------------------------------------------- #
# 画像门槛（规格 §5.7 / §5.8）
# --------------------------------------------------------------------------- #
def test_match_score_scales_with_profile_weight(pipeline_outcome, engine):
    """调整画像权重 → 匹配度与机会分随之变化（不是所有用户看到同一个 Dashboard）。

    ★ 断言必须**按策略过滤**：把 restructuring 的权重清零，
    说明的是「这个画像不关注重组」，而不是「这家公司没有任何机会」——
    另外 9 类策略照样可以建卡（实测 ST XXX 在零重组权重下
    仍会被其它策略命中，所以对全部结果断言 «都不建卡» 是错的）。
    """
    from app.models.profile import ProfileThesisWeight
    from app.strategies import implemented_types
    from app.pipeline import profile_seed
    from app.pipeline.opportunity_builder import build_opportunities

    with Session(engine) as s:
        profile = profile_seed.get_or_create_default_profile(s, template=None)
        row = s.exec(
            select(ProfileThesisWeight).where(
                ProfileThesisWeight.profile_id == profile.id,
                ProfileThesisWeight.thesis_type == ThesisType.RESTRUCTURING,
            )
        ).first()
        assert row is not None
        row.weight = 0.0
        s.add(row)
        s.commit()
        weights = profile_seed.profile_weights(s, int(profile.id or 0))
        assert weights.get("restructuring", 0) == 0.0

        company = s.exec(select(Company).where(Company.name == "ST XXX")).first()
        results = build_opportunities(s, int(company.id), int(profile.id), weights, commit=True)

    restructuring = [r for r in results if r.thesis_type == "restructuring"]
    assert restructuring, "重组策略应当仍被评估（只是权重为 0）"
    assert all(not r.created for r in restructuring), (
        "画像与重组无关时不应产出重组机会卡"
    )
    assert all(
        "相关性不足" in (r.reason or "") or "逻辑强度不足" in (r.reason or "")
        for r in restructuring
    )
    # 其它策略不受这次权重调整的影响（各自按自己的权重判定）
    assert {r.thesis_type for r in results} == {
        c.value for c in implemented_types()
    }
def test_zero_weight_strategy_gets_zero_match(pipeline_outcome):
    """画像里没配置的策略 → 权重比 0 → 匹配度 0 → 被门槛挡住。"""
    from app.engine.scoring import compute_match_score, profile_weight_ratio

    ratio = profile_weight_ratio({"restructuring": 0.4, "value": 0.2}, "growth")
    assert ratio == 0.0
    assert compute_match_score(ratio, 1.0) == 0.0


# --------------------------------------------------------------------------- #
# 可观测
# --------------------------------------------------------------------------- #
def test_run_is_recorded_for_observability(pipeline_outcome, engine):
    from app.models.audit import IngestRun

    with Session(engine) as s:
        run = s.exec(select(IngestRun).order_by(IngestRun.started_at.desc())).first()  # type: ignore[attr-defined]
    assert run is not None
    assert run.funnel and run.funnel.get("cards") == 3
    assert run.quality is not None
    assert run.finished_at is not None


def test_quality_metrics_expose_r3_verdict(pipeline_outcome):
    """质量指标必须能直接回答「本机模型能否胜任」（即使 mock 源没有 LLM 调用）。"""
    quality = pipeline_outcome.report.quality
    assert quality.llm_schema_failure_rate == pytest.approx(0.0)
    assert "本地模型" in quality.extractor_verdict
    assert quality.hallucination_signal is None      # 无证据被拒


# --------------------------------------------------------------------------- #
# 并发抽取：与串行必须**结果完全一致**
# --------------------------------------------------------------------------- #
@pytest.fixture()
def parallel_outcome(session, engine):
    """同一场景，但抽取阶段用 2 个线程。

    ★ 与 ``pipeline_outcome`` 一样显式固定画像为「重组猎手」——
    这条测试比对的是「并发与串行的结果是否逐项一致」，
    不该因为默认画像覆盖了 10 类策略而改变卡片数量。
    """
    from app.pipeline import profile_seed
    from app.pipeline.runner import PipelineOptions, run_pipeline

    del session
    with Session(engine) as s:
        profile = profile_seed.get_or_create_default_profile(s, template=None)
        profile_seed.apply_template(s, profile, "重组猎手")
    return run_pipeline(PipelineOptions(
        source="mock", stage="full", limit=None, extract_workers=2
    ))


def test_parallel_extraction_matches_serial_numbers(parallel_outcome, engine):
    """★ 并发抽取必须与串行**逐项一致**。

    设计约束：**并发只包住 LLM 调用，落库仍在主线程串行执行** ——
    SQLite 多线程写不安全，缓存 IO 也已在 ``SqlNodeCache`` 内串行化。

    这里复用 ``test_pipeline_e2e`` 里已经锁定的规范数值（那些是在串行下产生的）：
    若并发改变了任何一项，说明有线程安全问题或顺序依赖。
    """
    report = parallel_outcome.report
    assert report.funnel.cards == 3
    assert report.funnel.events_extracted == 8

    with Session(engine) as s:
        assert len(s.exec(select(Event)).all()) == 8
        assert len(s.exec(select(Evidence)).all()) == 21
        assert len(s.exec(select(ScoreItem)).all()) == 52

        # 顺序也必须是确定的（按 id 升序对应于固定公告顺序）
        scores = [
            (round(o.rule_score or 0, 4), o.match_score, o.catalyst_stage, o.is_early_signal)
            for o in s.exec(select(Opportunity).order_by(Opportunity.id)).all()
        ]
    assert scores == [
        (56.5625, 69.0, "进展｜草案 + 评估", False),
        (37.2, 49.0, "早期｜法院受理 / 指定管理人", True),
        (21.05, 42.0, "终止 / 失败", False),
    ], scores


def test_sql_node_cache_is_thread_safe():
    """缓存读写必须串行化 —— 否则并发抽取会同时改同一个 Session。"""
    from app.ai.cache import SqlNodeCache

    assert hasattr(SqlNodeCache, "_io_lock"), "SqlNodeCache 必须内置 IO 锁"
    assert SqlNodeCache._io_lock is SqlNodeCache._io_lock, "必须是类级共享锁"


def test_extract_workers_defaults_to_one():
    """默认 1 线程 —— 行为必须与改动前完全一致（不能悄悄改变默认语义）。"""
    from app.pipeline.runner import PipelineOptions

    assert PipelineOptions().extract_workers == 1


# --------------------------------------------------------------------------- #
# ★★ 证据必须属于同一家公司（用户反馈的那个 bug 的守卫）
# --------------------------------------------------------------------------- #
def test_supporting_evidence_belongs_to_the_same_company(pipeline_outcome, engine):
    """★★ **这是本项目最严重的一类数据错误**，此前所有测试都没发现。

    用户反馈：「跳转原文别的公司是错的」。

    根因：``ConditionResult.evidence_ids`` 与 ``RuleHit.evidence_ids`` 里装的
    是 ``EventFact.id``（**事件 ID**），而不是**证据 ID**。
    ``Event`` 与 ``Evidence`` 是两张表、两套自增 ID，数值范围还重叠 ——
    于是每条证据都指到了**别的公司**的公告，逐家错位一格。

    为什么之前的测试抓不到：**没有任何测试校验「证据是否属于同一家公司」**。
    大家只验证了「有证据」「证据数量对」「子串校验通过」——
    而错位的证据照样能满足这些断言（子串校验是对**证据自己指向的段落**做的，
    自洽但张冠李戴）。

    ⇒ 所以这条测试是必要的：**跨实体引用必须校验归属**。
    """
    from app.models.knowledge import Announcement

    with Session(engine) as s:
        checked = 0
        for opportunity in s.exec(
            select(Opportunity).where(Opportunity.supporting_evidence_ids != None)  # noqa: E711
        ).all():
            company_id = opportunity.company_id
            for evidence_id in opportunity.supporting_evidence_ids or []:
                evidence = s.get(Evidence, evidence_id)
                assert evidence is not None, f"证据 {evidence_id} 不存在"

                announcement = s.get(Announcement, evidence.announcement_id)
                assert announcement is not None, (
                    f"机会 #{opportunity.id} 的证据 {evidence_id} 没有对应公告"
                )
                assert announcement.company_id == company_id, (
                    f"★ 机会 #{opportunity.id}（公司 {company_id}）的证据 {evidence_id} "
                    f"却属于公司 {announcement.company_id}"
                    f"（公告：{announcement.title[:30]}）—— 跨实体引用错位"
                )
                checked += 1
        assert checked > 0, "至少应校验到一条证据"


def test_score_items_reference_evidence_of_the_same_company(pipeline_outcome, engine):
    """评分项里的证据引用同样必须属于同一家公司（同一个 bug 的另一处出口）。"""
    from app.models.knowledge import Announcement

    with Session(engine) as s:
        checked = 0
        for item in s.exec(select(ScoreItem)).all():
            if not item.evidence_ids:
                continue
            opportunity = s.get(Opportunity, item.opportunity_id)
            assert opportunity is not None
            for evidence_id in item.evidence_ids:
                evidence = s.get(Evidence, evidence_id)
                assert evidence is not None, (
                    f"评分项 {item.rule_id} 引用了不存在的证据 {evidence_id}"
                )
                announcement = s.get(Announcement, evidence.announcement_id)
                assert announcement is not None
                assert announcement.company_id == opportunity.company_id, (
                    f"★ 评分项 {item.rule_id}（规则 {item.rule_id}）的证据 {evidence_id} "
                    f"属于公司 {announcement.company_id}，而机会属于公司 "
                    f"{opportunity.company_id}"
                )
                checked += 1
        assert checked > 0


def test_condition_evidence_ids_are_evidence_not_events(pipeline_outcome, engine):
    """条件里的 ``evidence_ids`` 必须是**证据 ID**，不能是事件 ID。

    两者数值范围重叠，所以「查得到」不能证明是对的 ——
    必须真的把每个 id 拿去 ``Evidence`` 表查，而不是 ``Event`` 表。
    """
    from app.strategies.restructuring.rules import STRATEGY
    from app.pipeline import facts_builder

    with Session(engine) as s:
        company = s.exec(select(Company).where(Company.name == "ST XXX")).first()
        assert company is not None
        facts = facts_builder.build_strategy_facts(s, int(company.id))
        evaluation = STRATEGY.evaluate(facts)

        referenced: list[int] = []
        for condition in evaluation.hits:
            referenced.extend(condition.evidence_ids)
        assert referenced, "命中的条件必须带证据引用"

        for evidence_id in referenced:
            assert s.get(Evidence, evidence_id) is not None, (
                f"条件引用的 {evidence_id} 在 Evidence 表里不存在 —— "
                "很可能又混进了 Event ID"
            )
            evidence = s.get(Evidence, evidence_id)
            announcement = s.get(Announcement, evidence.announcement_id)
            assert announcement.company_id == int(company.id)


# --------------------------------------------------------------------------- #
# 漏斗的每一级都必须真的被计数
# --------------------------------------------------------------------------- #
def test_funnel_deep_analyzed_is_actually_counted(pipeline_outcome):
    """★ ``deep_analyzed`` 原先**从未被 +1**。

    分析在 ``build_opportunities`` 内部跑，runner 看不到跑了几个 ——
    于是报告里恒为 0，而缓存库里明明有 14 次 ``analyze``。
    「报告说 0 次深度分析、实际跑了 14 次」会让人以为分析阶段没接上，
    从而去修一个并不存在的问题。

    这条测试守的是「漏斗的每一级都要有真实的写入点」——
    ``FunnelCounters`` 有字段不等于有计数。
    """
    from app.engine.funnel import FunnelCounters

    funnel = pipeline_outcome.report.funnel
    analysis_stats = pipeline_outcome.analysis_stats

    # 上游：有多少机会进入了 AI 分析
    analyzed_expected = sum(
        1 for r in pipeline_outcome.opportunities if getattr(r, "analyzed", False)
    )
    assert funnel.deep_analyzed == analyzed_expected, (
        f"deep_analyzed={funnel.deep_analyzed} 与实际分析数 {analyzed_expected} 不一致"
    )

    # 若确实跑了分析，计数必须非零
    if analysis_stats.get("calls") or analysis_stats.get("ok"):
        assert funnel.deep_analyzed > 0, (
            f"分析层确实跑了（{analysis_stats}）但漏斗计数为 0"
        )

    # 全部漏斗级别都必须出现在主链里（防止有字段没被使用）
    for stage in FunnelCounters.SEQUENTIAL_CHAIN:
        assert hasattr(funnel, stage)
