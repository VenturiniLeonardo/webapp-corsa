"""GAP, HR at reference pace, adjusted EF, weather

Revision ID: 004
Revises: 003
"""
import sqlalchemy as sa

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

COLS = (
    ("activities", "weather_temp_c"),
    ("activities", "weather_dew_point_c"),
    ("laps", "gap_speed_ms"),
    ("activity_metrics", "gap_speed_ms"),
    ("activity_metrics", "hr_at_ref_pace"),
    ("activity_metrics", "ef_adjusted"),
)


def upgrade() -> None:
    # plain ADD COLUMN: batch mode would DROP/recreate activities, blocked by inbound FKs
    for table, col in COLS:
        op.add_column(table, sa.Column(col, sa.REAL(), nullable=True))


def downgrade() -> None:
    for table, col in COLS:
        op.drop_column(table, col)
