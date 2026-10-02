"""Route planner: routes table

Revision ID: 006
Revises: 005
"""

import sqlalchemy as sa

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("coords", sa.JSON(), nullable=False),
        sa.Column("distance_m", sa.REAL(), nullable=False),
        sa.Column("elev_gain_m", sa.REAL(), nullable=False),
        sa.Column("elev_loss_m", sa.REAL(), nullable=False),
        sa.Column("target_speed_ms", sa.REAL()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("routes")
