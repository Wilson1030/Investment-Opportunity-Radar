"""规则层（确定性逻辑）。

规格 §28 的分工：**确定性的事情尽量交给规则和程序；需要理解语义的事情交给 LLM。**

本包内的模块全部是纯函数或接受注入依赖的函数，不调用 LLM。
"""

from app.engine import classifier, freshness, funnel, guard, rules, scope, scoring

__all__ = [
    "classifier",
    "freshness",
    "funnel",
    "guard",
    "rules",
    "scope",
    "scoring",
]
