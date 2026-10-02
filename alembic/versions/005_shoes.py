"""Shoe tracker: shoes table, activities.shoe_id

Revision ID: 005
Revises: 004
"""
import sqlalchemy as sa

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("target_distance_m", sa.REAL(), nullable=False),
        sa.Column("initial_distance_m", sa.REAL(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    # raw ADD COLUMN: SQLite accepts an inline FK here; batch mode would recreate activities
    op.execute(
        "ALTER TABLE activities ADD COLUMN shoe_id INTEGER "
        "CONSTRAINT fk_activities_shoe REFERENCES shoes (id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.drop_column("activities", "shoe_id")
    op.drop_table("shoes")
