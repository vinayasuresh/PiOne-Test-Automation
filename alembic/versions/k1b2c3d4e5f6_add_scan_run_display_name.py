"""Persist the friendly scan session name for restart-safe artifacts.

Revision ID: k1b2c3d4e5f6
Revises: j0a1b2c3d4e5
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import column_exists, table_exists


revision = "k1b2c3d4e5f6"
down_revision = "j0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if table_exists("scan_runs") and not column_exists("scan_runs", "display_name"):
        op.add_column("scan_runs", sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    if table_exists("scan_runs") and column_exists("scan_runs", "display_name"):
        op.drop_column("scan_runs", "display_name")
