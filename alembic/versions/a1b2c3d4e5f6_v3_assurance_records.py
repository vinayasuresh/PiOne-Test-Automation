"""add V3 assurance records

Revision ID: a1b2c3d4e5f6
Revises: 7366c6a3b93d
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "7366c6a3b93d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_incidents",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("correlation_id", sa.String(length=160), nullable=False, unique=True),
        sa.Column("ticket_reference", sa.String(length=160)),
        sa.Column("grouping_key", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("affected_feature", sa.String(length=255), server_default=""),
        sa.Column("reporter_role", sa.String(length=120), server_default=""),
        sa.Column("expected_behavior", sa.Text(), server_default=""),
        sa.Column("actual_behavior", sa.Text(), server_default=""),
        sa.Column("priority", sa.String(length=30), nullable=False, server_default="Medium"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_incidents_correlation_id", "support_incidents", ["correlation_id"])
    op.create_index("ix_support_incidents_grouping_key", "support_incidents", ["grouping_key"])
    op.create_index("ix_support_incidents_status", "support_incidents", ["status"])
    op.create_table(
        "verification_jobs",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("incident_id", sa.String(length=80), sa.ForeignKey("support_incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("scan_runs.id", ondelete="SET NULL")),
        sa.Column("execution_id", sa.String(length=80), sa.ForeignKey("executions.id", ondelete="SET NULL")),
        sa.Column("checkpoint_id", sa.String(length=80), sa.ForeignKey("checkpoints.id", ondelete="SET NULL")),
        sa.Column("artifact_id", sa.String(length=80), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("scenario_version", sa.String(length=100), nullable=False, server_default="unassigned"),
        sa.Column("target", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("authorized_role", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=60), nullable=False, server_default="functional_only"),
        sa.Column("state", sa.String(length=40), nullable=False, server_default="created"),
        sa.Column("verdict", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("technical_category", sa.String(length=100), server_default=""),
        sa.Column("grouping_key", sa.String(length=255), nullable=False),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("incident_id", "run_id", "execution_id", "checkpoint_id", "artifact_id", "state", "verdict", "grouping_key"):
        op.create_index(f"ix_verification_jobs_{column}", "verification_jobs", [column])
    op.create_table(
        "verification_attempts",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("job_id", sa.String(length=80), sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=120), server_default=""),
        sa.Column("state", sa.String(length=40), nullable=False, server_default="created"),
        sa.Column("terminal_outcome", sa.String(length=40), server_default="pending"),
        sa.Column("failure_category", sa.String(length=100), server_default=""),
        sa.Column("event_sequence", sa.JSON()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_verification_attempt_job_number"),
    )
    op.create_index("ix_verification_attempts_job_id", "verification_attempts", ["job_id"])
    op.create_table(
        "evidence_manifests",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("job_id", sa.String(length=80), sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(length=80), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("scan_runs.id", ondelete="SET NULL")),
        sa.Column("source_step", sa.String(length=160), server_default=""),
        sa.Column("source_route", sa.String(length=1024), server_default=""),
        sa.Column("checksum", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=120), nullable=False, server_default="application/octet-stream"),
        sa.Column("storage_location", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("retention_expires_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("job_id", "artifact_id", "run_id"):
        op.create_index(f"ix_evidence_manifests_{column}", "evidence_manifests", [column])
    op.create_table(
        "assignment_rules",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("product", sa.String(length=255), server_default=""),
        sa.Column("feature", sa.String(length=255), server_default=""),
        sa.Column("service", sa.String(length=255), server_default=""),
        sa.Column("owner_team", sa.String(length=255), nullable=False),
        sa.Column("severity_threshold", sa.String(length=30), server_default="Low"),
        sa.Column("escalation_window_minutes", sa.Integer(), server_default="0"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assignment_rules_feature", "assignment_rules", ["feature"])


def downgrade() -> None:
    for table in ("assignment_rules", "evidence_manifests", "verification_attempts", "verification_jobs", "support_incidents"):
        op.drop_table(table)
