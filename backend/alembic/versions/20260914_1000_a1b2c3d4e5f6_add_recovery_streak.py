"""add recovery_streak to opportunity

Revision ID: a1b2c3d4e5f6
Revises: 47845a70b30d
Create Date: 2026-09-14 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "47845a70b30d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 失效纠错的防抖动计数器：0 = 尚未出现「失效条件不再成立」的判定。
    #
    # ⚠ 这里**刻意不用** batch_alter_table：SQLite 的 batch 模式会
    # 「建临时表 → 拷数据 → DROP 原表 → RENAME」，而 opportunity 被
    # scoreitem / alert / opportunitystatuslog 等表外键引用 ——
    # DROP 会被外键约束挡住，且失败后会残留 _alembic_tmp_opportunity。
    # 单纯加列是 SQLite 原生支持的（ALTER TABLE ADD COLUMN），
    # 不重建表、不碰外键，是这里唯一稳妥的做法。
    # NOT NULL 列必须带 server_default，否则已有数据行无法满足约束。
    op.execute(
        "ALTER TABLE opportunity ADD COLUMN recovery_streak "
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    # SQLite 支持 DROP COLUMN（3.35+）；同样不重建表。
    op.execute("ALTER TABLE opportunity DROP COLUMN recovery_streak")
