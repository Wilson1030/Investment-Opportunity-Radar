"""``turnaround`` 策略的核心条件判定与催化剂阶梯（docs/06 §4）。

## 规格给的五个条件

| # | 条件 | 权重 |
|:---:|---|:---:|
| C1 | 过去有明确经营恶化（营收连续下降 / 净利润亏损 / 毛利率下降，≥2 期） | 0.20 |
| C2 | 最近出现经营改善迹象（营收恢复增长 / 毛利率连续 ≥2 季改善 / 现金流转正） | 0.30 |
| C3 | 改善有可归因的具体举措（剥离亏损业务 / 降债 / 管理层变化 / 成本改善 / 资产处置） | 0.20 |
| C4 | 行业或公司层面需求恢复（行业数据 / 订单恢复） | 0.15 |
| C5 | **不依赖 ST 标签**（明确允许非 ST 公司） | 0.15 |

## ⚠️ 本策略最重要的那条约束（规格示例 J）

> **投资策略应该决定股票为什么被发现，而不是股票标签决定投资策略。**

两个反例，本实现都不能犯：

  · 「困境反转必须找 ST」→ **C5 反向计分**：它检查的是「改善依据是否来自
    财务数据本身」，而不是「公司是不是 ST」。IS_ST **不出现在本文件的任何
    if 条件里**（由 ``tests/test_turnaround_strategy.py`` 用源码扫描守住）。
  · 「业绩大涨，所以看好」→ 必须形成 Thesis 语句并列出
    Supporting Evidence / Uncertainties / Invalidating Events 三段，
    不能只给一个分数。

## 为什么 C1 与 C2 必须能同时成立

它们方向相反：C1 问「**过去**有没有恶化」，C2 问「**最近**有没有改善」。
所以 C1 用 ``*_declining_run_max``（史上最长连续下降），
而不是「最近是否在下降」—— 后者会把一家正在改善的公司的历史恶化抹掉，
而那恰恰是「反转」成立的前提。

## C4 的诚实处理

当前数据源**没有行业数据**（只有公司公告与财务）。所以 C4 只承认
**公司层面**的需求恢复证据（订单/中标类公告 + 营收恢复增长），
并在 ``detail`` 里**明说行业数据未采集** ——
不能因为「没采到」就默认行业景气，也不能假装有行业判断。
"""

from __future__ import annotations

from app.engine import classifier
from app.facts import StrategyFacts
from app.models.enums import EventType, ReliabilityLevel, ThesisType
from app.strategies.base import CatalystStage, ConditionResult, StrategyEvaluation
from app.strategies.registry import get_def
from app.strategies.turnaround import invalidation

_CONDITIONS = get_def(ThesisType.TURNAROUND).core_conditions
_C1, _C2, _C3, _C4, _C5 = _CONDITIONS

#: 与「经营改善 / 需求恢复」相关的公告类型
_IMPROVEMENT_EVENTS = (
    EventType.EARNINGS_TURNAROUND,
    EventType.MAJOR_CONTRACT,
    EventType.NEW_PRODUCT,
)

#: 「改善有可归因的具体举措」的证据词（C3）
#: 对应规格列举的五类：剥离亏损业务 / 降债 / 管理层变化 / 成本改善 / 资产处置
_MEASURE_MARKERS: dict[str, tuple[str, ...]] = {
    "剥离亏损业务": ("剥离", "出售子公司", "转让子公司", "退出", "不再纳入合并"),
    "资产处置": ("资产处置", "处置资产", "出售资产", "转让资产"),
    "降债": ("降债", "债务重组", "债务和解", "减少负债", "降低负债", "清偿"),
    "管理层变化": ("董事长", "总经理", "财务总监", "高管", "管理层", "聘任", "辞职"),
    "成本改善": ("降本", "成本改善", "费用下降", "降本增效", "精简"),
}

#: 「需求恢复」的公司层面证据词（C4）
_DEMAND_MARKERS = ("中标", "订单", "合同", "采购", "产能", "销量增长", "需求恢复")


def _best_level(events: tuple) -> ReliabilityLevel | None:
    order = [ReliabilityLevel.A, ReliabilityLevel.B, ReliabilityLevel.C,
             ReliabilityLevel.D, ReliabilityLevel.E]
    levels = [e.evidence_level for e in events if e.evidence_level is not None]
    for level in order:
        if level in levels:
            return level
    return None


def _evidence_of(events: tuple) -> tuple[int, ...]:
    seen: list[int] = []
    for event in events:
        for evidence_id in event.evidence_ids:
            if evidence_id not in seen:
                seen.append(evidence_id)
    return tuple(seen)


def _own_subject_events(events: tuple) -> tuple:
    """只保留**主体是上市公司本身**的事件。

    与 restructuring 同一套主体判定（复用 ``subject_is_third_party``）：
    子公司的经营改善不是母公司的困境反转。
    """
    return tuple(e for e in events if not classifier.subject_is_third_party(e.title))


# --------------------------------------------------------------------------- #
# C1 过去有明确经营恶化
# --------------------------------------------------------------------------- #
def _condition_c1(facts: StrategyFacts) -> ConditionResult:
    """C1 过去有明确经营恶化（≥2 期）。

    ★ 用 ``*_declining_run_max``（史上最长连续下降）而不是「最近是否在下降」：
    反转的前提是「**曾经**恶化过」。一家连续改善了 3 个季度的公司，
    最近当然不在下降 —— 但它的历史恶化必须被算进来，否则「反转」无从谈起。
    """
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(
            _C1.key, _C1.label, _C1.weight, 0.0,
            f"财务期数不足（{fin.periods_with_data} 期），无法判断经营恶化",
        )

    signals: list[str] = []
    if fin.revenue_declining_run_max >= 2:
        signals.append(f"营收曾连续 {fin.revenue_declining_run_max} 期同比下降")
    if fin.margin_declining_run_max >= 2:
        signals.append(f"毛利率曾连续 {fin.margin_declining_run_max} 期同比下降")
    if fin.loss_run_max >= 2:
        signals.append(f"曾连续 {fin.loss_run_max} 期净利润为负")

    if len(signals) >= 2:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 1.0,
                               "；".join(signals[:3]))
    if len(signals) == 1:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.60, signals[0])
    # 单期亏损也算部分恶化，但不足以支撑「明确恶化 ≥2 期」
    if fin.loss_years == 1:
        return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.25, "最近一期亏损（不足 2 期）")
    return ConditionResult(_C1.key, _C1.label, _C1.weight, 0.0, "未见连续经营恶化")


# --------------------------------------------------------------------------- #
# C2 最近出现经营改善迹象
# --------------------------------------------------------------------------- #
def _condition_c2(facts: StrategyFacts) -> ConditionResult:
    """C2 最近出现经营改善迹象（权重最高，0.30）。"""
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(
            _C2.key, _C2.label, _C2.weight, 0.0,
            f"财务期数不足（{fin.periods_with_data} 期），无法判断改善",
        )

    strong: list[str] = []
    weak: list[str] = []

    if fin.revenue_improving_quarters >= 2:
        strong.append(f"营收连续 {fin.revenue_improving_quarters} 期同比增长")
    elif fin.revenue_improving_quarters == 1:
        weak.append("营收单期同比增长")

    if fin.margin_improving_quarters >= 2:
        strong.append(f"毛利率连续 {fin.margin_improving_quarters} 期改善")
    elif fin.margin_improving_quarters == 1:
        weak.append("毛利率单期改善")

    if fin.ocf_turned_positive:
        strong.append("经营现金流转正")
    elif fin.ocf_improving:
        weak.append("经营现金流同比改善")

    # 事件层面的改善（业绩预告上调 / 订单）
    events = _own_subject_events(facts.events_of(EventType.EARNINGS_TURNAROUND))
    improving_events = tuple(
        e for e in events
        if any(kw in (e.title or "") for kw in ("预增", "扭亏", "上调", "增长", "改善"))
    )
    if improving_events:
        strong.append(f"业绩类公告显示改善（{improving_events[0].title[:24]}）")

    if len(strong) >= 2:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 1.0,
                               "；".join(strong[:3]), _evidence_of(improving_events))
    if len(strong) == 1:
        detail = strong[0] + ("；另有 " + "、".join(weak) if weak else "")
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.65, detail,
                               _evidence_of(improving_events))
    if weak:
        return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.35,
                               "；".join(weak) + "（均不足 2 期，尚不构成连续改善）")
    return ConditionResult(_C2.key, _C2.label, _C2.weight, 0.0, "未见经营改善迹象")


# --------------------------------------------------------------------------- #
# C3 改善有可归因的具体举措
# --------------------------------------------------------------------------- #
def _condition_c3(facts: StrategyFacts) -> ConditionResult:
    """C3 改善有可归因的具体举措。

    没有举措支撑的改善，可能只是行业普涨或一次性因素 ——
    所以「归因」这一条本身就是**给改善打折扣**的机制。
    """
    events = _own_subject_events(facts.events)
    found: list[str] = []
    evidence: list[int] = []
    for label, markers in _MEASURE_MARKERS.items():
        matched = tuple(
            e for e in events if any(m in (e.title or "") for m in markers)
        )
        if matched:
            found.append(f"{label}（{matched[0].title[:22]}）")
            evidence.extend(_evidence_of(matched[:1]))

    # 降债也可以从财务侧确认：资产负债率在下降
    if not facts.financials.debt_ratio_rising and facts.financials.periods_with_data >= 2:
        found.append("资产负债率未上升")

    if len(found) >= 2:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 1.0,
                               "；".join(found[:3]), tuple(evidence))
    if len(found) == 1:
        return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.50, found[0],
                               tuple(evidence))
    return ConditionResult(_C3.key, _C3.label, _C3.weight, 0.0, "未见可归因的改善举措")


# --------------------------------------------------------------------------- #
# C4 行业或公司层面需求恢复
# --------------------------------------------------------------------------- #
def _condition_c4(facts: StrategyFacts) -> ConditionResult:
    """C4 行业或公司层面需求恢复。

    ★ **诚实边界**：当前数据源没有行业数据。所以这里只承认**公司层面**证据，
    并在 detail 里明说「行业数据未采集」——
    不能因为没采到就默认行业景气，更不能假装有行业判断。
    """
    events = _own_subject_events(
        facts.events_of(EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT)
    )
    demand_events = tuple(
        e for e in events if any(m in (e.title or "") for m in _DEMAND_MARKERS)
    )
    revenue_recovering = facts.financials.revenue_improving_quarters >= 1

    note = "（行业层面数据当前未采集，仅按公司层面证据判断）"
    if demand_events and revenue_recovering:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.85,
                               f"订单/中标类公告 + 营收恢复增长{note}",
                               _evidence_of(demand_events))
    if demand_events:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.55,
                               f"存在订单/中标类公告{note}", _evidence_of(demand_events))
    if revenue_recovering:
        return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.30,
                               f"仅营收出现恢复迹象{note}")
    return ConditionResult(_C4.key, _C4.label, _C4.weight, 0.0,
                           f"未见需求恢复证据{note}")


# --------------------------------------------------------------------------- #
# C5 不依赖 ST 标签（★ 反向计分）
# --------------------------------------------------------------------------- #
def _condition_c5(facts: StrategyFacts, c2: ConditionResult) -> ConditionResult:
    """C5 **不依赖 ST 标签** —— 刻意反向计分（规格示例 J）。

    ★ 这一条检查的是「**改善依据是否来自经营数据本身**」，
    **不是**「公司是不是 ST」。``is_st`` 不出现在本函数的任何判定里
    （由 ``tests/test_turnaround_strategy.py`` 的源码扫描守住 INV-C1）。

    为什么要有这么一条：如果反转策略只能靠 ST 名单找标的，
    那它就不是一个策略，而是一个标签筛选器 —— 而标签会失效
    （摘帽、新增 ST、标签滞后），策略不应该跟着标签一起失效。
    """
    fin = facts.financials
    if fin.periods_with_data < 2:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                               "财务期数不足，无法确认改善来自经营数据")
    if c2.satisfaction >= 0.65:
        return ConditionResult(
            _C5.key, _C5.label, _C5.weight, 1.0,
            "改善依据来自财务数据与公告，与 ST 标签无关（非 ST 公司同样成立）",
        )
    if c2.satisfaction > 0.0:
        return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.50,
                               "改善迹象偏弱，尚不能确认它独立于标签")
    return ConditionResult(_C5.key, _C5.label, _C5.weight, 0.0,
                           "无经营数据支撑的改善")


# --------------------------------------------------------------------------- #
# 催化剂阶梯
# --------------------------------------------------------------------------- #
#: (阶段名, 分数, 是否早期)
#:
#: 规格给的阶梯是「单季改善 40 / 连续两季改善 70 / 业绩预告上调 85 / 年报确认 95」。
#: 前两档由**财务数据**驱动、后两档由**公告**驱动 —— 与 restructuring
#: 纯看公告标题的阶梯不同，这里两条腿都要走。
_LADDER_FINANCIAL: tuple[tuple[str, float, bool], ...] = (
    # ★ 单季改善刻意标为「早期」：规格自己的待确认模板第一条就是
    #   「改善是否具有持续性」—— 策略本身承认单季改善尚未被证实。
    #   不标的话，用户会把一个季度的好转当成趋势（规格 §24 / §38）。
    ("单季改善", 40.0, True),
    ("连续两季改善", 70.0, False),
)

#: 只有**弱**信号时的阶段（现金流同比改善但仍为负 / 未转正）。
#:
#: ★ 为什么必须有这一档 —— 实测踩到的不一致：
#: 三安光电的营收与毛利率都没改善，只有经营现金流「同比改善」（仍为正），
#: C2 因此给了 0.35（弱证据，合理），但阶梯里没有对应档位，
#: 于是卡片显示「**未出现改善迹象**」却仍然建了一张困境反转卡 ——
#: 阶段与卡片类型自相矛盾。加这一档后阶段变成「弱改善迹象」，
#: 并标为早期：现金流改善而收入没动，还谈不上反转。
_WEAK_STAGE = ("弱改善迹象", 20.0, True)

_LADDER_EVENT: tuple[tuple[str, float, bool, tuple[str, ...]], ...] = (
    ("业绩预告上调", 85.0, False, ("预增", "扭亏", "上调", "上修", "业绩快报")),
    ("年报确认", 95.0, False, ("年度报告", "年报", "审计报告", "确认")),
)


class TurnaroundStrategy:
    """``turnaround`` 困境反转实现类。"""

    code = ThesisType.TURNAROUND
    display_name = "困境反转"

    def evaluate(self, facts: StrategyFacts) -> StrategyEvaluation:
        c1 = _condition_c1(facts)
        c2 = _condition_c2(facts)
        return StrategyEvaluation(
            conditions=(
                c1,
                c2,
                _condition_c3(facts),
                _condition_c4(facts),
                _condition_c5(facts, c2),
            ),
            coverage_cap=self._coverage_cap(c1, c2),
        )

    @staticmethod
    def _coverage_cap(c1: ConditionResult, c2: ConditionResult) -> float | None:
        """顺序门控（docs/06 §14.2）：两条**必要条件**缺失时压低覆盖率。

        ★ 为什么需要它 —— 实测踩到的漏洞：
        南网能源「过去从未连续恶化」（C1 = 0.00）却拿到 **0.49** coverage，
        足以越过 0.35 的建卡门槛。于是它会作为一张**「困境反转」卡**出现，
        而它根本没有「困境」。这不是打分偏保守或偏激进的问题，
        是**用错的逻辑去解释一家公司**。

        「先恶化」与「后改善」都是困境反转的**必要条件**，
        缺任一条就不是这个策略要描述的现象（应由增长/景气类策略覆盖）：
          · C1 = 0 → 没有困境，谈不上反转
          · C2 = 0 → 没有改善，只是还在恶化

        上限 0.30 是**刻意低于**建卡门槛 ``MIN_COVERAGE = 0.35`` 的
        （``tests/test_turnaround_strategy.py`` 会断言这个大小关系，
        所以门槛若被调整，测试会立刻发现这里的假设失效）。
        """
        if c1.satisfaction == 0.0 or c2.satisfaction == 0.0:
            return 0.30
        return None

    # ---------- 催化剂阶梯 ----------
    def is_early_signal(self, facts: StrategyFacts) -> bool:
        return self.catalyst_strength(facts).early

    def catalyst_strength(self, facts: StrategyFacts) -> CatalystStage:
        """阶段取「财务驱动」与「公告驱动」两条腿里**较高**的那个。

        ★ 失效优先：与 restructuring 一样，先问失效引擎。
        逻辑已经失效的机会不该还挂着「连续两季改善」的阶段 ——
        否则卡片上会出现「阶段：连续两季改善」+「状态：逻辑失效」的自相矛盾。
        """
        hits = invalidation.detect(facts)
        if invalidation.should_invalidate(hits):
            return CatalystStage(
                "终止 / 失效", 0.0,
                f"命中失效条件：{hits[0].rule_description}",
                early=False,
            )

        fin = facts.financials
        best: CatalystStage | None = None

        for stage, score, early in _LADDER_FINANCIAL:
            reached = (
                stage == "连续两季改善"
                and (fin.revenue_improving_quarters >= 2 or fin.margin_improving_quarters >= 2)
            ) or (
                stage == "单季改善"
                and (fin.revenue_improving_quarters >= 1 or fin.margin_improving_quarters >= 1
                     or fin.ocf_turned_positive)
            )
            if reached:
                best = CatalystStage(stage, score, "依据：财务数据的连续改善", early=early)

        # 弱信号档位（收入与毛利率都没改善，只有现金流同比改善）
        if best is None and fin.ocf_improving:
            stage, score, early = _WEAK_STAGE
            best = CatalystStage(
                stage, score,
                "依据：仅经营现金流同比改善，营收与毛利率尚未改善",
                early=early,
            )

        events = _own_subject_events(facts.events_of(*_IMPROVEMENT_EVENTS))
        for stage, score, early, keywords in _LADDER_EVENT:
            matched = [e for e in events if any(kw in (e.title or "") for kw in keywords)]
            if matched and (best is None or score > best.score):
                best = CatalystStage(stage, score, f"依据：{matched[0].title[:28]}", early=early)

        if best is not None:
            return best
        return CatalystStage(
            "未出现改善迹象", 0.0,
            "财务数据未显示连续改善，也没有业绩改善类公告",
        )

    # ---------- 失效 / 待确认 / 叙事 ----------
    def invalidation_hits(self, facts: StrategyFacts) -> tuple:
        return invalidation.detect(facts)

    def build_statement(self, facts: StrategyFacts, evaluation: StrategyEvaluation) -> str:
        from app.strategies.turnaround import thesis

        return thesis.build_statement(facts, evaluation)

    def open_questions(self, facts: StrategyFacts) -> tuple[str, ...]:
        from app.strategies.turnaround import questions

        return questions.open_only(facts)

    def why_now(self, facts: StrategyFacts) -> dict[str, str]:
        from app.strategies.turnaround import thesis

        return thesis.why_now(facts)


STRATEGY = TurnaroundStrategy()

__all__ = [
    "STRATEGY",
    "TurnaroundStrategy",
    "_condition_c1",
    "_condition_c2",
    "_condition_c3",
    "_condition_c4",
    "_condition_c5",
]
