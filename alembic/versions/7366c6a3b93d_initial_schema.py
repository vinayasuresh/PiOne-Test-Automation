"""initial schema

Revision ID: 7366c6a3b93d
Revises: 
Create Date: 2026-10-21 01:52:26.717035

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from api.db.session import Base
import api.db.models  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = '7366c6a3b93d'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BASELINE_TABLES = [
    "projects",
    "application_targets",
    "scan_runs",
    "scan_modules",
    "scan_routes",
    "scan_elements",
    "route_edges",
    "test_plans",
    "test_cases",
    "checkpoints",
    "executions",
    "execution_steps",
    "artifacts",
    "audit_events",
    "regression_suites",
    "regression_suite_cases",
]


def upgrade() -> None:
    # Baseline V2 tables materialized from ORM declarative metadata.
    # Uses checkfirst=True to remain fully idempotent on databases where
    # tables were already created.
    bind = op.get_bind()
    tables_to_create = [
        Base.metadata.tables[name]
        for name in BASELINE_TABLES
        if name in Base.metadata.tables
    ]
    Base.metadata.create_all(bind=bind, tables=tables_to_create, checkfirst=True)


def downgrade() -> None:
    pass
