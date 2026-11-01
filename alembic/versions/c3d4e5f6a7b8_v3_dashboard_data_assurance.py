"""add V3 dashboard data assurance tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_assertion_scenarios",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("route", sa.String(length=1024), nullable=False),
        sa.Column("ui_selector", sa.String(length=1024), nullable=False),
        sa.Column("metric_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_operation_id", sa.String(length=80), sa.ForeignKey("api_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_field", sa.String(length=255), nullable=False),
        sa.Column("source_timestamp_field", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("expected_filters", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False, server_default="UTC"),
        sa.Column("freshness_window_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("tolerance", sa.String(length=80), nullable=False, server_default="0"),
        sa.Column("value_format", sa.String(length=30), nullable=False, server_default="number"),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_data_assertion_scenarios_source_operation_id", "data_assertion_scenarios", ["source_operation_id"])
    op.create_index("ix_data_assertion_scenarios_created_at", "data_assertion_scenarios", ["created_at"])
    op.create_table(
        "data_assertion_observations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("scenario_id", sa.String(length=80), sa.ForeignKey("data_assertion_scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(length=80), sa.ForeignKey("verification_jobs.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("scan_runs.id", ondelete="SET NULL")),
        sa.Column("evidence_manifest_id", sa.String(length=80), sa.ForeignKey("evidence_manifests.id", ondelete="SET NULL")),
        sa.Column("scenario_version", sa.String(length=100), nullable=False, server_default="unassigned"),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("classification", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("ui_raw_value", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("source_raw_value", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("normalized_ui_value", sa.String(length=80)),
        sa.Column("normalized_source_value", sa.String(length=80)),
        sa.Column("delta", sa.String(length=80)),
        sa.Column("filter_context", sa.JSON(), nullable=False),
        sa.Column("source_filter_context", sa.JSON(), nullable=False),
        sa.Column("source_fresh_at", sa.DateTime()),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("scenario_id", "job_id", "run_id", "evidence_manifest_id", "result", "created_at"):
        op.create_index(f"ix_data_assertion_observations_{column}", "data_assertion_observations", [column])


def downgrade() -> None:
    op.drop_table("data_assertion_observations")
    op.drop_table("data_assertion_scenarios")
