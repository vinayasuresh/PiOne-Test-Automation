"""add V3 API contract assurance tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_contracts",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False, server_default="unversioned"),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("environment", sa.String(length=40), nullable=False, server_default="staging"),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_api_contracts_created_at", "api_contracts", ["created_at"])
    op.create_table(
        "api_operations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("contract_id", sa.String(length=80), sa.ForeignKey("api_contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("expected_status_codes", sa.JSON(), nullable=False),
        sa.Column("required_fields", sa.JSON(), nullable=False),
        sa.Column("max_latency_ms", sa.Integer(), nullable=False, server_default="3000"),
        sa.Column("auth_requirement", sa.String(length=30), nullable=False, server_default="none"),
        sa.Column("auth_fixture_ref", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("approved_role", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("allow_unauthenticated_probe", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("safe_read_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("contract_id", "operation_key", name="uq_api_operation_contract_key"),
    )
    op.create_index("ix_api_operations_contract_id", "api_operations", ["contract_id"])
    op.create_table(
        "api_observations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("operation_id", sa.String(length=80), sa.ForeignKey("api_operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(length=80), sa.ForeignKey("verification_jobs.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("scan_runs.id", ondelete="SET NULL")),
        sa.Column("evidence_manifest_id", sa.String(length=80), sa.ForeignKey("evidence_manifests.id", ondelete="SET NULL")),
        sa.Column("scenario_version", sa.String(length=100), nullable=False, server_default="unassigned"),
        sa.Column("result", sa.String(length=30), nullable=False),
        sa.Column("classification", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("status_code", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("request_metadata", sa.JSON(), nullable=False),
        sa.Column("assertion_result", sa.JSON(), nullable=False),
        sa.Column("evidence_link", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("operation_id", "job_id", "run_id", "evidence_manifest_id", "result", "created_at"):
        op.create_index(f"ix_api_observations_{column}", "api_observations", [column])


def downgrade() -> None:
    op.drop_table("api_observations")
    op.drop_table("api_operations")
    op.drop_table("api_contracts")
