"""策略全景：每类策略当前「有多少卡、为什么没有卡」。

## 为什么需要它

实现了 10 类策略，但某次采集只出 4 类卡 —— 用户看到的是「剩下 6 类没加入」，
而真相可能是「这批候选公司里没有符合那 6 类逻辑的标的」。
**系统不解释，用户就只能认为它没实现。**

所以每类策略都要给出一个可核对的数字：

    成长        0 张卡   最高覆盖率 0.30（门槛 0.35）→ 逻辑强度不足
    价值发现    3 张卡   最高覆盖率 0.47
    技术突破    0 张卡   本次候选池里没有相关公告 → 覆盖率 0.18

这样「没卡」也变成一个**有依据的结论**，而不是一片空白。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.enums import ThesisType
from app.models.knowledge import Company
from app.models.opportunity import Opportunity
from app.models.thesis import Thesis
from app.pipeline import facts_builder
from app.pipeline.opportunity_builder import MIN_COVERAGE, MIN_MATCH
from app.strategies import get_strategy, implemented_types
from app.strategies.registry import get_def
from app.engine.scoring import compute_rule_score, profile_weight_ratio


def _explain(coverage: float, match: float, weight_ratio: float) -> str:
    """为什么这一类没有卡 —— 说清卡在哪一级。"""
    if weight_ratio <= 0:
        return "当前画像未关注该策略（权重为 0）—— 可在画像页调整"
    if coverage < MIN_COVERAGE:
        return (
            f"本次候选公司的最高逻辑覆盖率 {coverage:.2f}，低于建卡门槛 "
            f"{MIN_COVERAGE} —— 没有符合该逻辑的标的"
        )
    if match < MIN_MATCH:
        return (
            f"覆盖率达标（{coverage:.2f}）但匹配度 {match:.0f} 低于门槛 "
            f"{MIN_MATCH:.0f} —— 该策略在你的画像里权重偏低"
        )
    return "有符合条件的标的，但本次未生成卡片（请重跑采集）"


def strategy_overview(
    session: Session, profile_id: int, weights: dict[str, float]
) -> list[dict]:
    """每类已实现策略的卡片数与诊断信息。

    ``best_coverage`` 是对**画像候选公司**逐一求值取最大值 ——
    不做 LLM 调用，纯规则计算，单用户本地跑几十家公司开销可忽略。
    """
    # 卡片数（按 thesis_type 聚合）
    cards: dict[str, int] = {}
    rows = session.exec(
        select(Thesis.thesis_type, Opportunity.id)
        .join(Opportunity, Opportunity.thesis_id == Thesis.id)  # type: ignore[arg-type]
        .where(Opportunity.profile_id == profile_id)
    ).all()
    for thesis_type, _ in rows:
        key = str(thesis_type)
        cards[key] = cards.get(key, 0) + 1

    # 诊断用的候选公司（有财务数据或事件的都算 —— 与 runner 的跳过条件一致）
    companies = list(session.exec(select(Company)).all())

    out: list[dict] = []
    for code in implemented_types():
        strategy = get_strategy(code)
        ratio = profile_weight_ratio(weights, code.value)
        count = cards.get(code.value, 0)

        best_coverage = 0.0
        best_match = 0.0
        best_company: str | None = None
        if count == 0:
            for company in companies:
                facts = facts_builder.build_strategy_facts(session, int(company.id or 0))
                if facts.financials.periods_with_data == 0 and not facts.events:
                    continue
                evaluation = strategy.evaluate(facts)
                if evaluation.coverage > best_coverage:
                    best_coverage = evaluation.coverage
                    best_company = company.name
                    best_match = compute_rule_score(
                        facts, code.value, ratio, evaluation=evaluation
                    ).match_score

        out.append({
            "thesis_type": code.value,
            "display_name": get_def(code).display_name,
            "card_count": count,
            "weight": round(float(weights.get(code.value, 0.0)), 4),
            "weight_ratio": round(ratio, 4),
            **(
                {"best_coverage": round(best_coverage, 4),
                 "best_match": round(best_match, 1),
                 "best_company": best_company,
                 "reason": _explain(best_coverage, best_match, ratio)}
                if count == 0 else {}
            ),
        })
    return out


def inactive_strategies(
    session: Session, profile_id: int, weights: dict[str, float]
) -> list[dict]:
    """画像里权重为 0 的已实现策略 —— 它们**永远不会出卡**，必须显式告知。"""
    return [
        item for item in strategy_overview(session, profile_id, weights)
        if item["weight"] <= 0
    ]


__all__ = ["inactive_strategies", "strategy_overview"]
