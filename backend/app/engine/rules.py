"""维度规则档案（docs/04-评分与证据链 §4）。

本模块是**纯粹的函数集合**：输入 :class:`~app.facts.StrategyFacts`，输出规则命中项。
不访问数据库、不调用 LLM —— 这正是「确定性的事情交给规则和程序」（规格 §28）。

规则 ID 规范：``R-<策略或 GEN>-<维度>-<序号>``。
每个 :class:`RuleHit` 都会落库为 ``ScoreItem``，因此用户可以点击展开看到
「为什么是 82」以及**每一项对应的证据**（规格 §13）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.facts import StrategyFacts
from app.models.enums import (
    EventType,
    ReliabilityLevel,
    ScoreDimension,
)
from app.strategies import get_def, get_strategy

# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleHit:
    """一条规则命中 —— 直接对应一个 ``ScoreItem``。"""

    rule_id: str
    dimension: ScoreDimension
    delta: float
    reason: str
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class DimensionComputation:
    dimension: ScoreDimension
    raw_value: float
    hits: tuple[RuleHit, ...]
    note: str = ""


@dataclass(frozen=True)
class RiskContext:
    """风险判别所需的外部上下文。"""

    is_invalidating: bool = False


# --------------------------------------------------------------------------- #
# 风险触发判别表（registry 的所有 RiskTrigger.when 必须在此存在 —— 启动自检强制）
# --------------------------------------------------------------------------- #

RiskPredicate = Callable[[StrategyFacts, RiskContext], bool]

RISK_TRIGGER_PREDICATES: dict[str, RiskPredicate] = {
    # 事件 / 逻辑层面
    "is_invalidating": lambda f, c: c.is_invalidating,
    "has_history_failure": lambda f, c: f.has_history_failure,
    "has_late_stage_pending_approval": lambda f, c: f.has_late_stage_pending_approval,
    "has_conflicting_media": lambda f, c: f.has_conflicting_media,
    # 监管
    "has_unanswered_inquiry": lambda f, c: f.has_unanswered_inquiry,
    # 基本面
    "loss_years_gte_2": lambda f, c: f.financials.loss_years >= 2,
    "not_profitable": lambda f, c: f.financials.loss_years > 0
    or f.financials.profitable_years == 0,
    "ocf_not_positive": lambda f, c: not f.financials.ocf_positive,
    "margin_declining": lambda f, c: f.financials.margin_improving_quarters == 0,
    "receivable_growth_exceeds_revenue": lambda f, c: (
        f.financials.receivable_growth_exceeds_revenue
    ),
    "debt_ratio_rising": lambda f, c: f.financials.debt_ratio_rising,
    "one_off_attributed": lambda f, c: f.financials.deteriorating_attributed_to_one_off,
    # 股东
    "high_pledge": lambda f, c: f.shareholder.high_pledge,
    # 交易性 / 估值
    "is_halted": lambda f, c: f.is_halted,
    "high_valuation": lambda f, c: (
        f.valuation_percentile is not None and f.valuation_percentile >= 0.80
    ),
    # 市场：只有 E 类讨论、缺硬证据（规格 §16 的正确用法）
    "social_buzz_only": lambda f, c: f.market.social_buzz and not f.has_hard_evidence,
}

#: 风险严重度形容词分档（docs/04 §4.8）—— 仅用于展示，计算使用 0~1 连续值
SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.80, "高"),
    (0.60, "中高"),
    (0.40, "中"),
    (0.20, "中低"),
    (0.00, "低"),
)


def severity_label(value: float) -> str:
    for threshold, label in SEVERITY_BANDS:
        if value >= threshold:
            return label
    return "低"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _event_ids(facts: StrategyFacts, *types: EventType) -> tuple[int, ...]:
    return tuple(e.id for e in facts.events_of(*types))


# --------------------------------------------------------------------------- #
# 逻辑匹配（规格 §12 的第一大权重；复用 match_score，见 docs/04 §4.1）
# --------------------------------------------------------------------------- #
def compute_thesis_match_dimension(
    thesis_type: str,
    coverage: float,
    profile_weight_ratio: float,
) -> DimensionComputation:
    """``THESIS_MATCH = 100 × (w_thesis / w_max) × coverage``。

    该公式复现规格 §5.8 的三组示例（回购事件）：高股息 55 / 股东回报 94 / 重组预期 18。
    """
    ratio = max(0.0, min(1.0, profile_weight_ratio))
    cov = max(0.0, min(1.0, coverage))
    raw = round(100.0 * ratio * cov, 4)
    hit = RuleHit(
        rule_id=f"R-{thesis_type[:2].upper()}-TM-01",
        dimension=ScoreDimension.THESIS_MATCH,
        delta=raw,
        reason=(
            f"策略权重比 {ratio:.2f} × 核心条件覆盖 {cov:.2f}"
            f"（{cov * 100:.0f}%）"
        ),
    )
    return DimensionComputation(ScoreDimension.THESIS_MATCH, raw, (hit,))


# --------------------------------------------------------------------------- #
# 事件催化（规格 §12 的「事件重要程度」）
# --------------------------------------------------------------------------- #
def compute_event_catalyst_dimension(
    facts: StrategyFacts, thesis_type: str, decay: float
) -> DimensionComputation:
    definition = get_def(thesis_type)
    support = set(definition.support_event_types)
    hits: list[RuleHit] = []
    total = 0.0

    support_events = [e for e in facts.events if e.event_type in support]
    best_level = facts.best_evidence_level

    if any(e.evidence_level is ReliabilityLevel.A for e in support_events):
        total += 40
        hits.append(RuleHit(
            "R-GEN-EV-01", ScoreDimension.EVENT_CATALYST, 40,
            "存在 A 类公告直接对应核心事件类型",
            _event_ids(facts, *support),
        ))

    distinct = {e.event_type for e in support_events}
    if len(distinct) >= 2:
        total += 25
        hits.append(RuleHit(
            "R-GEN-EV-02", ScoreDimension.EVENT_CATALYST, 25,
            f"同期出现 {len(distinct)} 个不同事件类型（组合催化）",
            tuple(e.id for e in support_events),
        ))

    significant = [e for e in support_events if e.amount_ratio >= 0.10]
    if significant:
        total += 15
        hits.append(RuleHit(
            "R-GEN-EV-03", ScoreDimension.EVENT_CATALYST, 15,
            f"事件涉及金额达显著阈值（最高占比 {max(e.amount_ratio for e in significant):.2f}）",
            tuple(e.id for e in significant),
        ))

    if any(e.counterparty_known for e in support_events):
        total += 10
        hits.append(RuleHit(
            "R-GEN-EV-04", ScoreDimension.EVENT_CATALYST, 10,
            "事件牵涉可识别的产业方 / 头部企业",
            tuple(e.id for e in support_events if e.counterparty_known),
        ))

    if best_level is ReliabilityLevel.C:
        total += 10
        hits.append(RuleHit(
            "R-GEN-EV-05", ScoreDimension.EVENT_CATALYST, 10,
            "仅由 C 类（高可信媒体）报道，无正式公告",
        ))
    elif best_level is ReliabilityLevel.E:
        total += 5
        hits.append(RuleHit(
            "R-GEN-EV-06", ScoreDimension.EVENT_CATALYST, 5,
            "仅由 E 类（市场讨论）出现，作为「市场正在讨论什么」计分",
        ))

    raw_before_decay = _clamp(total)
    raw = _clamp(raw_before_decay * decay)
    if decay < 1.0:
        hits.append(RuleHit(
            "R-GEN-EV-TIME", ScoreDimension.EVENT_CATALYST, 0.0,
            f"时效衰减系数 {decay:.2f}（应用于本维度，不额外加减分）",
        ))
    return DimensionComputation(ScoreDimension.EVENT_CATALYST, raw, tuple(hits))


# --------------------------------------------------------------------------- #
# 催化剂强度
# --------------------------------------------------------------------------- #
def compute_catalyst_strength_dimension(
    facts: StrategyFacts, thesis_type: str
) -> DimensionComputation:
    strategy = get_strategy(thesis_type)
    stage = strategy.catalyst_strength(facts)  # type: ignore[attr-defined]
    hit = RuleHit(
        rule_id=f"R-{str(thesis_type)[:2].upper()}-CS-01",
        dimension=ScoreDimension.CATALYST_STRENGTH,
        delta=stage.score,
        reason=f"{stage.stage}｜{stage.description}",
    )
    return DimensionComputation(ScoreDimension.CATALYST_STRENGTH, _clamp(stage.score), (hit,))


# --------------------------------------------------------------------------- #
# 确定性
# --------------------------------------------------------------------------- #
_BASE_BY_LEVEL: dict[ReliabilityLevel, float] = {
    ReliabilityLevel.A: 100.0,
    ReliabilityLevel.B: 85.0,
    ReliabilityLevel.C: 60.0,
    ReliabilityLevel.D: 45.0,
    ReliabilityLevel.E: 20.0,
}

#: 未确认事项的单项扣分与上限（docs/04 §4.4）
OPEN_QUESTION_PENALTY = 8.0
OPEN_QUESTION_PENALTY_CAP = 30.0


def compute_certainty_dimension(facts: StrategyFacts) -> DimensionComputation:
    best = facts.best_evidence_level
    base = _BASE_BY_LEVEL.get(best, 40.0) if best else 40.0
    level_note = (
        f"最高证据等级 {best.value}（{_LEVEL_NAME[best]}）" if best else "无证据（按最低基线计）"
    )
    hits: list[RuleHit] = [
        RuleHit("R-GEN-CT-00", ScoreDimension.CERTAINTY, base, f"确定性基线：{level_note}")
    ]
    total = base

    if facts.open_question_count > 0:
        penalty = min(OPEN_QUESTION_PENALTY_CAP, OPEN_QUESTION_PENALTY * facts.open_question_count)
        total -= penalty
        hits.append(RuleHit(
            "R-GEN-CT-01", ScoreDimension.CERTAINTY, -penalty,
            f"存在 {facts.open_question_count} 项待确认事项"
            f"（{OPEN_QUESTION_PENALTY:.0f}/项，上限 {OPEN_QUESTION_PENALTY_CAP:.0f}）",
        ))

    if facts.has_unanswered_inquiry:
        total -= 15
        hits.append(RuleHit("R-GEN-CT-02", ScoreDimension.CERTAINTY, -15,
                            "存在未回复的监管问询 / 问询函"))

    if facts.has_late_stage_pending_approval:
        total -= 10
        hits.append(RuleHit("R-GEN-CT-03", ScoreDimension.CERTAINTY, -10,
                            "进入后期阶段但仍有前置审批未完成"))

    if facts.has_history_failure:
        total -= 10
        hits.append(RuleHit("R-GEN-CT-04", ScoreDimension.CERTAINTY, -10,
                            "公司历史上有同类事项失败记录"))

    if facts.has_conflicting_media:
        total -= 5
        hits.append(RuleHit("R-GEN-CT-05", ScoreDimension.CERTAINTY, -5,
                            "存在与官方公告口径不一致的媒体报道"))

    return DimensionComputation(ScoreDimension.CERTAINTY, _clamp(total), tuple(hits))


_LEVEL_NAME: dict[ReliabilityLevel, str] = {
    ReliabilityLevel.A: "公司正式公告 / 交易所披露",
    ReliabilityLevel.B: "公司财报 / 官方文件",
    ReliabilityLevel.C: "高可信媒体",
    ReliabilityLevel.D: "机构 / 研究观点",
    ReliabilityLevel.E: "社交媒体 / 市场讨论",
}


# --------------------------------------------------------------------------- #
# 基本面
# --------------------------------------------------------------------------- #
FUNDAMENTALS_BASELINE = 50.0


def compute_fundamentals_dimension(facts: StrategyFacts) -> DimensionComputation:
    fin = facts.financials
    hits: list[RuleHit] = [
        RuleHit("R-GEN-FD-00", ScoreDimension.FUNDAMENTALS, FUNDAMENTALS_BASELINE,
                "基本面基线")
    ]
    total = FUNDAMENTALS_BASELINE

    if fin.revenue_improving_quarters >= 2:
        total += 25
        hits.append(RuleHit("R-GEN-FD-01", ScoreDimension.FUNDAMENTALS, 25,
                            f"营收连续 {fin.revenue_improving_quarters} 期改善"))
    if fin.ocf_positive and fin.ocf_improving:
        total += 20
        hits.append(RuleHit("R-GEN-FD-02", ScoreDimension.FUNDAMENTALS, 20,
                            "经营现金流为正且同比改善"))
    if fin.margin_improving_quarters >= 2:
        total += 20
        hits.append(RuleHit("R-GEN-FD-03", ScoreDimension.FUNDAMENTALS, 20,
                            f"毛利率连续 {fin.margin_improving_quarters} 期改善"))
    if fin.profitable_years >= 3 and fin.ocf_positive:
        total += 20
        hits.append(RuleHit("R-GEN-FD-04", ScoreDimension.FUNDAMENTALS, 20,
                            f"连续 {fin.profitable_years} 年盈利且现金流为正"))

    if fin.receivable_growth_exceeds_revenue:
        total -= 15
        hits.append(RuleHit("R-GEN-FD-05", ScoreDimension.FUNDAMENTALS, -15,
                            "应收账款增速显著高于营收增速"))
    if fin.debt_ratio_rising:
        total -= 10
        hits.append(RuleHit("R-GEN-FD-06", ScoreDimension.FUNDAMENTALS, -10,
                            "资产负债率同比显著上升"))

    if fin.deteriorating_attributed_to_one_off:
        hits.append(RuleHit("R-GEN-FD-07", ScoreDimension.FUNDAMENTALS, 0.0,
                            "指标恶化已归因于一次性因素（减值 / 重组费用）→ 不扣分（INV-F1）"))

    return DimensionComputation(ScoreDimension.FUNDAMENTALS, _clamp(total), tuple(hits))


# --------------------------------------------------------------------------- #
# 股东结构变化
# --------------------------------------------------------------------------- #
SHAREHOLDER_BASELINE = 50.0


def compute_shareholder_dimension(facts: StrategyFacts) -> DimensionComputation:
    sh = facts.shareholder
    hits: list[RuleHit] = [
        RuleHit("R-GEN-SH-00", ScoreDimension.SHAREHOLDER_STRUCTURE, SHAREHOLDER_BASELINE,
                "股东结构基线")
    ]
    total = SHAREHOLDER_BASELINE
    control_events = _event_ids(facts, EventType.CONTROL_CHANGE)

    if sh.controlling_shareholder_changed or sh.actual_controller_changed or control_events:
        total += 40
        hits.append(RuleHit("R-GEN-SH-01", ScoreDimension.SHAREHOLDER_STRUCTURE, 40,
                            "控股股东 / 实际控制人发生变更", control_events))
    if sh.insider_buy or facts.has_event(EventType.SHAREHOLDER_BUY):
        total += 20
        hits.append(RuleHit("R-GEN-SH-02", ScoreDimension.SHAREHOLDER_STRUCTURE, 20,
                            "控股股东或管理层增持",
                            _event_ids(facts, EventType.SHAREHOLDER_BUY)))
    if sh.buyback and sh.buyback_scale_significant:
        total += 20
        hits.append(RuleHit("R-GEN-SH-03", ScoreDimension.SHAREHOLDER_STRUCTURE, 20,
                            "公司回购且规模显著",
                            _event_ids(facts, EventType.BUYBACK)))
    if sh.high_pledge:
        total -= 15
        hits.append(RuleHit("R-GEN-SH-04", ScoreDimension.SHAREHOLDER_STRUCTURE, -15,
                            "存在股权质押且比例较高"))
    if sh.insider_sell or facts.has_event(EventType.SHAREHOLDER_SELL):
        total -= 10
        hits.append(RuleHit("R-GEN-SH-05", ScoreDimension.SHAREHOLDER_STRUCTURE, -10,
                            "控股股东减持",
                            _event_ids(facts, EventType.SHAREHOLDER_SELL)))

    return DimensionComputation(ScoreDimension.SHAREHOLDER_STRUCTURE, _clamp(total), tuple(hits))


# --------------------------------------------------------------------------- #
# 市场关注 ★ C / D / E 类证据唯一合法的去处（docs/04 §4.7）
# --------------------------------------------------------------------------- #
MARKET_BASELINE = 40.0


def compute_market_attention_dimension(
    facts: StrategyFacts, decay: float
) -> DimensionComputation:
    market = facts.market
    hits: list[RuleHit] = [
        RuleHit("R-GEN-MA-00", ScoreDimension.MARKET_ATTENTION, MARKET_BASELINE,
                "市场关注基线")
    ]
    total = MARKET_BASELINE

    clusters = market.news_cluster_count
    if clusters >= 10:
        total += 20
        hits.append(RuleHit("R-GEN-MA-01", ScoreDimension.MARKET_ATTENTION, 20,
                            f"相关报道/讨论形成 {clusters} 个事件簇"))
    elif clusters >= 3:
        total += 10
        hits.append(RuleHit("R-GEN-MA-01", ScoreDimension.MARKET_ATTENTION, 10,
                            f"相关报道/讨论形成 {clusters} 个事件簇"))
    elif clusters >= 1:
        total += 5
        hits.append(RuleHit("R-GEN-MA-01", ScoreDimension.MARKET_ATTENTION, 5,
                            f"相关报道/讨论形成 {clusters} 个事件簇"))

    if market.abnormal_volatility:
        total += 15
        hits.append(RuleHit("R-GEN-MA-02", ScoreDimension.MARKET_ATTENTION, 15,
                            "出现股票异常波动公告"))
    if market.on_dragon_tiger:
        total += 10
        hits.append(RuleHit("R-GEN-MA-03", ScoreDimension.MARKET_ATTENTION, 10,
                            "登上龙虎榜 / 成交量显著放大"))
    if market.institutional_reports_delta > 0:
        total += 10
        hits.append(RuleHit("R-GEN-MA-04", ScoreDimension.MARKET_ATTENTION, 10,
                            f"机构研报覆盖增加 {market.institutional_reports_delta} 篇"))
    if market.social_buzz:
        total += 5
        hits.append(RuleHit("R-GEN-MA-05", ScoreDimension.MARKET_ATTENTION, 5,
                            "存在 E 类社交媒体热议（仅在此维度计分）"))

    raw = _clamp(_clamp(total) * decay)
    if decay < 1.0:
        hits.append(RuleHit("R-GEN-MA-TIME", ScoreDimension.MARKET_ATTENTION, 0.0,
                            f"时效衰减系数 {decay:.2f}"))
    return DimensionComputation(ScoreDimension.MARKET_ATTENTION, raw, tuple(hits))


# --------------------------------------------------------------------------- #
# 历史相似案例（Phase 2 启用；MVP 权重为 0）
# --------------------------------------------------------------------------- #
def compute_history_case_dimension(facts: StrategyFacts) -> DimensionComputation:
    hit = RuleHit("R-GEN-HC-00", ScoreDimension.HISTORY_CASE, 0.0,
                  "历史案例库未启用（docs/07 §9，Phase 2）")
    return DimensionComputation(ScoreDimension.HISTORY_CASE, 0.0, (hit,))


# --------------------------------------------------------------------------- #
# 风险
# --------------------------------------------------------------------------- #
def compute_risk(
    facts: StrategyFacts,
    thesis_type: str,
    context: RiskContext,
) -> tuple[float, tuple[RuleHit, ...], dict[str, float]]:
    """返回 ``(risk_score, hits, severities)``；``risk_score`` 越高越危险。"""
    factors = get_def(thesis_type).risk_factors
    if not factors:
        return 0.0, (), {}

    hits: list[RuleHit] = []
    severities: dict[str, float] = {}
    weighted = 0.0
    weight_total = 0.0

    for factor in factors:
        severity = factor.base_severity
        reasons: list[str] = []
        for trigger in factor.triggers:
            predicate = RISK_TRIGGER_PREDICATES.get(trigger.when)
            if predicate is None or not predicate(facts, context):
                continue
            severity = (
                severity + trigger.severity if trigger.mode == "add" else trigger.severity
            )
            severity = max(0.0, min(1.0, severity))
            reasons.append(f"{trigger.reason}（{severity_label(trigger.severity)}）")

        severities[factor.key] = severity
        weighted += factor.weight * severity
        weight_total += factor.weight

        explanation = (
            "；".join(reasons) if reasons else f"未触发具体条件，按基础风险 {severity:.2f} 计"
        )
        hits.append(RuleHit(
            rule_id=f"R-GEN-RK-{factor.key.upper()}",
            dimension=ScoreDimension.RISK,
            delta=severity,
            reason=f"{factor.label}：{severity_label(severity)}（{severity:.2f}）｜{explanation}",
        ))

    risk_score = 100.0 * weighted / weight_total if weight_total else 0.0
    return round(risk_score, 4), tuple(hits), severities


__all__ = [
    "FUNDAMENTALS_BASELINE",
    "MARKET_BASELINE",
    "OPEN_QUESTION_PENALTY",
    "OPEN_QUESTION_PENALTY_CAP",
    "RISK_TRIGGER_PREDICATES",
    "SEVERITY_BANDS",
    "SHAREHOLDER_BASELINE",
    "DimensionComputation",
    "RiskContext",
    "RuleHit",
    "compute_catalyst_strength_dimension",
    "compute_certainty_dimension",
    "compute_event_catalyst_dimension",
    "compute_fundamentals_dimension",
    "compute_history_case_dimension",
    "compute_market_attention_dimension",
    "compute_risk",
    "compute_shareholder_dimension",
    "compute_thesis_match_dimension",
    "severity_label",
]
