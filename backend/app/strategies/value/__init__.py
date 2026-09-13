"""``value`` 策略实现（docs/06）。

模块仅声明 ``CODE`` 与 ``DISPLAY_NAME``：
刻意不导入 ``rules``，避免与 ``app.strategies`` 的加载器形成循环导入。
"""

from app.models.enums import ThesisType

CODE = ThesisType.VALUE
DISPLAY_NAME = "价值发现 / 高股息"

__all__ = ["CODE", "DISPLAY_NAME"]
