"""ai_analyses

Revision ID: 002
Revises: 001
"""
import sqlalchemy as sa

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('activity', 'period')", name="ck_ai_analyses_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "input_hash", name="uq_ai_analyses_kind_input_hash"),
    )
    op.create_index("ix_ai_analyses_kind_subject", "ai_analyses", ["kind", "subject"])


def downgrade() -> None:
    op.drop_index("ix_ai_analyses_kind_subject", table_name="ai_analyses")
    op.drop_table("ai_analyses")
