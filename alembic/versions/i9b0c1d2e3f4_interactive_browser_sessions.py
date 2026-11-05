"""Add durable interactive browser session lifecycle records.

Revision ID: i9b0c1d2e3f4
Revises: h8a9b0c1d2e3f
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import table_exists


revision = "i9b0c1d2e3f4"
down_revision = "h8a9b0c1d2e3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if table_exists("interactive_browser_sessions"):
        return
    op.create_table(
        "interactive_browser_sessions",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), nullable=True),
        sa.Column("organization_id", sa.String(length=120), nullable=False, server_default="default"),
        sa.Column("owner_id", sa.String(length=120), nullable=False, server_default="operator"),
        sa.Column("target_origin", sa.String(length=1024), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("session_token_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="created"),
        sa.Column("phase", sa.String(length=40), nullable=False, server_default="control_plane"),
        sa.Column("checkpoint_id", sa.String(length=80), nullable=True),
        sa.Column("module_id", sa.String(length=160), nullable=True),
        sa.Column("route", sa.String(length=1024), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=False, server_default="local-worker"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for name, cols in {
        "ix_interactive_browser_sessions_run_id": ["run_id"],
        "ix_interactive_browser_sessions_organization_id": ["organization_id"],
        "ix_interactive_browser_sessions_status": ["status"],
        "ix_interactive_browser_sessions_checkpoint_id": ["checkpoint_id"],
        "ix_interactive_browser_sessions_expires_at": ["expires_at"],
        "ix_interactive_browser_sessions_last_activity_at": ["last_activity_at"],
        "ix_interactive_browser_sessions_created_at": ["created_at"],
        "ix_interactive_browser_org_status": ["organization_id", "status"],
    }.items():
        op.create_index(name, "interactive_browser_sessions", cols)


def downgrade() -> None:
    for name in (
        "ix_interactive_browser_org_status",
        "ix_interactive_browser_sessions_created_at",
        "ix_interactive_browser_sessions_last_activity_at",
        "ix_interactive_browser_sessions_expires_at",
        "ix_interactive_browser_sessions_checkpoint_id",
        "ix_interactive_browser_sessions_status",
        "ix_interactive_browser_sessions_organization_id",
        "ix_interactive_browser_sessions_run_id",
    ):
        op.drop_index(name, table_name="interactive_browser_sessions")
    op.drop_table("interactive_browser_sessions")
