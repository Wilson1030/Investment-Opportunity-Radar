"""节点基类与纯函数约束。

**硬性约束（保证可迁移到 LangGraph，D06）**

* 节点内 **不得** 访问 DB、文件系统、网络（LLM 调用通过注入的 runner 完成）
* 节点 **不得** 读取全局可变状态
* 节点 **不得** 修改输入对象
* 节点只做两件事：**构造 prompt** 与 **声明输出 Schema**
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel

from app.ai.prompts import SHARED_CONSTRAINTS


class PromptNode:
    """所有节点的共同骨架。"""

    name: ClassVar[str] = ""
    prompt_version: ClassVar[str] = "v1"
    layer: ClassVar[str] = "extract"          # extract | analyze
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel]]

    #: 节点专属规则（子类覆盖）
    rules: ClassVar[str] = ""

    # ------------------------------------------------------------------ #
    def system_prompt(self) -> str:
        return (
            f"{SHARED_CONSTRAINTS}\n{self.rules}\n"
            f"【输出 JSON Schema 提示】\n{self.schema_hint()}"
        )

    def user_prompt(self, payload: BaseModel) -> str:
        return (
            "【输入数据】\n"
            f"{json.dumps(payload.model_dump(mode='json'), ensure_ascii=False, indent=1)}"
        )

    # ------------------------------------------------------------------ #
    def schema_hint(self) -> str:
        """用 Pydantic 的 JSON Schema 作为输出契约提示（降低本机小模型的不合规率）。"""
        try:
            schema = self.Output.model_json_schema()
        except Exception:  # pragma: no cover - 仅防御
            return "（无法生成 Schema 提示）"
        return json.dumps(schema, ensure_ascii=False, indent=1)


__all__ = ["PromptNode"]
