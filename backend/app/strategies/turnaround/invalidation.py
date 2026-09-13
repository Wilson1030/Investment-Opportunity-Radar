"""``turnaround`` 策略的失效条件判定（规格 §23 / docs/06 §4）。

**这是「困境反转」逻辑失效的唯一依据。** 判定全部由规则完成，
LLM 只负责产出事件，不得直接改状态（docs/03 §2.2）。

与 ``restructuring`` 的关键差别：
  · 重组失效看的是**事件**（交易终止 / 法院驳回）
  · 反转失效看的是**经营数据的反向变化**（现金流又转负 / 亏损扩大 / 毛利率重新恶化）
    —— 所以本模块同时看**事件**与**财务事实**两条腿。

共用两个与主体/语义相关的判定（与 restructuring 同一份实现，避免漂移）：
  · ``classifier.subject_is_third_party``  —— 子公司/控股股东的经营变化不是母公司的反转
  · ``is_completion_driven_delisting``     —— 「终止上市 + 换股吸收合并」是完成不是失败
"""

from __future__ import annotations

from app.engine import classifier
from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, InvalidationSeverity, ThesisType
from app.strategies.base import InvalidationHit
from app.strategies.registry import STRATEGIES
from app.strategies.restructuring.invalidation import is_completion_driven_delisting


def rules() -> tuple:
    """本策略的失效条件定义（来自注册表，单一事实来源）。"""
    return STRATEGIES[ThesisType.TURNAROUND].invalidating_events


#: 「改善已被确认」的证据词 —— 出现它们说明改善不再是单季度的苗头
_CONFIRMED_IMPROVEMENT = (
    "年报", "年度报告", "审计报告", "业绩快报", "扭亏为盈", "净利润为正",
)


def has_confirmed_improvement(facts: StrategyFacts) -> bool:
    """改善是否已经被**正式披露确认**（而不是单季数据）。

    用于 ``early_only`` 类规则：对只有单季改善的苗头，费用/减值类公告可能是
    一次性扰动；对已被年报确认的改善，同样的公告才是真正的风险。
    """
    return any(
        any(kw in (event.title or "") for kw in _CONFIRMED_IMPROVEMENT)
        for event in facts.events
    )


def _matches(event: EventFact, definition) -> tuple[bool, str]:
    """事件是否命中某条失效条件。"""
    if event.event_type != definition.event_type:
        return False, ""

    title = event.title or ""

    # 主体错位：子公司 / 控股股东自己的经营变化，不是母公司的反转
    if classifier.subject_is_third_party(title):
        return False, ""

    # 「终止上市 + 换股吸收合并」是完成，不是失败
    if is_completion_driven_delisting(title):
        return False, ""

    none_of = getattr(definition, "title_none_of", ())
    if none_of and any(kw in title for kw in none_of):
        return False, ""

    all_of = getattr(definition, "title_all_of", ())
    if all_of and not all(kw in title for kw in all_of):
        return False, ""

    if definition.title_contains:
        hit_kw = [kw for kw in definition.title_contains if kw in title]
        if not hit_kw:
            return False, ""
        return True, f"标题包含 {'/'.join(hit_kw)}"

    return True, "事件类型匹配"


def _financial_hits(facts: StrategyFacts) -> list[InvalidationHit]:
    """财务事实层面的失效 —— 反转策略特有的「第二条腿」。

    ★ 为什么必须有：反转逻辑的死法经常**不出现在公告里**。
    一家公司可以没有任何「终止」类公告，只是悄悄又亏了一个季度 ——
    只看事件的话，这个逻辑已经死了而雷达上还挂着。

    判定取自**最新一期**的方向事实，且要求确实有数据
    （``periods_with_data >= 2``）—— 数据不足时**不判失效**，
    因为「不知道」不等于「恶化」。
    """
    fin = facts.financials
    hits: list[InvalidationHit] = []
    if fin.periods_with_data < 2:
        return hits

    if fin.revenue_declining_quarters >= 2 and fin.margin_declining_quarters >= 2:
        hits.append(InvalidationHit(
            event_id=None,
            event_type=EventType.EARNINGS_TURNAROUND,
            severity=InvalidationSeverity.TERMINAL.value,
            rule_description="营收与毛利率同时重新转弱（连续 ≥2 期）",
            reason=(
                f"最近连续下降期数：营收 {fin.revenue_declining_quarters}、"
                f"毛利率 {fin.margin_declining_quarters}"
            ),
        ))
    elif fin.margin_declining_quarters >= 2:
        hits.append(InvalidationHit(
            event_id=None,
            event_type=EventType.EARNINGS_TURNAROUND,
            severity=InvalidationSeverity.SEVERE.value,
            rule_description="毛利率重新恶化（连续 ≥2 期）",
            reason=f"最近连续 {fin.margin_declining_quarters} 期毛利率同比下降",
        ))
    return hits


def detect(facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
    hits: list[InvalidationHit] = []
    for event in facts.events:
        for definition in rules():
            if getattr(definition, "early_only", False) and has_confirmed_improvement(facts):
                continue
            ok, reason = _matches(event, definition)
            if ok:
                hits.append(InvalidationHit(
                    event_id=event.id,
                    event_type=event.event_type,
                    severity=definition.severity.value,
                    rule_description=definition.description,
                    reason=reason,
                ))
    hits.extend(_financial_hits(facts))
    return tuple(hits)


def is_terminal(hits: tuple[InvalidationHit, ...]) -> bool:
    return any(h.severity == InvalidationSeverity.TERMINAL.value for h in hits)


def warning_hits(hits: tuple[InvalidationHit, ...]) -> tuple[InvalidationHit, ...]:
    """仅「预警」级别的命中 —— 只提醒，不改状态。"""
    return tuple(h for h in hits if h.severity == InvalidationSeverity.WARNING.value)


def should_invalidate(hits: tuple[InvalidationHit, ...]) -> bool:
    """是否应把机会迁移到 ``invalidated``。

    与 restructuring 同规则：只有 ``terminal`` / ``severe`` 触发迁移，
    ``warning`` 只记 Alert（规格 §23 的 severity 分级）。
    """
    return any(
        h.severity in (InvalidationSeverity.TERMINAL.value, InvalidationSeverity.SEVERE.value)
        for h in hits
    )


__all__ = [
    "detect",
    "has_confirmed_improvement",
    "is_terminal",
    "rules",
    "should_invalidate",
    "warning_hits",
]
