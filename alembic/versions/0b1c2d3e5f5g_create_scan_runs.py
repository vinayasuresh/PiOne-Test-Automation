"""create scan_runs table

Revision ID: 0b1c2d3e5f5g
Revises: 7366c6a3b93d
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import table_exists

revision = "0b1c2d3e5f5g"
down_revision = "7366c6a3b93d"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # If the table already exists (e.g., from a legacy SQLite dump), skip creation.
    if table_exists("scan_runs"):
        return
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # Indexes for later look‑ups (optional but useful).
    op.create_index("ix_scan_runs_created_at", "scan_runs", ["created_at"])
    op.create_index("ix_scan_runs_updated_at", "scan_runs", ["updated_at"])

def downgrade() -> None:
    if not table_exists("scan_runs"):
        return
    op.drop_index("ix_scan_runs_created_at", table_name="scan_runs")
    op.drop_index("ix_scan_runs_updated_at", table_name="scan_runs")
    op.drop_table("scan_runs")
