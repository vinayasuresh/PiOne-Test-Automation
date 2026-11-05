"""Add bounded locator evidence for interactive browser sessions.

Revision ID: j0a1b2c3d4e5
Revises: i9b0c1d2e3f4
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import table_exists


revision = "j0a1b2c3d4e5"
down_revision = "i9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if table_exists("interactive_browser_evidence"):
        return
    op.create_table(
        "interactive_browser_evidence",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("session_id", sa.String(length=80), sa.ForeignKey("interactive_browser_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(length=120), nullable=False, server_default="default"),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("page_url", sa.String(length=2048), nullable=False),
        sa.Column("route", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("selector", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("xpath", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=80), nullable=False, server_default="manual_interaction"),
        sa.Column("text_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("text_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_interactive_browser_evidence_session_id", "interactive_browser_evidence", ["session_id"])
    op.create_index("ix_interactive_browser_evidence_organization_id", "interactive_browser_evidence", ["organization_id"])
    op.create_index("ix_interactive_browser_evidence_created_at", "interactive_browser_evidence", ["created_at"])
    op.create_index(
        "ix_interactive_browser_evidence_session_created",
        "interactive_browser_evidence",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_interactive_browser_evidence_org_created",
        "interactive_browser_evidence",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    for name in (
        "ix_interactive_browser_evidence_org_created",
        "ix_interactive_browser_evidence_session_created",
        "ix_interactive_browser_evidence_created_at",
        "ix_interactive_browser_evidence_organization_id",
        "ix_interactive_browser_evidence_session_id",
    ):
        op.drop_index(name, table_name="interactive_browser_evidence")
    op.drop_table("interactive_browser_evidence")
