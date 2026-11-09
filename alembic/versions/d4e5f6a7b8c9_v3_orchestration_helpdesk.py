"""add V3 bounded orchestration and helpdesk records

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import table_exists, column_exists


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if (
        table_exists("assurance_scenarios")
        and table_exists("monitoring_schedules")
        and all(column_exists("support_incidents", name) for name in
                ("owner_team", "next_action", "last_outcome", "resolved_at"))
        and all(column_exists("verification_jobs", name) for name in
                ("scenario_id", "policy_decision"))
    ):
        return
    op.create_table(
        "assurance_scenarios",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("feature", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("environment", sa.String(length=80), nullable=False, server_default="staging"),
        sa.Column("authorized_role", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("target_origin", sa.String(length=1024), nullable=False),
        sa.Column("scenario_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.String(length=80), nullable=False, unique=True),
        sa.Column("max_actions", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("feature", "environment", "reference_id", "created_at"):
        op.create_index(f"ix_assurance_scenarios_{column}", "assurance_scenarios", [column])
    op.create_table(
        "monitoring_schedules",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scenario_id", sa.String(length=80), sa.ForeignKey("assurance_scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target", sa.String(length=1024), nullable=False),
        sa.Column("environment", sa.String(length=80), nullable=False, server_default="staging"),
        sa.Column("grouping_key", sa.String(length=255), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_triggered_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("scenario_id", "grouping_key", "created_at"):
        op.create_index(f"ix_monitoring_schedules_{column}", "monitoring_schedules", [column])
    with op.batch_alter_table("support_incidents") as batch:
        batch.add_column(sa.Column("owner_team", sa.String(length=255), nullable=False, server_default="Unassigned"))
        batch.add_column(sa.Column("next_action", sa.String(length=500), nullable=False, server_default="Review verification outcome"))
        batch.add_column(sa.Column("last_outcome", sa.String(length=40), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("resolved_at", sa.DateTime()))
    op.create_index("ix_support_incidents_owner_team", "support_incidents", ["owner_team"])
    # Do not use SQLite batch recreation here.  Older deployment histories
    # may not have the full V2 foreign-key graph in Alembic metadata, and a
    # batch reflection would fail before these additive columns are applied.
    # The ORM relationship remains explicit; this additive migration never
    # rewrites or drops a verification job table.
    op.add_column("verification_jobs", sa.Column("scenario_id", sa.String(length=80), nullable=True))
    op.add_column("verification_jobs", sa.Column("policy_decision", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_verification_jobs_scenario_id", "verification_jobs", ["scenario_id"])


def downgrade() -> None:
    op.drop_index("ix_verification_jobs_scenario_id", table_name="verification_jobs")
    with op.batch_alter_table("verification_jobs") as batch:
        batch.drop_column("policy_decision")
        batch.drop_column("scenario_id")
    op.drop_index("ix_support_incidents_owner_team", table_name="support_incidents")
    with op.batch_alter_table("support_incidents") as batch:
        batch.drop_column("resolved_at")
        batch.drop_column("last_outcome")
        batch.drop_column("next_action")
        batch.drop_column("owner_team")
    op.drop_table("monitoring_schedules")
    op.drop_table("assurance_scenarios")
