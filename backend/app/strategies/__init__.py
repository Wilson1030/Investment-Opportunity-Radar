"""策略包入口：数据（registry）与实现（loader）分离。

* :mod:`app.strategies.registry` —— 10 类策略的**完整设计元数据**（纯数据）
* :func:`get_strategy`            —— 按 code 取**实现类**；未实现的返回占位实现

占位实现会**显式抛错**而不是返回「0 分机会」—— 静默的假结果比明确的错误危险得多。
"""

from __future__ import annotations

from app.models.enums import StrategyStatus, ThesisType
from app.strategies.base import (
    CatalystStage,
    ConditionResult,
    InvalidationHit,
    NotImplementedStrategy,
    Strategy,
    StrategyEvaluation,
)
from app.strategies.registry import (
    GLOBAL_DEFAULT_WEIGHTS,
    IMPLEMENTATION_ORDER,
    POSITIVE_WEIGHT_TOTAL,
    RISK_PENALTY_MAX,
    STRATEGIES,
    STRATEGY_TEMPLATES,
    StrategyDef,
    designed_types,
    get_def,
    implemented_types,
    weights_for,
)

_IMPLEMENTATIONS: dict[ThesisType, object] = {}


def _register_implementations() -> None:
    """延迟导入实现类，避免 registry ↔ implementation 的循环依赖。"""
    if _IMPLEMENTATIONS:
        return
    from app.strategies.restructuring.rules import STRATEGY as restructuring_strategy

    _IMPLEMENTATIONS[ThesisType.RESTRUCTURING] = restructuring_strategy


def get_strategy(thesis_type: ThesisType | str) -> object:
    """返回策略实现。未实现的策略返回 :class:`NotImplementedStrategy`（调用即抛错）。"""
    code = ThesisType(thesis_type)
    _register_implementations()
    if code in _IMPLEMENTATIONS:
        return _IMPLEMENTATIONS[code]
    definition = STRATEGIES[code]
    return NotImplementedStrategy(code, definition.display_name)


def implementation_status() -> dict[ThesisType, StrategyStatus]:
    _register_implementations()
    return {
        code: (
            StrategyStatus.IMPLEMENTED
            if code in _IMPLEMENTATIONS
            else definition.status
        )
        for code, definition in STRATEGIES.items()
    }


def validate_registry() -> list[str]:
    """启动自检：返回问题列表（空表示通过）。

    检查项（对应 INV-TT1 / INV-TT2 / docs/04 §3）::

    1. 每个策略的失效条件非空（规格 §58 原则 5）
    2. status=implemented 的策略必须有实现类（INV-TT2）
    3. 正向权重合计 = 0.95，且风险不在正向权重表内
    4. 核心条件权重合计 = 1.0
    5. 风险因素权重合计 = 1.0
    6. 所有 RiskTrigger.when 必须存在于规则层的判别表中
    7. implemented 策略必须有核心条件、待确认模板、催化剂阶梯
    """
    from app.engine.rules import RISK_TRIGGER_PREDICATES  # 延迟导入避免循环

    problems: list[str] = []
    _register_implementations()

    for code, definition in STRATEGIES.items():
        name = definition.display_name

        # 1. 失效条件非空
        if not definition.invalidating_events:
            problems.append(f"[{name}] invalidating_events 为空（违反 INV-TT1）")

        # 3. 正向权重
        total = sum(definition.default_weights.values())
        if abs(total - POSITIVE_WEIGHT_TOTAL) > 1e-9:
            problems.append(f"[{name}] 正向权重合计 {total:.4f} ≠ {POSITIVE_WEIGHT_TOTAL}")
        if any(v < 0 for v in definition.default_weights.values()):
            problems.append(f"[{name}] 存在负权重（风险必须是扣分项，不在权重表内）")

        # 4 / 5. 条件与风险权重
        if abs(definition.condition_weight_total - 1.0) > 1e-9:
            problems.append(
                f"[{name}] 核心条件权重合计 {definition.condition_weight_total:.4f} ≠ 1.0"
            )
        if definition.risk_factors:
            if abs(definition.risk_weight_total - 1.0) > 1e-9:
                problems.append(
                    f"[{name}] 风险因素权重合计 {definition.risk_weight_total:.4f} ≠ 1.0"
                )

        # 6. 风险触发键必须可实现
        for factor in definition.risk_factors:
            for trigger in factor.triggers:
                if trigger.when not in RISK_TRIGGER_PREDICATES:
                    problems.append(
                        f"[{name}] 风险触发键 '{trigger.when}' 无对应判别函数"
                    )

        # 7. implemented 完整性
        if code in _IMPLEMENTATIONS:
            if not definition.core_conditions:
                problems.append(f"[{name}] implemented 但无核心条件")
            if not definition.open_question_templates:
                problems.append(f"[{name}] implemented 但无待确认模板（规格 §24）")
            if not definition.catalyst_ladder:
                problems.append(f"[{name}] implemented 但无催化剂阶梯")

    # 2. implemented 必须有实现类
    for code, definition in STRATEGIES.items():
        if definition.status == StrategyStatus.IMPLEMENTED and code not in _IMPLEMENTATIONS:
            problems.append(
                f"[{definition.display_name}] status=implemented 但无实现类（违反 INV-TT2）"
            )

    return problems


__all__ = [
    "GLOBAL_DEFAULT_WEIGHTS",
    "IMPLEMENTATION_ORDER",
    "POSITIVE_WEIGHT_TOTAL",
    "RISK_PENALTY_MAX",
    "STRATEGIES",
    "STRATEGY_TEMPLATES",
    "CatalystStage",
    "ConditionResult",
    "InvalidationHit",
    "NotImplementedStrategy",
    "Strategy",
    "StrategyDef",
    "StrategyEvaluation",
    "designed_types",
    "get_def",
    "get_strategy",
    "implementation_status",
    "implemented_types",
    "validate_registry",
    "weights_for",
]
