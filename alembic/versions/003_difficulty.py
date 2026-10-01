"""activities.difficulty (perceived effort 1-10)

Revision ID: 003
Revises: 002
"""
import sqlalchemy as sa

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plain ADD COLUMN: batch mode would DROP/recreate activities, blocked by inbound FKs
    op.add_column(
        "activities",
        sa.Column(
            "difficulty",
            sa.Integer(),
            sa.CheckConstraint(
                "difficulty IS NULL OR difficulty BETWEEN 1 AND 10", name="ck_activities_difficulty"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("activities", "difficulty")
