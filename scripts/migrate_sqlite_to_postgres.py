"""Safely migrate the local OmniTest SQLite store into an empty PostgreSQL DB.

The migration is deliberately opt-in (``--apply``), creates a consistent
SQLite backup first, refuses to overwrite any non-empty PostgreSQL table, and
copies only tables known to the current ORM plus the Alembic version marker.
Credentials are read from ``DATABASE_URL`` and never printed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Column, MetaData, String, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SOURCE = ROOT / "automation_framework" / "reports" / "pione_testmate.db"
DEFAULT_BACKUP_DIR = ROOT / "automation_framework" / "reports"


def make_sqlite_backup(source: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DEFAULT_BACKUP_DIR / f"pione_testmate.pre-postgres-{stamp}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    return backup


def public_table_counts(engine: Engine) -> dict[str, int]:
    names = inspect(engine).get_table_names(schema="public")
    if not names:
        return {}
    quote = engine.dialect.identifier_preparer.quote
    with engine.connect() as conn:
        return {
            name: int(conn.execute(text(f"SELECT count(*) FROM public.{quote(name)}")).scalar_one())
            for name in names
        }


def reset_sequences(engine: Engine, tables: list[Table], connection: Any) -> None:
    """Advance PostgreSQL serial sequences after importing explicit IDs."""
    for table in tables:
        if len(table.primary_key.columns) != 1:
            continue
        pk = next(iter(table.primary_key.columns))
        if not getattr(pk.type, "python_type", None) is int:
            continue
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": f"public.{table.name}", "column_name": pk.name},
        ).scalar_one_or_none()
        if not sequence:
            continue
        maximum = connection.execute(select(func.max(pk))).scalar_one_or_none()
        if maximum is not None:
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :value, true)"),
                {"sequence": sequence, "value": int(maximum)},
            )


def source_foreign_key_violations(source: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Return legacy orphan FK rows, grouped by source table and FK id."""
    violations: dict[tuple[str, int], dict[str, Any]] = {}
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as connection:
        for table, rowid, parent, fk_id in connection.execute("PRAGMA foreign_key_check"):
            key = (str(table), int(fk_id))
            entry = violations.setdefault(key, {"table": str(table), "parent": str(parent), "rowids": set()})
            entry["rowids"].add(rowid)
        for (table, fk_id), entry in violations.items():
            fk_rows = connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            matching = sorted((row for row in fk_rows if row[0] == fk_id), key=lambda row: row[1])
            if not matching:
                raise RuntimeError(f"Could not resolve a legacy foreign key on {table}.")
            entry["columns"] = [row[3] for row in matching]
            entry["referred_table"] = matching[0][2]
            entry["referred_columns"] = [row[4] for row in matching]
            entry["ondelete"] = matching[0][6]
            entry["row_count"] = len(entry.pop("rowids"))
    return violations


def relax_legacy_orphan_constraints(
    engine: Engine,
    connection: Any,
    violations: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recreate only already-violated FKs as PostgreSQL NOT VALID constraints.

    PostgreSQL still enforces NOT VALID constraints for new writes. The flag
    prevents the existing SQLite orphan rows from being discarded during the
    import and records this inherited data-quality debt for later repair.
    """
    quote = engine.dialect.identifier_preparer.quote
    inspector = inspect(connection)
    relaxed: list[dict[str, Any]] = []
    for (table_name, fk_id), info in violations.items():
        candidates = inspector.get_foreign_keys(table_name, schema="public")
        match = next(
            (
                fk for fk in candidates
                if fk.get("constrained_columns") == info["columns"]
                and fk.get("referred_table") == info["referred_table"]
                and fk.get("referred_columns") == info["referred_columns"]
            ),
            None,
        )
        if not match or not match.get("name"):
            raise RuntimeError(f"Could not match the PostgreSQL foreign key on {table_name}.")
        name = match["name"]
        connection.execute(
            text(f"ALTER TABLE public.{quote(table_name)} DROP CONSTRAINT {quote(name)}")
        )
        ondelete = str(info["ondelete"]).upper()
        allowed_actions = {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}
        if ondelete not in allowed_actions:
            raise RuntimeError(f"Unsupported ON DELETE action in source schema for {table_name}.")
        relaxed.append(
            {
                "table": table_name,
                "constraint": name,
                "columns": info["columns"],
                "referred_table": info["referred_table"],
                "referred_columns": info["referred_columns"],
                "ondelete": ondelete,
                "existing_orphan_rows": info["row_count"],
            }
        )
    return relaxed


def add_unvalidated_legacy_constraints(engine: Engine, connection: Any, relaxed: list[dict[str, Any]]) -> None:
    """Restore dropped constraints after import, preserving future-write checks."""
    quote = engine.dialect.identifier_preparer.quote
    for item in relaxed:
        child_columns = ", ".join(quote(name) for name in item["columns"])
        parent_columns = ", ".join(quote(name) for name in item["referred_columns"])
        connection.execute(
            text(
                f"ALTER TABLE public.{quote(item['table'])} ADD CONSTRAINT {quote(item['constraint'])} "
                f"FOREIGN KEY ({child_columns}) REFERENCES public.{quote(item['referred_table'])} "
                f"({parent_columns}) ON DELETE {item['ondelete']} NOT VALID"
            )
        )


def migrate(source: Path, apply: bool, expected_database: str) -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=False)
    import os

    from api.db.session import Base

    # Import all model declarations before inspecting the metadata.
    import api.db.models  # noqa: F401

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required; migration did not run.")
    target = create_engine(database_url, pool_pre_ping=True)
    if target.dialect.name != "postgresql":
        target.dispose()
        raise RuntimeError("The configured destination is not PostgreSQL; migration did not run.")
    with target.connect() as connection:
        database_name = connection.execute(text("SELECT current_database()")).scalar_one()
    if database_name != expected_database:
        target.dispose()
        raise RuntimeError(f"Destination must be the approved local database {expected_database!r}.")
    if not source.is_file():
        target.dispose()
        raise FileNotFoundError(f"SQLite source database not found: {source}")

    source_sqlite = create_engine(f"sqlite:///{source.as_posix()}")
    source_inspector = inspect(source_sqlite)
    source_names = set(source_inspector.get_table_names())
    orm_tables = list(Base.metadata.sorted_tables)
    known = {table.name: table for table in orm_tables}
    source_model_names = [table.name for table in orm_tables if table.name in source_names]
    if not source_model_names:
        source_sqlite.dispose()
        target.dispose()
        raise RuntimeError("No current OmniTest model tables were found in the SQLite source.")

    existing_counts = public_table_counts(target)
    nonempty = {name: count for name, count in existing_counts.items() if count}
    if nonempty:
        source_sqlite.dispose()
        target.dispose()
        raise RuntimeError(
            "Destination contains non-empty public tables; refusing to merge or overwrite them."
        )

    source_counts: dict[str, int] = {}
    with source_sqlite.connect() as connection:
        for name in source_model_names:
            source_counts[name] = int(connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
        if "alembic_version" in source_names:
            source_counts["alembic_version"] = int(
                connection.execute(text('SELECT count(*) FROM "alembic_version"')).scalar_one()
            )

    if not apply:
        source_fk_violations = source_foreign_key_violations(source)
        source_sqlite.dispose()
        target.dispose()
        return {
            "mode": "dry-run",
            "source_table_count": len(source_model_names),
            "nonempty_source_tables": {k: v for k, v in source_counts.items() if v},
            "destination_database": database_name,
            "destination_nonempty_tables": nonempty,
            "legacy_foreign_key_violation_rows": sum(
                info["row_count"] for info in source_fk_violations.values()
            ),
            "legacy_foreign_key_violations": [
                {key: value for key, value in info.items() if key not in {"rowids"}}
                for info in source_fk_violations.values()
            ],
        }

    backup = make_sqlite_backup(source)
    # Materialize the complete current ORM schema. create_all is additive and
    # safe here because the destination has no non-empty public tables.
    Base.metadata.create_all(bind=target)
    if "alembic_version" in source_names:
        alembic_table = Table(
            "alembic_version", MetaData(), Column("version_num", String(32), primary_key=True)
        )
        alembic_table.create(target, checkfirst=True)
    tables_to_reset = orm_tables
    copied_counts: dict[str, int] = {}
    source_fk_violations = source_foreign_key_violations(source)
    relaxed_constraints: list[dict[str, Any]] = []
    try:
        with source_sqlite.connect() as source_conn, target.begin() as target_conn:
            relaxed_constraints = relax_legacy_orphan_constraints(
                target, target_conn, source_fk_violations
            )
            for table in orm_tables:
                if table.name not in source_names:
                    continue
                source_columns = {column["name"] for column in source_inspector.get_columns(table.name)}
                columns = [column for column in table.columns if column.name in source_columns]
                if not columns:
                    copied_counts[table.name] = 0
                    continue
                result = source_conn.execute(select(*columns).select_from(table))
                rows = result.mappings().all()
                if rows:
                    target_conn.execute(table.insert(), [dict(row) for row in rows])
                copied_counts[table.name] = len(rows)

            if "alembic_version" in source_names:
                versions = source_conn.execute(text('SELECT version_num FROM "alembic_version"')).mappings().all()
                if versions:
                    target_conn.execute(
                        text("INSERT INTO public.alembic_version (version_num) VALUES (:version_num)"),
                        [dict(row) for row in versions],
                    )
                copied_counts["alembic_version"] = len(versions)

            reset_sequences(target, tables_to_reset, target_conn)
            add_unvalidated_legacy_constraints(target, target_conn, relaxed_constraints)
    except Exception:
        source_sqlite.dispose()
        target.dispose()
        raise

    actual_counts = public_table_counts(target)
    mismatches = {
        name: {"source": count, "destination": actual_counts.get(name, 0)}
        for name, count in source_counts.items()
        if actual_counts.get(name, 0) != count
    }
    source_sqlite.dispose()
    target.dispose()
    if mismatches:
        raise RuntimeError(f"Migration row-count verification failed for {len(mismatches)} tables.")
    return {
        "mode": "applied",
        "destination_database": database_name,
        "source": str(source),
        "sqlite_backup": str(backup),
        "source_table_count": len(source_model_names),
        "copied_table_count": len(copied_counts),
        "total_rows_copied": sum(copied_counts.values()),
        "row_counts": copied_counts,
        "row_count_verification": "passed",
        "legacy_foreign_key_violation_rows": sum(
            info["row_count"] for info in source_fk_violations.values()
        ),
        "legacy_foreign_key_constraints": [
            {
                **item,
                "enforcement": "NOT VALID; new writes remain enforced",
            }
            for item in relaxed_constraints
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--expected-database", default="Omni_Test")
    parser.add_argument("--apply", action="store_true", help="Create backup and apply the migration")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source.resolve(), args.apply, args.expected_database), indent=2))


if __name__ == "__main__":
    main()
