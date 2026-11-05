"""add response_schema and approved_parameters to api_operations

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import column_exists


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if column_exists("api_operations", "response_schema") and column_exists("api_operations", "approved_parameters"):
        return
    with op.batch_alter_table("api_operations") as batch_op:
        batch_op.add_column(sa.Column("response_schema", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("approved_parameters", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("api_operations") as batch_op:
        batch_op.drop_column("approved_parameters")
        batch_op.drop_column("response_schema")
