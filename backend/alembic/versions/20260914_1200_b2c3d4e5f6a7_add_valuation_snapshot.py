"""add valuation_snapshot

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-14 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新表，不涉及「给已有数据的表加 NOT NULL 列」，所以不需要 server_default。
    op.create_table(
        "valuationsnapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("market_cap", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("pe_percentile", sa.Float(), nullable=True),
        sa.Column("pb_percentile", sa.Float(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default=sa.text("1095")),
        sa.Column("source_name", sa.String(), nullable=False,
                  server_default=sa.text("'baidu_gushitong'")),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "as_of", name="uq_valuation_daily"),
    )
    op.create_index(op.f("ix_valuationsnapshot_company_id"), "valuationsnapshot",
                    ["company_id"], unique=False)
    op.create_index(op.f("ix_valuationsnapshot_as_of"), "valuationsnapshot",
                    ["as_of"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_valuationsnapshot_as_of"), table_name="valuationsnapshot")
    op.drop_index(op.f("ix_valuationsnapshot_company_id"), table_name="valuationsnapshot")
    op.drop_table("valuationsnapshot")
