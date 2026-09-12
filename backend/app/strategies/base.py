"""策略协议与评估结果（docs/06-策略设计总表）。

**硬约束（写在这里，不靠 prompt 提醒）**

* 策略只允许读取 ``StrategyFacts``，**不得**接受「标签 → 策略」的直接映射。
  规格 §5.6 示例 J：*投资策略应该决定股票为什么被发现，而不是股票标签决定投资策略*。
  因此本模块**刻意不提供** ``match_by_tag()`` 之类的接口（INV-C1，测试会扫描源码）。
* 顺序门控（docs/06 §14.2）：链路型策略（``policy`` / ``product``）在前置条件未命中时
  必须压低 ``coverage`` —— 由策略自身的 ``evaluate()`` 实现，评分层不参与。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, ThesisType


@dataclass(frozen=True)
class ConditionResult:
    """单个核心条件的满足程度。

    ``satisfaction`` 是 **0~1 的连续值**而非布尔值：
    规格 §17 示例的 ``94% MATCH`` 对应「C1 完全命中 + C2 完全命中 + C3 完全命中 +
    C4 部分满足（0.70）」，若只允许 bool，coverage 只能落在 0.80，无法表达 0.94。
    """

    key: str
    label: str
    weight: float
    satisfaction: float = 0.0          # 0~1
    detail: str = ""
    evidence_ids: tuple[int, ...] = ()

    @property
    def hit(self) -> bool:
        return self.satisfaction > 0.0

    @property
    def full(self) -> bool:
        return self.satisfaction >= 1.0


@dataclass(frozen=True)
class StrategyEvaluation:
    conditions: tuple[ConditionResult, ...]
    #: 顺序门控上限（docs/06 §14.2）：``policy`` 缺业务验证时压到 0.45，
    #: ``product`` 缺客户验证时把订单/收入条件计 0（由策略自身置 0，不走此字段）。
    coverage_cap: float | None = None

    @property
    def coverage(self) -> float:
        """加权满足度 ∈ [0, 1]，并应用顺序门控上限。"""
        total = sum(c.weight for c in self.conditions)
        if total <= 0:
            return 0.0
        raw = sum(c.weight * c.satisfaction for c in self.conditions) / total
        if self.coverage_cap is not None:
            raw = min(raw, self.coverage_cap)
        return raw

    @property
    def coverage_raw(self) -> float:
        """未归一化的加权满足度和（用于诊断）。"""
        return sum(c.weight * c.satisfaction for c in self.conditions)

    @property
    def hits(self) -> tuple[ConditionResult, ...]:
        return tuple(c for c in self.conditions if c.hit)

    @property
    def misses(self) -> tuple[ConditionResult, ...]:
        return tuple(c for c in self.conditions if not c.hit)


@dataclass(frozen=True)
class InvalidationHit:
    """命中了一条失效条件（规格 §23）。"""

    event_id: int
    event_type: EventType
    severity: str            # terminal / severe / warning
    rule_description: str
    reason: str


@dataclass(frozen=True)
class CatalystStage:
    stage: str
    score: float
    description: str
    #: 是否属于「早期苗头」（预重整 / 重整申请 / 法院受理 / 筹划停牌 / 意向协议）
    #:
    #: ★ 不能用「分数低」来代替这个标志：``存量｜重组已完成``阶段分数也很低（5），
    #: 但它是**已经完成**的事，不是早期。
    early: bool = False


@runtime_checkable
class Strategy(Protocol):
    """10 类策略各自实现本协议。"""

    code: ThesisType
    display_name: str

    def evaluate(self, facts: StrategyFacts) -> StrategyEvaluation:
        """核心条件命中情况 → 决定 ``coverage`` → 决定 ``match_score``。"""
        ...

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:
        """催化剂强度阶梯（docs/06 各策略的「催化剂强度阶梯」表）。"""
        ...

    def invalidation_hits(self, facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
        """扫描事件，返回命中的失效条件。"""
        ...

    def build_statement(self, facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
        """生成 Thesis 语句（规格 §21）。"""
        ...

    def open_questions(self, facts: StrategyFacts) -> tuple[str, ...]:
        """「待确认」模板（规格 §24）—— 必须具体，不能是「待确认」这个标签。"""
        ...

    def why_now(self, facts: StrategyFacts) -> dict[str, str]:
        """Why Now（规格 §52，固定字段）。"""
        ...


class NotImplementedStrategy:
    """``status=designed`` 的策略占位实现。

    设计全量、实现逐个（D13）：10 类策略的元数据在 ``registry.py`` 中已完整，
    但只有 ``restructuring`` 挂载真正的实现类。调用占位实现会显式抛错，
    而不是返回「0 分机会」这种静默错误。
    """

    def __init__(self, code: ThesisType, display_name: str) -> None:
        self.code = code
        self.display_name = display_name

    def _not_implemented(self) -> None:
        raise NotImplementedError(
            f"策略 {self.code.value} 尚未实现（status=designed）。"
            f"设计见 docs/06-策略设计总表.md；实现顺序见 docs/07 §P10。"
        )

    def evaluate(self, facts: StrategyFacts) -> StrategyEvaluation:  # pragma: no cover
        self._not_implemented()

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:  # pragma: no cover
        self._not_implemented()

    def invalidation_hits(self, facts: StrategyFacts) -> tuple[InvalidationHit, ...]:  # pragma: no cover
        self._not_implemented()

    def build_statement(self, facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:  # pragma: no cover
        self._not_implemented()

    def open_questions(self, facts: StrategyFacts) -> tuple[str, ...]:  # pragma: no cover
        self._not_implemented()

    def why_now(self, facts: StrategyFacts) -> dict[str, str]:  # pragma: no cover
        self._not_implemented()


__all__ = [
    "CatalystStage",
    "ConditionResult",
    "InvalidationHit",
    "NotImplementedStrategy",
    "Strategy",
    "StrategyEvaluation",
]
