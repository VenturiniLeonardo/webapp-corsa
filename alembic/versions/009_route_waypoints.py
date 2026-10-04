"""Route waypoints as placed by the user

Revision ID: 009
Revises: 008
"""

import sqlalchemy as sa

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("waypoints", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("routes", "waypoints")
