"""Pipeline 层：把「原始数据」组装成「机会」。

这是 docs/03 §3 漏斗的 Stage 3 ~ Stage 6 实现，也是把已有的零件接起来的那一环::

    ingest/  →  DB（Company / Announcement / Paragraph / News / Financial）
                    ↓ facts_builder       DB → StrategyFacts（纯数据）
                    ↓ ai/nodes            StrategyFacts → Event + Evidence（经证据闸门）
                    ↓ strategies/         规则命中、失效检测、催化剂阶梯
                    ↓ engine/scoring      维度分 + 逐项拆解 + 风险
                    ↓ opportunity_builder Opportunity + Thesis + OpenQuestion + Alert

分层原则：本包**不含规则**（规则在 engine/ 与 strategies/），也不含 LLM 调用细节
（在 ai/），只负责编排与持久化。这样每一层都能被单独测试。
"""

from app.pipeline import event_writer, facts_builder, opportunity_builder, runner

__all__ = ["event_writer", "facts_builder", "opportunity_builder", "runner"]
