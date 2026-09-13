"""组装机会：策略命中 → Thesis → 评分 → Opportunity + 待确认 + 状态 + 提醒。

**这是「同一家公司可以对应多个 Thesis」的实现**（规格 §5.7）：
对每一类**已实现**的策略分别评估，通过门槛的各自生成一条 Opportunity，
唯一键 ``(company_id, profile_id, thesis_id)`` 保证幂等。

门槛（可调）::

    coverage  ≥ 0.35   逻辑强度：核心条件至少要命中三分之一以上
    match     ≥ 30     相关性：与用户画像无关的策略不产出卡片

两条都不能省：只看 coverage 会把「逻辑很强但用户不关心」的机会塞进来；
只看 match 会让「用户关心但逻辑很弱」的噪声进来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlmodel import Session, delete, select

from app.engine import guard
from app.engine.rules import severity_label
from app.engine.scoring import (
    compute_divergence,
    compute_rule_score,
    is_divergence_flagged,
    profile_weight_ratio,
)
from app.facts import StrategyFacts
from app.models.enums import (
    EventType,
    OpportunityStatus,
    ScoreDimension,
    ScoreSource,
    ThesisType,
)
from app.models.opportunity import (
    Alert,
    OpenQuestion,
    Opportunity,
    OpportunityScore,
    OpportunityStatusLog,
    ScoreItem,
)
from app.models.thesis import Thesis
from app.strategies.restructuring.invalidation import is_completion_driven_delisting
from app.pipeline import facts_builder
from app.pipeline.analysis import analyze_opportunity
from app.pipeline.event_writer import facts_after_write
from app.strategies import STRATEGIES, get_def, get_strategy, implemented_types
from app.strategies.base import NotImplementedStrategy, StrategyEvaluation

if TYPE_CHECKING:  # pragma: no cover
    from app.ai.runner import NodeRunner

#: 逻辑强度门槛
MIN_COVERAGE = 0.35
#: 与用户画像的相关性门槛
MIN_MATCH = 30.0

#: 需要连续多少次「失效条件已不成立」才把卡片从失效纠正回来（防抖动）
RECOVERY_STREAK_REQUIRED = 2
#: 计入「风险」列表的最低严重度
RISK_LIST_THRESHOLD = 0.40


@dataclass
class OpportunityBuildResult:
    thesis_type: str
    created: bool
    opportunity_id: int | None = None
    coverage: float = 0.0
    match_score: float | None = None
    rule_score: float | None = None
    risk_score: float | None = None
    status: str | None = None
    reason: str = ""
    invalidated: bool = False
    #: ★ 是否真的跑了 AI 分析（``hunt_risk`` → ``analyze`` → ``score_semantic``）。
    #:
    #: 为什么需要它：分析在 ``_build_one`` 内部跑，runner 看不到跑了几个 ——
    #: 于是漏斗的 ``deep_analyzed`` **从来没有被计数过**（实测报告里恒为 0，
    #: 而缓存库里明明有 14 次 analyze）。报告说「0 次深度分析」而实际跑了 14 次，
    #: 会让人以为分析阶段没接上。
    analyzed: bool = False


# --------------------------------------------------------------------------- #
def build_opportunities(
    session: Session,
    company_id: int,
    profile_id: int,
    profile_weights: dict[str, float],
    *,
    dry_run: bool = False,
    commit: bool = True,
    analysis_runner: "NodeRunner | None" = None,
) -> tuple[OpportunityBuildResult, ...]:
    """为一家公司生成/更新全部符合门槛的机会。

    ``analysis_runner`` 非空时，对入池的机会跑 AI 分析链路
    （``hunt_risk`` → ``analyze`` → ``score_semantic``）—— 见
    :mod:`app.pipeline.analysis`。为 ``None`` 时只出规则分（用于测试与降级）。
    """
    facts = facts_builder.build_strategy_facts(session, company_id)
    accept_early_signals = _accept_early_signals(session, profile_id)
    results: list[OpportunityBuildResult] = []

    for code in implemented_types():
        strategy = get_strategy(code)
        if isinstance(strategy, NotImplementedStrategy):  # pragma: no cover - 防御
            continue

        evaluation: StrategyEvaluation = strategy.evaluate(facts)
        ratio = profile_weight_ratio(profile_weights, code.value)

        open_questions = strategy.open_questions(facts)
        facts_with_questions = facts_builder.with_open_question_count(facts, len(open_questions))

        # ★ 早期苗头是否纳入，由画像决定（用户要「提前布局」时开启）
        if not accept_early_signals and bool(
            getattr(strategy, "is_early_signal", lambda _f: False)(facts_with_questions)
        ):
            stage = strategy.catalyst_strength(facts_with_questions)  # type: ignore[attr-defined]
            results.append(OpportunityBuildResult(
                thesis_type=code.value, created=False, coverage=evaluation.coverage,
                reason=f"当前处于「{stage.stage}」，画像未开启早期信号（accept_early_signals=false）",
            ))
            continue

        # ★★ 门槛可以跳过「更新」，但**不能跳过「失效」**。
        #
        # 为什么：``_build_one`` 是唯一做失效判定与状态迁移的地方。
        # 若门槛把「逻辑已失效」的已有卡片也挡在外面，它就永远不会被迁移到
        # ``invalidated`` —— 死掉的苗头会一直挂在雷达上，
        # 这是本产品最该避免的失败模式（规格 §22：挂着不放比漏掉更糟）。
        #
        # 但也不能让已有卡片**无条件**绕过门槛：画像把某策略权重设为 0
        # （用户明确不关注）时不应继续产出该策略的机会卡
        # （既有语义，见 test_match_score_scales_with_profile_weight）。
        # 所以只对「已失效」这一种情况放行。
        has_card = _has_existing_card(session, company_id, profile_id, code)
        must_update = has_card and (
            bool(strategy.invalidation_hits(facts))  # type: ignore[attr-defined]
            or _not_actionable(facts)
        )

        coverage = evaluation.coverage
        match_score = compute_rule_score(
            facts_with_questions, code.value, ratio, evaluation=evaluation
        ).match_score

        # ★ 标的已无法布局 → 不进机会池（已有卡片的归档由 _build_one 处理）
        if not has_card and _not_actionable(facts):
            results.append(OpportunityBuildResult(
                thesis_type=code.value, created=False, coverage=coverage,
                reason="标的已停止交易（换股吸收合并 / 终止上市），不建卡 —— 事件仍保留在事件流",
            ))
            continue

        if not must_update and coverage < MIN_COVERAGE:
            results.append(OpportunityBuildResult(
                thesis_type=code.value, created=False, coverage=coverage,
                match_score=match_score,
                reason=f"核心条件覆盖 {coverage:.2f} < {MIN_COVERAGE}（逻辑强度不足，未建卡）",
            ))
            continue

        if not must_update and match_score < MIN_MATCH:
            results.append(OpportunityBuildResult(
                thesis_type=code.value, created=False, coverage=coverage,
                match_score=match_score,
                reason=f"匹配度 {match_score:.0f} < {MIN_MATCH}（与用户画像相关性不足，未建卡）",
            ))
            continue

        results.append(
            _build_one(
                session, company_id, profile_id, code.value, facts_with_questions,
                evaluation, ratio, open_questions, dry_run=dry_run, commit=commit,
                analysis_runner=analysis_runner,
            )
        )

    return tuple(results)


def _not_actionable(facts: StrategyFacts) -> bool:
    """标的**已经无法布局** —— 股票因换股吸收合并停止交易、上市地位终止。

    ★ 为什么这类公司不该进机会池（用户决定）：股票已停牌、价值已兑现，
    「提前布局」无从谈起。公告与事件**全部保留**（可追溯），只是不建卡。

    判定复用 ``is_completion_driven_delisting`` ——
    与失效规则同一个实现，避免两处关键词漂移。
    """
    return any(
        is_completion_driven_delisting(event.title or "") for event in facts.events
    )


def _has_existing_card(
    session: Session, company_id: int, profile_id: int, thesis_type: ThesisType
) -> bool:
    """该（公司, 画像, 策略）是否已经有机会卡。

    ★ 为什么需要单独判断：门槛要能区分「首次建卡」与「已有卡片」——
    对已有卡片，失效判定必须照跑。见 ``build_opportunities`` 里的说明。
    """
    thesis = session.exec(
        select(Thesis).where(
            Thesis.company_id == company_id,
            Thesis.thesis_type == ThesisType(thesis_type),
        )
    ).first()
    if thesis is None:
        return False
    return session.exec(
        select(Opportunity).where(
            Opportunity.company_id == company_id,
            Opportunity.profile_id == profile_id,
            Opportunity.thesis_id == int(thesis.id or 0),
        )
    ).first() is not None


def _accept_early_signals(session: Session, profile_id: int) -> bool:
    """画像是否把「早期苗头」纳入关注范围（§4 的产品设计：不同用户看不同东西）。

    找不到画像时默认 **True** —— 宁可见到（并标注为早期）也不要静默漏掉。
    """
    from app.models.profile import InvestmentProfile

    profile = session.get(InvestmentProfile, profile_id)
    if profile is None:
        return True
    return bool(profile.accept_early_signals)


# --------------------------------------------------------------------------- #
def _build_one(
    session: Session,
    company_id: int,
    profile_id: int,
    thesis_type: str,
    facts: StrategyFacts,
    evaluation: StrategyEvaluation,
    ratio: float,
    open_questions: tuple[str, ...],
    *,
    dry_run: bool,
    commit: bool,
    analysis_runner: "NodeRunner | None" = None,
) -> OpportunityBuildResult:
    strategy = get_strategy(thesis_type)
    definition = get_def(thesis_type)

    invalidation_hits = strategy.invalidation_hits(facts)  # type: ignore[attr-defined]
    from app.strategies.restructuring import invalidation as restructuring_invalidation

    should_invalidate = (
        restructuring_invalidation.should_invalidate(invalidation_hits)
        if thesis_type == ThesisType.RESTRUCTURING.value
        else bool(invalidation_hits)
    )
    # ★ 预警级别只提醒、不判死：早期待确认阶段收到监管关注值得知道，
    #   但问询函不等于交易失败（规格 §23 的 severity 分级正为此存在）
    warnings = (
        restructuring_invalidation.warning_hits(invalidation_hits)
        if thesis_type == ThesisType.RESTRUCTURING.value
        else ()
    )

    score = compute_rule_score(
        facts, thesis_type, ratio, evaluation=evaluation, invalidation_hits=invalidation_hits
    )

    supporting = _supporting_evidence_ids(facts, evaluation)
    why_in_radar = [c.label for c in evaluation.hits][:5]
    contradictory = _contradictory_evidence_ids(facts, invalidation_hits)

    if dry_run:
        return OpportunityBuildResult(
            thesis_type=thesis_type, created=False, coverage=evaluation.coverage,
            match_score=score.match_score, rule_score=score.rule_score,
            risk_score=score.risk_score, reason="dry_run：未写库",
            invalidated=should_invalidate,
        )

    # ---- Thesis（规格 §21：保存的是「因为 X 逻辑，所以关注 Y」）----
    statement = strategy.build_statement(facts, evaluation)  # type: ignore[attr-defined]
    why_now = strategy.why_now(facts)  # type: ignore[attr-defined]

    thesis = session.exec(
        select(Thesis).where(
            Thesis.company_id == company_id, Thesis.thesis_type == ThesisType(thesis_type)
        )
    ).first()
    if thesis is None:
        thesis = Thesis(
            company_id=company_id,
            thesis_type=ThesisType(thesis_type),
            statement=statement,
            invalidating_event_types=[d.event_type for d in definition.invalidating_events],
        )
        session.add(thesis)
        session.flush()
    else:
        thesis.statement = statement
        thesis.is_active = not should_invalidate
        thesis.updated_at = datetime.now(timezone.utc)
    # INV-TT1：失效条件不得为空
    guard.check_thesis_invalidation(
        thesis.invalidating_event_types or [d.event_type for d in definition.invalidating_events]
    )
    thesis.why_now_past = why_now.get("past")
    thesis.why_now_recent = why_now.get("recent")
    thesis.why_now_this_week = why_now.get("this_week")
    thesis.why_now_conclusion = why_now.get("conclusion", "")
    thesis.supporting_evidence_ids = list(supporting)
    thesis.contradictory_evidence_ids = list(contradictory)
    session.add(thesis)
    session.flush()
    thesis_id = int(thesis.id or 0)

    # ---- Opportunity ----
    stage = strategy.catalyst_strength(facts)  # type: ignore[attr-defined]
    early = bool(getattr(strategy, "is_early_signal", lambda _f: False)(facts))
    risks = _risk_labels(score)
    watch = (
        ["该逻辑已失效，观察是否重新筹划或出现反向进展"]
        if should_invalidate
        else _next_watch(definition, stage.score, facts)
    )

    opportunity = session.exec(
        select(Opportunity).where(
            Opportunity.company_id == company_id,
            Opportunity.profile_id == profile_id,
            Opportunity.thesis_id == thesis_id,
        )
    ).first()

    score_before = opportunity.rule_score if opportunity else None
    status_before = opportunity.status if opportunity else None

    # ---- 状态裁决 ----
    # 顺序有讲究：失效 → 不可布局（归档）→ 待确认 → 跟踪
    not_actionable = _not_actionable(facts)
    recovery_streak = int(opportunity.recovery_streak or 0) if opportunity else 0
    revived = False

    if should_invalidate:
        new_status = OpportunityStatus.INVALIDATED
        recovery_streak = 0          # 失效条件成立 → 重新计数
    elif status_before == OpportunityStatus.INVALIDATED.value and not_actionable:
        # 已失效 + 标的已停止交易 → 归档（终态），不需要纠错
        new_status = OpportunityStatus.ARCHIVED
        recovery_streak = 0
    elif status_before == OpportunityStatus.INVALIDATED.value and not not_actionable:
        # ★ 纠错：当前是失效状态，但按现在的规则失效条件已不成立。
        #
        # 为什么要「连续 N 次」才复活：规则或数据的一次抖动不应该让卡片在
        # 「失效 / 待确认」之间来回跳。连续多次一致等价于稳定信号。
        recovery_streak += 1
        if recovery_streak >= RECOVERY_STREAK_REQUIRED:
            revived = True
            recovery_streak = 0
            new_status = (
                OpportunityStatus.PENDING_CONFIRMATION
                if open_questions else OpportunityStatus.TRACKING
            )
        else:
            # 还不够稳定 → 保持失效（下一轮再判）
            new_status = OpportunityStatus.INVALIDATED
    elif not_actionable:
        # 不可布局 → 归档（终态）
        new_status = OpportunityStatus.ARCHIVED
        recovery_streak = 0
    elif open_questions:
        new_status = OpportunityStatus.PENDING_CONFIRMATION
    else:
        new_status = OpportunityStatus.TRACKING

    # ★ 逻辑失效时，若没有历史分数，就给出「若无失效事件本应是多少」的反事实分数 ——
    #   规格 §22 要的是「原机会评分 94 ↓ 31」这种可对比的叙事，而不是「None → 31」
    counterfactual: float | None = None
    if should_invalidate and score_before is None:
        counterfactual = compute_rule_score(
            facts, thesis_type, ratio, evaluation=evaluation, invalidation_hits=()
        ).rule_score
        score_before = counterfactual

    if opportunity is None:
        opportunity = Opportunity(
            company_id=company_id,
            profile_id=profile_id,
            thesis_id=thesis_id,
            status=new_status,
            first_discovered_at=datetime.now(timezone.utc),
        )
        session.add(opportunity)
        session.flush()
    else:
        # 状态机约束：只有合法迁移才改状态（M7-01）
        if status_before != new_status:
            try:
                guard.check_status_transition(status_before, new_status)
            except guard.InvariantViolation:
                new_status = OpportunityStatus(status_before)

    opportunity.status = new_status
    opportunity.catalyst_stage = stage.stage
    opportunity.is_early_signal = early
    opportunity.match_score = score.match_score
    opportunity.rule_score = score.rule_score
    opportunity.risk_score = score.risk_score
    opportunity.recovery_streak = recovery_streak
    opportunity.divergence = None
    opportunity.summary = None            # 由 analyze 节点填充（可选阶段）
    opportunity.why_in_radar = why_in_radar
    opportunity.why_now = [v for v in why_now.values() if v]
    opportunity.supporting_evidence_ids = list(supporting)
    opportunity.contradictory_evidence_ids = list(contradictory)
    opportunity.uncertainties = list(open_questions)
    opportunity.risks = risks
    opportunity.next_events_to_watch = watch
    opportunity.last_updated_at = datetime.now(timezone.utc)
    opportunity.score_version = score.score_version
    session.add(opportunity)
    session.flush()
    opportunity_id = int(opportunity.id or 0)

    # ---- 状态日志（仅在实际变化时写）----
    if status_before != new_status:
        session.add(OpportunityStatusLog(
            opportunity_id=opportunity_id,
            from_status=status_before,
            to_status=new_status,
            reason=(
                "命中逻辑失效条件" if should_invalidate
                # ★ 纠错必须留痕：说清「是原来的判定不成立」，而不是「新发现了什么」
                else "原失效判定已不再成立（规则修正后重算），恢复跟踪"
                if revived
                else "标的已停止交易（换股吸收合并 / 终止上市），归档"
                if not_actionable
                else f"存在 {len(open_questions)} 项待确认事项" if open_questions
                else "无待确认事项，进入跟踪"
            ),
            score_before=score_before,
            score_after=score.rule_score,
            changed_at=datetime.now(timezone.utc),
        ))

    # ---- 维度分与逐项拆解 ----
    _persist_scores(session, opportunity_id, score)

    # ---- 待确认事项（规格 §24：必须具体）----
    _persist_open_questions(session, opportunity_id, thesis_type, facts, open_questions)

    # ---- 逻辑失效提醒（规格 §22：推的是「你的逻辑变了」）----
    # ★ 只在**状态迁移进入**失效时发一次：重复运行不该重复打扰用户
    if should_invalidate and status_before != OpportunityStatus.INVALIDATED:
        session.add(Alert(
            opportunity_id=opportunity_id,
            alert_type="thesis_invalidated",
            title="投资逻辑发生重大变化",
            message=(
                f"你关注的「{definition.display_name}」逻辑出现失效事件："
                + "；".join(h.rule_description for h in invalidation_hits[:2])
            ),
            suggestion="建议重新评估该机会",
            score_before=score_before,
            score_after=score.rule_score,
            triggered_by_event_id=invalidation_hits[0].event_id if invalidation_hits else None,
            # counterfactual 仅用于日志可读性；Alert 本身只存 before/after
            created_at=datetime.now(timezone.utc),
        ))

    # ---- AI 分析：反证 / 叙事 / 语义分（新写好的那 3 个节点）----
    # ★ 顺序：风险 → 叙事 → 语义分。任何一步失败都不阻塞机会生成，
    #   但会把 node_status 记下来，避免「静默降级成没有 AI 叙事」。
    # ★ 默认 False：没有 analysis_runner（或 dry_run）时**不算**「做了深度分析」，
    #   否则报告会把「没跑」说成「跑了 0 次」——两者含义不同。
    ai_ok = False
    if analysis_runner is not None and not dry_run:
        # ★ 先提交，释放 SQLite 写锁。
        #   节点缓存用自己的连接写 llm_node_run；主 session 若仍持有未提交的
        #   写事务，缓存那条连接会拿不到写锁 → 「database is locked」整条中断。
        #   （这是把缓存改成独立 Session 后引入的回归，实测踩到。）
        session.commit()

        ai = analyze_opportunity(
            session, opportunity, facts, statement, score, analysis_runner,
            rule_next_watch=list(watch),
        )
        ai_ok = ai.ok
        if ai.summary:
            opportunity.summary = ai.summary
        if ai.risks:
            opportunity.risks = ai.risks
        if ai.uncertainties:
            opportunity.uncertainties = ai.uncertainties
        if ai.next_events_to_watch:
            opportunity.next_events_to_watch = ai.next_events_to_watch
        if ai.contradictory_evidence_ids:
            opportunity.contradictory_evidence_ids = ai.contradictory_evidence_ids
        if ai.why_now:
            opportunity.why_now = ai.why_now
        opportunity.semantic_score = ai.semantic_score
        opportunity.divergence = ai.divergence
        session.add(opportunity)
        session.flush()

        # 分歧 > 20 → 提示人工复核（D08：两个分数从不同角度看同一件事）
        # ★ 必须幂等：重复运行不该重复打扰用户（与失效/预警提醒同一条规则）
        if ai.divergence_flagged and session.exec(
            select(Alert).where(
                Alert.opportunity_id == opportunity_id,
                Alert.alert_type == "divergence_flagged",
            )
        ).first() is None:
            session.add(Alert(
                opportunity_id=opportunity_id,
                alert_type="divergence_flagged",
                title="规则分与语义分分歧较大",
                message=(
                    f"规则分 {score.rule_score:.1f} 与语义分 {ai.semantic_score:.1f} "
                    f"相差 {ai.divergence:.1f}。可能规则未覆盖某些因素，"
                    "也可能是模型在编造 —— 建议人工复核。"
                ),
                suggestion="语义分不参与排序，仅作提示",
                score_before=score.rule_score,
                score_after=score.rule_score,
                created_at=datetime.now(timezone.utc),
            ))

    # ---- 预警提醒（不改状态）----
    if warnings and not should_invalidate:
        first = warnings[0]
        already = session.exec(
            select(Alert).where(
                Alert.opportunity_id == opportunity_id,
                Alert.alert_type == "thesis_weakened",
                Alert.triggered_by_event_id == first.event_id,
            )
        ).first()
        if already is None:
            session.add(Alert(
                opportunity_id=opportunity_id,
                alert_type="thesis_weakened",
                title="投资逻辑出现预警信号",
                message=(
                    f"你关注的「{definition.display_name}」逻辑出现{first.severity}级信号："
                    f"{first.rule_description}"
                ),
                suggestion="暂不需要判定失效，但值得重新审视该机会",
                score_before=score_before,
                score_after=score.rule_score,
                triggered_by_event_id=first.event_id,
                created_at=datetime.now(timezone.utc),
            ))

    if commit:
        session.commit()

    return OpportunityBuildResult(
        thesis_type=thesis_type,
        created=True,
        opportunity_id=opportunity_id,
        coverage=evaluation.coverage,
        match_score=score.match_score,
        rule_score=score.rule_score,
        risk_score=score.risk_score,
        status=new_status.value,
        reason="已生成/更新机会",
        invalidated=should_invalidate,
        analyzed=ai_ok,
    )


# --------------------------------------------------------------------------- #
def _supporting_evidence_ids(
    facts: StrategyFacts, evaluation: StrategyEvaluation
) -> tuple[int, ...]:
    """支撑证据 = 命中条件所引用的事件证据（去重、保序）。"""
    seen: list[int] = []
    for condition in evaluation.hits:
        for evidence_id in condition.evidence_ids:
            if evidence_id not in seen:
                seen.append(evidence_id)
    if seen:
        return tuple(seen)
    # 回退：全部事件证据（条件未绑定证据时，至少不漏掉证据链）
    for event in facts.events:
        for evidence_id in event.evidence_ids:
            if evidence_id not in seen:
                seen.append(evidence_id)
    return tuple(seen)


def _contradictory_evidence_ids(facts: StrategyFacts, invalidation_hits: tuple) -> tuple[int, ...]:
    """反证 = 失效事件所绑定的证据（规则层能找到的「相反证据」）。"""
    hit_event_ids = {h.event_id for h in invalidation_hits}
    seen: list[int] = []
    for event in facts.events:
        if event.id in hit_event_ids:
            for evidence_id in event.evidence_ids:
                if evidence_id not in seen:
                    seen.append(evidence_id)
    return tuple(seen)


def _risk_labels(score) -> list[str]:
    """把风险拆解转成人类可读的标签（只列严重度 ≥ 中低的部分）。"""
    severity_by_key = score.risk_severities
    labels: list[str] = []
    for item in score.dimension(ScoreDimension.RISK).items:
        key = item.rule_id.removeprefix("R-GEN-RK-").lower()
        severity = severity_by_key.get(key)
        if severity is None or severity < RISK_LIST_THRESHOLD:
            continue
        labels.append(item.reason)
    return labels


def _next_watch(definition, current_stage_score: float, facts: StrategyFacts) -> list[str]:
    """「下一步观察什么」= 当前催化剂阶段**之上**的阶梯 + 未回复的问询。

    这样它天然对应该策略真实的推进路径，而不是一句写死的话。
    """
    watch = [
        stage.stage
        for stage in definition.catalyst_ladder
        if stage.score > current_stage_score
    ]
    if facts.has_unanswered_inquiry:
        watch.insert(0, "交易所问询回复")
    if not watch:
        watch = [f"是否有「{d.description}」类公告" for d in definition.invalidating_events[:2]]
    return watch


def _persist_scores(session: Session, opportunity_id: int, score) -> None:
    session.exec(delete(OpportunityScore).where(
        OpportunityScore.opportunity_id == opportunity_id
    ))
    session.exec(delete(ScoreItem).where(ScoreItem.opportunity_id == opportunity_id))
    for dimension in score.dimensions:
        session.add(OpportunityScore(
            opportunity_id=opportunity_id,
            dimension=dimension.dimension,
            raw_value=dimension.raw_value,
            weight=dimension.weight,
            weighted_value=dimension.weighted_value,
            source=ScoreSource.RULE,
        ))
    for dimension, item in score.score_items:
        session.add(ScoreItem(
            opportunity_id=opportunity_id,
            source=ScoreSource.RULE,
            dimension=dimension,
            delta=item.delta,
            reason=item.reason,
            rule_id=item.rule_id,
            evidence_ids=list(item.evidence_ids),
        ))


def _persist_open_questions(
    session: Session,
    opportunity_id: int,
    thesis_type: str,
    facts: StrategyFacts,
    open_questions: tuple[str, ...],
) -> None:
    """写入待确认事项：未确认的标 open，已确认的带证据 ID（规格 §24 的 ✓ / ? 两侧）。"""
    from app.strategies.restructuring import questions as restructuring_questions

    session.exec(delete(OpenQuestion).where(OpenQuestion.opportunity_id == opportunity_id))

    seeds = (
        restructuring_questions.evaluate(facts)
        if thesis_type == ThesisType.RESTRUCTURING.value
        else ()
    )
    if seeds:
        for seed in seeds:
            session.add(OpenQuestion(
                opportunity_id=opportunity_id,
                question=seed.question,
                status=seed.status,
                confirmed_evidence_id=seed.confirmed_by_event_id,
            ))
        return

    for question in open_questions:
        session.add(OpenQuestion(
            opportunity_id=opportunity_id, question=question, status="open"
        ))


__all__ = [
    "MIN_COVERAGE",
    "MIN_MATCH",
    "OpportunityBuildResult",
    "build_opportunities",
]
