"""Route surface sectors (cached Overpass result)

Revision ID: 008
Revises: 007
"""

import sqlalchemy as sa

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("surface", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("routes", "surface")
