"""Small idempotency helpers for additive Alembic migrations.

The local developer database historically used SQLAlchemy ``create_all`` at
startup before Alembic was introduced.  These helpers let additive migrations
recognise that already-materialised schema and avoid trying to recreate the
same tables, indexes, or columns.  They never drop or rewrite user data.
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
from sqlalchemy import inspect


def _inspector():
    return inspect(op.get_bind())


def table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    if not table_exists(table_name):
        return False
    return column_name in {col["name"] for col in _inspector().get_columns(table_name)}


def index_exists(table_name: str, index_name: str) -> bool:
    if not table_exists(table_name):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table_name)}


def create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: Iterable[str],
    *,
    unique: bool = False,
) -> None:
    if not index_exists(table_name, index_name):
        op.create_index(index_name, table_name, list(columns), unique=unique)


def add_column_if_missing(table_name: str, column) -> None:
    if not column_exists(table_name, column.name):
        op.add_column(table_name, column)
