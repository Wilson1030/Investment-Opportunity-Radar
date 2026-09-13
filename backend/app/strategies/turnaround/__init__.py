"""``turnaround`` 困境反转 —— 第 2 个完整实现的策略（docs/06 §4）。

规格给的验收闸门（docs/06 §16）：

> | 2 | ``turnaround`` | 验证**策略与 ST 标签解耦**（示例 J） |

模块结构与 ``restructuring`` 对齐::

    rules.py         核心条件判定 + 催化剂阶梯
    invalidation.py  失效条件判定（逻辑失效状态的唯一依据）
    questions.py     「待确认」事项的可判定实现
    thesis.py        Thesis 语句与 Why Now

**注意**：本 ``__init__`` 刻意不导入 ``rules``，以避免与
``app.strategies`` 的加载器形成循环导入。
"""

from app.models.enums import ThesisType

CODE = ThesisType.TURNAROUND
DISPLAY_NAME = "困境反转"

__all__ = ["CODE", "DISPLAY_NAME"]
