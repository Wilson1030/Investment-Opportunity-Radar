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
    assert counts["alerts"] == 1, "失效必须产生提醒"


def test_funnel_reports_where_it_dropped(pipeline_outcome):
    report = pipeline_outcome.report
    funnel = report.funnel
    assert funnel.candidates == 4
    assert funnel.announcements_fetched == 8
    assert funnel.events_extracted == 8
    assert funnel.cards == 3
    # 5 事件 → 2 机会：提示应指向这一级，而不是无关的分支计数
    assert funnel.drop_at() == "events_extracted → thesis_candidates"


# --------------------------------------------------------------------------- #
# 结果分布
# --------------------------------------------------------------------------- #
def _by_thesis_status(outcome):
    return {r.thesis_type: r for r in outcome.opportunities}


def test_four_companies_get_three_verdicts(pipeline_outcome):
    """四家公司 → 三个结局：健康（待确认）/ 早期苗头（待确认）/ 失效，另加一家被门槛拒绝。"""
    created = [r for r in pipeline_outcome.opportunities if r.created]
    rejected = [r for r in pipeline_outcome.opportunities if not r.created]

    assert len(created) == 3
    assert len(rejected) == 1
    assert "逻辑强度不足" in rejected[0].reason
    assert rejected[0].coverage == pytest.approx(0.11)


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
    # 当前已到「草案 + 评估」阶段 → 后续阶段（股东大会 / 监管核准）应出现
    assert any("股东大会" in w for w in watch)
    assert not any("草案" in w for w in watch), "已完成的阶段不该再列"
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
    run_pipeline(PipelineOptions(source="mock", stage="incremental"))
    with Session(engine) as s:
        alerts = int(s.exec(select(func.count()).select_from(Alert)).one())
    assert alerts == 1, "重复运行不应重复发提醒"


# --------------------------------------------------------------------------- #
# 画像门槛（规格 §5.7 / §5.8）
# --------------------------------------------------------------------------- #
def test_match_score_scales_with_profile_weight(pipeline_outcome, engine):
    """调整画像权重 → 匹配度与机会分随之变化（不是所有用户看到同一个 Dashboard）。"""
    from app.pipeline import profile_seed

    from app.models.profile import ProfileThesisWeight

    with Session(engine) as s:
        profile = profile_seed.get_or_create_default_profile(s, template=None)
        # 显式把重组权重清零（套用模板是「合并」语义，不会移除既有项）
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

        from app.pipeline.opportunity_builder import build_opportunities

        company = s.exec(select(Company).where(Company.name == "ST XXX")).first()
        results = build_opportunities(s, int(company.id), int(profile.id), weights, commit=True)

    assert all(not r.created for r in results), "画像与重组无关时不应产出重组机会卡"
    assert all("相关性不足" in r.reason or "逻辑强度不足" in r.reason for r in results)


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
