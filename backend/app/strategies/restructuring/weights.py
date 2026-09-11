"""``restructuring`` 策略的权重与风险因素访问（docs/04 §3.1 / §4.8，docs/06 §3）。

策略级权重覆盖的意义：**同一个事实（基本面弱）在不同策略下的符号是相反的。**
对重组预期策略，基本面弱是该逻辑的前提，因此 ``FUNDAMENTALS`` 权重从 0.10 降到 0.05，
让出的权重给 ``EVENT_CATALYST``；「基本面较弱」改为进入 ``RISK`` 扣分
—— 这与规格 §13 把「公司基本面较弱」放在**扣分侧**一致。
"""

from __future__ import annotations

from app.models.enums import ScoreDimension, ThesisType
from app.strategies.registry import (
    GLOBAL_DEFAULT_WEIGHTS,
    POSITIVE_WEIGHT_TOTAL,
    RISK_PENALTY_MAX,
    get_def,
    weights_for,
)


def weights() -> dict[ScoreDimension, float]:
    return weights_for(ThesisType.RESTRUCTURING)


def risk_factors() -> tuple:
    return get_def(ThesisType.RESTRUCTURING).risk_factors


def selfcheck() -> list[str]:
    """返回问题列表（空表示通过）。供启动自检与单测使用。"""
    problems: list[str] = []
    w = weights()

    if abs(sum(w.values()) - POSITIVE_WEIGHT_TOTAL) > 1e-9:
        problems.append(
            f"正向权重合计 {sum(w.values()):.4f} ≠ {POSITIVE_WEIGHT_TOTAL}"
        )
    if w.get(ScoreDimension.FUNDAMENTALS) != 0.05:
        problems.append("FUNDAMENTALS 权重应为 0.05（重组策略的关键覆盖）")
    if w.get(ScoreDimension.EVENT_CATALYST) != 0.25:
        problems.append("EVENT_CATALYST 权重应为 0.25（重组策略的关键覆盖）")
    if ScoreDimension.RISK in w:
        problems.append("RISK 不应出现在正向权重表（它是扣分项）")

    factors = risk_factors()
    total = sum(f.weight for f in factors)
    if abs(total - 1.0) > 1e-9:
        problems.append(f"风险因素权重合计 {total:.4f} ≠ 1.0")
    if RISK_PENALTY_MAX != 15.0:
        problems.append("RISK_PENALTY_MAX 应为 15.0（规格 §12 的风险因素 −15%）")

    # 覆盖后的权重不得出现负值，也不得遗漏维度
    for dim, value in w.items():
        if value < 0:
            problems.append(f"维度 {dim.value} 权重为负：{value}")
    if set(w) != set(GLOBAL_DEFAULT_WEIGHTS):
        missing = set(GLOBAL_DEFAULT_WEIGHTS) - set(w)
        problems.append(f"权重表缺少维度：{[d.value for d in missing]}")

    return problems


__all__ = ["GLOBAL_DEFAULT_WEIGHTS", "RISK_PENALTY_MAX", "risk_factors", "selfcheck", "weights"]
