"""Persist V3 API execution plans and checkpoints.

Revision ID: h8a9b0c1d2e3f
Revises: g7b8c9d0e1f2
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import table_exists


revision = "h8a9b0c1d2e3f"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if table_exists("api_test_plan_states"):
        return
    op.create_table(
        "api_test_plan_states",
        sa.Column("plan_id", sa.String(length=80), primary_key=True),
        sa.Column("contract_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("total_operations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_count", sa.Integer(), nullable=False, server_default="0"),
        # Use quoted JSON text defaults for SQLite/PostgreSQL portability.
        # ``sa.text('{}')`` renders as DEFAULT ({}) and is invalid SQLite SQL.
        sa.Column("checkpoints", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("operation_results", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resolved_parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("coverage_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_api_test_plan_states_contract_id", "api_test_plan_states", ["contract_id"])
    op.create_index("ix_api_test_plan_states_status", "api_test_plan_states", ["status"])
    op.create_index("ix_api_test_plan_states_started_at", "api_test_plan_states", ["started_at"])
    op.create_index("ix_api_test_plan_states_updated_at", "api_test_plan_states", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_api_test_plan_states_updated_at", table_name="api_test_plan_states")
    op.drop_index("ix_api_test_plan_states_started_at", table_name="api_test_plan_states")
    op.drop_index("ix_api_test_plan_states_status", table_name="api_test_plan_states")
    op.drop_index("ix_api_test_plan_states_contract_id", table_name="api_test_plan_states")
    op.drop_table("api_test_plan_states")
