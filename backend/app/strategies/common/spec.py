"""``StrategySpec`` + ``RuleBasedStrategy`` —— 把共享机制组装成一个策略。

每类策略只需要声明**差异**：

  · ``conditions``   核心条件求值函数（C1…Cn）
  · ``ladder``       催化阶梯（可按事件标题、也可按财务数据）
  · ``narrative``    叙事三要素
  · ``markers``      待确认事项的确认依据
  · ``extra_hits``   策略特有的非事件失效判定（可选）
  · ``coverage_cap`` 顺序门控（必要条件缺失时压低覆盖率，可选）

其余全部由本模块统一处理 —— 包括三条**全局不变量**：

  1. **失效优先**：逻辑已失效时催化强度归零。
     否则卡片会出现「阶段：连续两季改善」+「状态：逻辑失效」的自相矛盾。
  2. **``coverage_cap`` 必须低于建卡门槛**（由测试断言，见
     ``test_strategy_conformance.py``）—— 上限比门槛高，门控就形同虚设。
  3. **``caveat`` 不得为空**：规格示例 B 禁止只有结论的 Thesis。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.facts import StrategyFacts
from app.models.enums import ThesisType
from app.strategies.base import (
    CatalystStage,
    ConditionResult,
    StrategyEvaluation,
)
from app.strategies.common import invalidation as invalidation_module
from app.strategies.common.evidence import own_subject_events
from app.strategies.common.narrative import Narrative
from app.strategies.common.narrative import build_statement as _statement
from app.strategies.common.narrative import why_now as _why_now
from app.strategies.common.questions import MarkerMap, open_questions
from app.strategies.registry import get_def

#: 条件求值函数
ConditionFn = Callable[[StrategyFacts], ConditionResult]
#: 数据驱动的阶梯钩子：返回 None 表示「本策略没有数据侧阶梯」
DataLadderFn = Callable[[StrategyFacts], CatalystStage | None]
#: 策略特有的附加失效判定
ExtraHitsFn = Callable[[StrategyFacts], list]
#: 顺序门控：按条件满足度决定覆盖率上限
CapFn = Callable[[dict[str, ConditionResult]], float | None]


@dataclass(frozen=True)
class LadderRung:
    """催化阶梯的一档：``keywords`` 命中任一事件标题即达成。"""

    stage: str
    score: float
    early: bool
    keywords: tuple[str, ...] = ()
    #: 事件类型范围（空 = 任意事件的标题都参与匹配）
    event_types: tuple = ()


@dataclass(frozen=True)
class StrategySpec:
    code: ThesisType
    conditions: tuple[ConditionFn, ...]
    narrative: Narrative
    #: 事件侧阶梯（按标题关键词）
    ladder: tuple[LadderRung, ...] = ()
    #: 数据侧阶梯（例：turnaround 的「连续两季改善」由财务数据判定）
    data_ladder: DataLadderFn | None = None
    #: 无法达成任何阶梯时的保守阶段
    fallback_stage: tuple[str, float, bool] = ("未出现相关迹象", 0.0, False)
    #: 参与阶梯匹配的事件类型（空 = 全部）
    ladder_event_types: tuple = ()
    markers: MarkerMap = field(default_factory=dict)
    unverifiable: dict[str, str] = field(default_factory=dict)
    extra_hits: ExtraHitsFn | None = None
    coverage_cap: CapFn | None = None


class RuleBasedStrategy:
    """把 :class:`StrategySpec` 组装成符合 ``Strategy`` 协议的策略。

    实例化后注册到 ``app.strategies._IMPLEMENTATIONS`` 即可参与机会生成。
    """

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec
        self.code = spec.code
        self.display_name = get_def(spec.code).display_name

    # ---------- 核心条件 ----------
    def evaluate(self, facts: StrategyFacts) -> StrategyEvaluation:
        results = tuple(fn(facts) for fn in self.spec.conditions)
        cap = None
        if self.spec.coverage_cap is not None:
            cap = self.spec.coverage_cap({c.key: c for c in results})
        return StrategyEvaluation(conditions=results, coverage_cap=cap)

    # ---------- 催化阶梯 ----------
    def is_early_signal(self, facts: StrategyFacts) -> bool:
        return self.catalyst_strength(facts).early

    def _ladder_events(self, facts: StrategyFacts):
        if self.spec.ladder_event_types:
            return own_subject_events(facts.events_of(*self.spec.ladder_event_types))
        return own_subject_events(facts.events)

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:
        """★ 失效优先 —— 这是全局不变量，写在共享层而不是各策略里。

        否则会出现「阶段：连续两季改善」+「状态：逻辑失效」的自相矛盾卡片，
        而用户看到的正是这两行。
        """
        hits = self.invalidation_hits(facts)
        if invalidation_module.should_invalidate(hits):
            return CatalystStage(
                "终止 / 失效", 0.0,
                f"命中失效条件：{hits[0].rule_description}",
                early=False,
            )

        best: CatalystStage | None = None

        if self.spec.data_ladder is not None:
            best = self.spec.data_ladder(facts)

        events = self._ladder_events(facts)
        for rung in self.spec.ladder:
            matched = [
                e for e in events
                if any(kw in (e.title or "") for kw in rung.keywords)
            ]
            if matched and (best is None or rung.score > best.score):
                best = CatalystStage(
                    rung.stage, rung.score,
                    f"依据：{(matched[0].title or matched[0].event_type.value)[:28]}",
                    early=rung.early,
                )

        if best is not None:
            return best
        stage, score, early = self.spec.fallback_stage
        return CatalystStage(stage, score, "未发现与该逻辑相关的进展", early=early)

    # ---------- 失效 / 待确认 / 叙事 ----------
    def invalidation_hits(self, facts: StrategyFacts) -> tuple:
        return invalidation_module.detect(
            facts, self.code, extra_hits=self.spec.extra_hits,
        )

    def open_questions(self, facts: StrategyFacts) -> tuple[str, ...]:
        return open_questions(facts, self.code, self.spec.markers, self.spec.unverifiable)

    def build_statement(self, facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
        return _statement(facts, evaluation, self.spec.narrative)

    def why_now(self, facts: StrategyFacts) -> dict[str, str]:
        return _why_now(facts, self.spec.narrative)


__all__ = [
    "CapFn",
    "ConditionFn",
    "DataLadderFn",
    "ExtraHitsFn",
    "LadderRung",
    "RuleBasedStrategy",
    "StrategySpec",
]
