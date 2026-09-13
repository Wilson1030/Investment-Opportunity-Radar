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
        """输入数据 + **收尾的输出指令**。

        ★ 收尾指令不是装饰：小模型（如本机 qwen3:4b）在「要求 JSON 输出 +
        输入是一大段 JSON」时，很容易**直接把输入抄回来**当结果。
        实测 ``analyze`` 与 ``score_semantic`` 因此 6/6 校验失败
        （``raw_output`` 里是输入数据的字段，而不是要求的字段）。

        利用近位效应在最后再说一遍「你要输出什么字段、不要重复输入」，
        对这类失败最有效。
        """
        fields = "、".join(self.Output.model_fields)
        return (
            "【输入数据】\n"
            f"{json.dumps(payload.model_dump(mode='json'), ensure_ascii=False, indent=1)}\n\n"
            "【你要输出的结果】\n"
            f"只输出一个 JSON 对象，必须且只能包含这些字段：{fields}\n"
            "严禁把上面的输入数据原样重复一遍；严禁输出解释文字或 Markdown 代码块。"
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
