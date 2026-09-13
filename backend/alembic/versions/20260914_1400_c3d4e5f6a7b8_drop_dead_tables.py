"""drop three dead tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-14 14:00:00.000000

移除三张**从未被写入过**的表（各 0 行，后端 0 处写入）：

  watchlistitem           自选 —— 已被状态机的 TRACKING 与 Opportunity 模型取代。
                          规格 §21 明确要求「不要保存成『自选股：ST XXX』，
                          而应保存成『因为重组预期，所以关注 ST XXX』」——
                          也就是说「自选」这个概念本身就是被 Opportunity+Thesis 取代的，
                          这张表是旧设计遗留。
  thesisinvalidationrule  失效规则镜像 —— 本想把 registry 的规则同步进库，
  thesistypedef           策略定义镜像     但 registry（纯 Python 数据）才是唯一来源。
                          镜像进库等于制造第二个来源，正是本项目反复踩到的那类问题
                          （同一事实两处实现 → 必然漂移）。

保留它们是「schema 层面的空承诺」：表存在、注释写着设计意图，但没有任何代码路径
能往里写一行。对使用者来说，这比没有这张表更容易误解。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEAD_TABLES = ("watchlistitem", "thesisinvalidationrule", "thesistypedef")


def upgrade() -> None:
    # 删表前先确认它们确实是空的 —— 有数据就说明有人写进去了，
    # 那删除会丢数据，必须先停下来看清楚（而不是默默删掉）。
    conn = op.get_bind()
    for table in DEAD_TABLES:
        rows = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar() or 0
        if rows:
            raise RuntimeError(
                f"{table} 有 {rows} 行数据 —— 说明它并非死表，停止删除"
            )
    for table in DEAD_TABLES:
        op.drop_table(table)


def downgrade() -> None:
    # 不重建：这三张表没有写入路径，重建没有意义。
    # 需要恢复时请从 git 历史里取回模型定义并重新生成迁移。
    pass
