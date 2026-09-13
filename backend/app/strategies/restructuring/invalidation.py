"""``restructuring`` 策略的失效条件判定（规格 §23 / M5-03）。

**这是「逻辑失效」状态迁移的唯一依据。** 判定全部由规则完成，
LLM 只负责产出事件，不得直接改状态（docs/03 §2.2）。
"""

from __future__ import annotations

from app.engine import classifier
from app.facts import EventFact, StrategyFacts
from app.models.enums import EventType, InvalidationSeverity, ThesisType
from app.strategies.base import InvalidationHit
from app.strategies.registry import STRATEGIES


def rules() -> tuple:
    """本策略的失效条件定义（来自注册表，单一事实来源）。"""
    return STRATEGIES[ThesisType.RESTRUCTURING].invalidating_events


#: 交易**成功导致的上市终止**：换股吸收合并 / 私有化 / 主动退市。
#:
#: ★ 为什么需要：``_TERMINATE_WORDS`` 里的裸词「终止」会命中「终止**上市**」，
#: 而换股吸收合并里的「终止上市」讲的是**上市地位**，且是合并**成功**的结果。
#: 实测东兴证券、信达证券的「连续停牌直至终止上市、实施换股吸收合并」
#: 被判成「重组终止 / 重大资产重组失败」——判死方向完全反了。
_COMPLETION_CONTEXT = (
    "换股吸收合并", "吸收合并", "私有化", "主动退市", "要约收购",
)

#: 但「终止换股吸收合并」这类是**真的失败** —— 出现这些短语时前面的豁免不成立。
#: 豁免必须能被推翻，否则会漏掉真实的交易终止。
_DEAL_FAILURE_PHRASES = (
    "终止换股吸收合并", "终止吸收合并", "吸收合并终止", "合并终止",
    "终止筹划", "终止本次", "终止重组", "终止重大资产", "终止发行",
)


def is_completion_driven_delisting(title: str) -> bool:
    """标题里的「终止」是否在讲「交易成功 → 上市地位终止」而非交易失败。

    这是**确定性的标题模式识别**，与 ``subject_is_third_party`` 同类。
    两处调用（事件层粗判 / 机会层权威判定）复用本函数，避免两份关键词漂移。
    """
    title = title or ""
    if any(phrase in title for phrase in _DEAL_FAILURE_PHRASES):
        return False
    return any(marker in title for marker in _COMPLETION_CONTEXT)


#: 「已进入实质推进」的证据词 —— 出现它们说明不是早期苗头
_PROGRESS_KEYWORDS = (
    "预案", "报告书", "草案", "批复", "股东大会", "核准", "过户",
    "审核通过", "无条件通过",
)


def has_progress_evidence(facts: StrategyFacts) -> bool:
    """是否已经出现「进入实质推进」的证据。

    用于 ``early_only`` 规则：对已经推进到预案/草案的机会，
    收到问询函是常规流程，不该报警；对刚起步的苗头，监管关注往往是终止前兆。
    """
    return any(
        any(kw in (event.title or "") for kw in _PROGRESS_KEYWORDS)
        for event in facts.events
    )


def _matches(event: EventFact, definition) -> tuple[bool, str]:
    """事件是否命中某条失效条件。"""
    if event.event_type != definition.event_type:
        return False, ""

    title = event.title or ""

    # ★ 失效判定必须与正向信号用**同一套主体判定**。
    #   否则会出现不对称：控股股东自己的重整被撤回不会给 C1 加分，
    #   却足以把上市公司的卡片判死（实测三安光电）。
    if classifier.subject_is_third_party(title):
        return False, ""

    # ★ 「终止上市 + 换股吸收合并」是完成，不是失败（实测东兴证券 / 信达证券）
    if is_completion_driven_delisting(title):
        return False, ""

    # 排除词优先：命中任一排除词即不成立
    none_of = getattr(definition, "title_none_of", ())
    if none_of and any(kw in title for kw in none_of):
        return False, ""

    # AND 语义：标题必须同时包含全部关键词（应对「宣告公司破产」这类插词）
    all_of = getattr(definition, "title_all_of", ())
    if all_of and not all(kw in title for kw in all_of):
        return False, ""

    # 标题关键词（若定义了）
    if definition.title_contains:
        hit_kw = [kw for kw in definition.title_contains if kw in event.title]
        if not hit_kw:
            return False, ""
        return True, f"标题包含 {'/'.join(hit_kw)}"

    # 金额阈值（若定义了）
    if definition.amount_ratio_gt is not None:
        if event.amount_ratio > definition.amount_ratio_gt:
            return True, f"金额占比 {event.amount_ratio:.2f} > {definition.amount_ratio_gt:.2f}"
        return False, ""

    # 只按事件类型匹配
    return True, "事件类型匹配"


def detect(facts: StrategyFacts) -> tuple[InvalidationHit, ...]:
    hits: list[InvalidationHit] = []
    early = not has_progress_evidence(facts)
    for event in facts.events:
        for definition in rules():
            # early_only 规则只对「尚无进展证据」的机会生效
            if getattr(definition, "early_only", False) and not early:
                continue
            ok, reason = _matches(event, definition)
            if ok:
                hits.append(
                    InvalidationHit(
                        event_id=event.id,
                        event_type=event.event_type,
                        severity=definition.severity.value,
                        rule_description=definition.description,
                        reason=reason,
                    )
                )
    return tuple(hits)


def is_terminal(hits: tuple[InvalidationHit, ...]) -> bool:
    return any(h.severity == InvalidationSeverity.TERMINAL.value for h in hits)


def warning_hits(hits: tuple[InvalidationHit, ...]) -> tuple[InvalidationHit, ...]:
    """仅「预警」级别的命中 —— 只提醒，不改状态。

    ★ 早期待确认阶段收到监管关注时应该提醒，但不该判死：
    问询函不等于交易失败（规格 §23 的 severity 分级正为此存在）。
    """
    return tuple(h for h in hits if h.severity == InvalidationSeverity.WARNING.value)


def should_invalidate(hits: tuple[InvalidationHit, ...]) -> bool:
    """是否应把机会迁移到 ``invalidated``。

    规格 §20：出现事件**直接破坏**原投资逻辑时进入失效。
    因此只有 ``terminal`` 与 ``severe`` 触发迁移，``warning`` 只记 Alert。
    """
    return any(
        h.severity in (InvalidationSeverity.TERMINAL.value, InvalidationSeverity.SEVERE.value)
        for h in hits
    )


__all__ = ["detect", "has_progress_evidence", "is_completion_driven_delisting",
           "is_terminal", "rules", "should_invalidate", "warning_hits"]
