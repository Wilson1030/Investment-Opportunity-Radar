"""评分引擎（docs/04-评分与证据链 §2）。

自洽定义（规格 §12 的权重视为权威，§13/§14 视为展示形态参考 —— OQ-01 已确认）::

    rule_score = clamp( Σ_d ( raw_d × w_d ) − risk_penalty , 0 , 100 )
    risk_penalty = (risk_score / 100) × 15

不变量：``rule_score == Σ dimensions[i].weighted_value``（含风险项的负值）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine import freshness, rules
from app.engine.rules import DimensionComputation, RuleHit
from app.facts import StrategyFacts
from app.models.enums import DIMENSION_LABELS, ScoreDimension
from app.strategies import (
    RISK_PENALTY_MAX,
    StrategyEvaluation,
    get_strategy,
    weights_for,
)

#: 规则集版本（规则语义变更时递增 → ``Opportunity.score_version`` 随之变化）
RULESET_VERSION = "1.0"
#: 权重版本（权重调整时递增）
WEIGHTS_VERSION = "1.0"

#: 规则分与语义分的分歧阈值：超过则标红提示人工复核（D08）
SEMANTIC_DIVERGENCE_THRESHOLD = 20.0

#: 风险维度的展示权重（等价的扣分比例，用于 UI 统一展示）
RISK_DISPLAY_WEIGHT = RISK_PENALTY_MAX / 100.0


@dataclass(frozen=True)
class DimensionResult:
    """一级展开：维度分（规格 §14 形态）。"""

    dimension: ScoreDimension
    display_name: str
    raw_value: float
    weight: float
    weighted_value: float
    direction: str                      # positive | negative
    items: tuple[RuleHit, ...] = ()
    note: str = ""

    @property
    def direction_note(self) -> str | None:
        """风险维度必须显式标注方向，避免「风险 43」被误读（docs/04 §7）。"""
        if self.direction == "negative":
            return "★ 数值越高代表风险越大（与上方维度方向相反）"
        return None


@dataclass(frozen=True)
class RuleScoreResult:
    thesis_type: str
    rule_score: float
    risk_score: float
    risk_penalty: float
    match_score: float
    coverage: float
    dimensions: tuple[DimensionResult, ...]
    score_version: str
    risk_severities: dict[str, float]

    def dimension(self, dimension: ScoreDimension) -> DimensionResult:
        for result in self.dimensions:
            if result.dimension == dimension:
                return result
        raise KeyError(dimension)

    @property
    def score_items(self) -> tuple[tuple[ScoreDimension, RuleHit], ...]:
        """展平后的逐项加减分（二级展开：规格 §13 形态）。"""
        return tuple(
            (d.dimension, hit) for d in self.dimensions for hit in d.items
        )

    @property
    def rule_score_display(self) -> int:
        return int(round(self.rule_score))


# --------------------------------------------------------------------------- #
# 匹配度（规格 §4.3 / §5.8）
# --------------------------------------------------------------------------- #
def compute_match_score(profile_weight_ratio: float, coverage: float) -> float:
    """``match_score = 100 × (w_thesis / w_max) × coverage``。

    复现规格 §5.8 的三组示例（同一回购事件）：高股息 55 / 股东回报 94 / 重组预期 18。
    """
    ratio = max(0.0, min(1.0, profile_weight_ratio))
    cov = max(0.0, min(1.0, coverage))
    return round(100.0 * ratio * cov, 2)


def profile_weight_ratio(weights: dict[str, float], thesis_type: str) -> float:
    """``w_thesis / w_max`` ∈ [0, 1]（docs/04 §4.1）。

    画像里没有配置该策略 → 0（不会误报高匹配）；
    权重可为任意正数，读取时按最大值归一化（INV-PW1：权重之和不必为 1）。
    """
    positive = {k: v for k, v in (weights or {}).items() if v and v > 0}
    if not positive:
        return 0.0
    maximum = max(positive.values())
    current = (weights or {}).get(thesis_type, 0.0) or 0.0
    if maximum <= 0 or current <= 0:
        return 0.0
    return min(1.0, current / maximum)


def compute_divergence(rule_score: float, semantic_score: float | None) -> float | None:
    if semantic_score is None:
        return None
    return round(abs(rule_score - semantic_score), 2)


def is_divergence_flagged(divergence: float | None) -> bool:
    return divergence is not None and divergence > SEMANTIC_DIVERGENCE_THRESHOLD


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def compute_rule_score(
    facts: StrategyFacts,
    thesis_type: str,
    profile_weight_ratio: float = 1.0,
    *,
    evaluation: StrategyEvaluation | None = None,
    invalidation_hits: tuple = (),
    age_days: float | None = None,
) -> RuleScoreResult:
    """计算规则分（主分）。

    参数
    ----
    profile_weight_ratio
        该策略在用户画像中的归一化相对权重（``w_thesis / w_max`` ∈ [0, 1]）。
    evaluation
        策略核心条件评估结果；为 ``None`` 时现场计算。
    invalidation_hits
        失效条件命中（决定 ``RISK`` 中的 ``is_invalidating`` 触发）。
    age_days
        最新证据距今天数；为 ``None`` 时取 ``facts.newest_evidence_age_days``。
    """
    strategy = get_strategy(thesis_type)
    if evaluation is None:
        evaluation = strategy.evaluate(facts)  # type: ignore[attr-defined]

    if age_days is None:
        age_days = facts.newest_evidence_age_days
    decay = freshness.decay_factor(age_days)

    coverage = evaluation.coverage
    match_score = compute_match_score(profile_weight_ratio, coverage)

    invalidating_ids = frozenset(
        getattr(hit, "event_id", None) for hit in invalidation_hits
    ) - {None}
    computations: list[DimensionComputation] = [
        rules.compute_thesis_match_dimension(thesis_type, coverage, profile_weight_ratio),
        rules.compute_event_catalyst_dimension(
            facts, thesis_type, decay, invalidating_event_ids=invalidating_ids
        ),
        rules.compute_catalyst_strength_dimension(facts, thesis_type),
        rules.compute_certainty_dimension(facts),
        rules.compute_fundamentals_dimension(facts),
        rules.compute_shareholder_dimension(facts),
        rules.compute_market_attention_dimension(facts, decay),
        rules.compute_history_case_dimension(facts),
    ]

    weights = weights_for(thesis_type)
    dimensions: list[DimensionResult] = []
    total = 0.0
    for computation in computations:
        weight = weights.get(computation.dimension, 0.0)
        weighted = round(computation.raw_value * weight, 6)
        total += weighted
        dimensions.append(
            DimensionResult(
                dimension=computation.dimension,
                display_name=DIMENSION_LABELS[computation.dimension],
                raw_value=round(computation.raw_value, 4),
                weight=weight,
                weighted_value=weighted,
                direction="positive",
                items=computation.hits,
                note=computation.note,
            )
        )

    # ---- 风险：负向维度 ----
    risk_score, risk_hits, severities = rules.compute_risk(
        facts, thesis_type, rules.RiskContext(is_invalidating=bool(invalidation_hits))
    )
    risk_penalty = round((risk_score / 100.0) * RISK_PENALTY_MAX, 6)
    total += -risk_penalty
    dimensions.append(
        DimensionResult(
            dimension=ScoreDimension.RISK,
            display_name=DIMENSION_LABELS[ScoreDimension.RISK],
            raw_value=round(risk_score, 4),
            weight=-RISK_DISPLAY_WEIGHT,
            weighted_value=-risk_penalty,
            direction="negative",
            items=risk_hits,
            note="风险分越高代表风险越大；扣分上限 15 分",
        )
    )

    rule_score = max(0.0, min(100.0, round(total, 6)))

    return RuleScoreResult(
        thesis_type=str(thesis_type),
        rule_score=rule_score,
        risk_score=round(risk_score, 4),
        risk_penalty=risk_penalty,
        match_score=match_score,
        coverage=round(coverage, 6),
        dimensions=tuple(dimensions),
        score_version=f"rules-{RULESET_VERSION}+weights-{thesis_type}-{WEIGHTS_VERSION}",
        risk_severities=severities,
    )


def verify_internal_consistency(result: RuleScoreResult, tolerance: float = 1e-6) -> list[str]:
    """自检：``rule_score`` 必须等于各维度加权值之和。"""
    problems: list[str] = []
    total = sum(d.weighted_value for d in result.dimensions)
    if abs(total - result.rule_score) > tolerance and result.rule_score not in (0.0, 100.0):
        problems.append(
            f"rule_score {result.rule_score} ≠ Σweighted_value {total}"
        )
    if result.match_score > 100 or result.match_score < 0:
        problems.append(f"match_score 越界：{result.match_score}")
    if not (0 <= result.risk_score <= 100):
        problems.append(f"risk_score 越界：{result.risk_score}")
    return problems


__all__ = [
    "RULESET_VERSION",
    "RISK_DISPLAY_WEIGHT",
    "SEMANTIC_DIVERGENCE_THRESHOLD",
    "WEIGHTS_VERSION",
    "DimensionResult",
    "RuleScoreResult",
    "compute_divergence",
    "compute_match_score",
    "compute_rule_score",
    "profile_weight_ratio",
    "is_divergence_flagged",
    "verify_internal_consistency",
]
