"""``restructuring`` 重组预期 —— 本轮唯一完整实现的策略（D13）。

模块结构（对应 docs/03 §9 的目录约定）::

    rules.py         核心条件判定 + 催化剂阶梯
    invalidation.py  失效条件判定（逻辑失效状态的唯一依据）
    weights.py       维度权重覆盖 + 风险因素
    questions.py     「待确认」事项的可判定实现
    thesis.py        Thesis 语句与 Why Now

**注意**：本 ``__init__`` 刻意不导入 ``rules``，以避免与
``app.strategies`` 的加载器形成循环导入。
"""

from app.models.enums import ThesisType

CODE = ThesisType.RESTRUCTURING
DISPLAY_NAME = "重组预期"

__all__ = ["CODE", "DISPLAY_NAME"]
