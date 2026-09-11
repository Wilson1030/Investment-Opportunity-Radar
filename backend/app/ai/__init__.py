"""AI 层（语义理解）。

**分工（规格 §28）**：规则层负责确定性判断，本层只负责语义理解 ——
文本理解、事件归类、公告摘要、逻辑推理、证据整理、不确定性识别、措辞表达。

架构为**自研轻量节点管道**（D06）：节点是 ``JSON → JSON`` 纯函数，
LLM 调用 / 缓存 / 重试 / 审计 / 落库全部集中在 :mod:`app.ai.runner`，
因此将来要迁移到 LangGraph，只需新增 ``graph.py`` 把现有节点包成图节点。
"""

from app.ai import cache, nodes, prompts, provider, runner, schemas

__all__ = ["cache", "nodes", "prompts", "provider", "runner", "schemas"]
