"""``ma_integration`` 策略实现（docs/06）。

模块仅声明 ``CODE`` 与 ``DISPLAY_NAME``：
刻意不导入 ``rules``，避免与 ``app.strategies`` 的加载器形成循环导入。
"""

from app.models.enums import ThesisType

CODE = ThesisType.MA_INTEGRATION
DISPLAY_NAME = "并购 / 产业整合"

__all__ = ["CODE", "DISPLAY_NAME"]
