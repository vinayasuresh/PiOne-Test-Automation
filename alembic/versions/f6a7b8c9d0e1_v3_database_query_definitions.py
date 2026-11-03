"""add approved V3 database query definitions

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "database_query_definitions",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("query_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("parameter_names", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("database_ref", sa.String(length=80), nullable=False, server_default="configured-test"),
        sa.Column("environment", sa.String(length=20), nullable=False, server_default="test"),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("query_id", name="uq_database_query_definitions_query_id"),
    )
    op.create_index("ix_database_query_definitions_query_id", "database_query_definitions", ["query_id"], unique=True)
    op.create_index("ix_database_query_definitions_created_at", "database_query_definitions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_database_query_definitions_created_at", table_name="database_query_definitions")
    op.drop_index("ix_database_query_definitions_query_id", table_name="database_query_definitions")
    op.drop_table("database_query_definitions")
