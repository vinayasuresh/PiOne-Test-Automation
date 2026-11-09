"""link dashboard assertion scenarios to approved database queries

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa
from migration_compat import column_exists


revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if column_exists("data_assertion_scenarios", "database_query_id"):
        return
    with op.batch_alter_table("data_assertion_scenarios") as batch_op:
        batch_op.add_column(sa.Column("database_query_id", sa.String(length=80), nullable=True))
        batch_op.create_index("ix_data_assertion_scenarios_database_query_id", ["database_query_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_data_assertion_scenarios_database_query_id",
            "database_query_definitions", ["database_query_id"], ["query_id"], ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("data_assertion_scenarios") as batch_op:
        batch_op.drop_constraint("fk_data_assertion_scenarios_database_query_id", type_="foreignkey")
        batch_op.drop_index("ix_data_assertion_scenarios_database_query_id")
        batch_op.drop_column("database_query_id")
