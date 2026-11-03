"""Bounded, read-only database cross-read support for OmniTest V3.

Database checks are intentionally definition-driven. Callers select a query ID
that was approved by the server; they cannot submit arbitrary SQL or a
connection string. The adapter is suitable for test/staging engines supplied
by the application configuration and never returns credentials or raw query
text in evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from automation_framework.utils.redactor import redact_sensitive_data


_READ_ONLY_START = re.compile(r"^(?:select|with)\b", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"(?:;|\b(?:insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|attach|detach|pragma|vacuum)\b)",
    re.IGNORECASE,
)
_PARAMETER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class ApprovedDatabaseQuery:
    """A server-owned query definition; SQL is never accepted from a request."""

    query_id: str
    sql: str
    parameter_names: tuple[str, ...] = ()
    database_ref: str = "configured-test"
    environment: str = "test"


def validate_approved_query(query: ApprovedDatabaseQuery) -> None:
    """Reject non-read-only or malformed server definitions before execution."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", query.query_id):
        raise ValueError("invalid approved database query ID")
    if query.database_ref != "configured-test" or query.environment not in {"test", "staging"}:
        raise ValueError("database reference is not approved")
    sql = " ".join(query.sql.strip().split())
    if not sql or not _READ_ONLY_START.match(sql) or _FORBIDDEN_SQL.search(sql):
        raise ValueError("only a single read-only SELECT or WITH query is allowed")
    placeholders = tuple(dict.fromkeys(_PARAMETER.findall(sql)))
    if placeholders != tuple(query.parameter_names):
        raise ValueError("query parameters do not match the approved definition")


def read_approved_database(
    engine: Engine,
    query: ApprovedDatabaseQuery,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one approved read and return redacted, bounded evidence."""
    try:
        validate_approved_query(query)
    except ValueError as exc:
        return {
            "result": "blocked",
            "classification": "database_query_not_approved",
            "source_metadata": {"query_id": query.query_id, "reason": str(exc)},
        }

    supplied = dict(parameters or {})
    unexpected = sorted(set(supplied) - set(query.parameter_names))
    missing = sorted(set(query.parameter_names) - set(supplied))
    if unexpected or missing:
        return {
            "result": "blocked",
            "classification": "database_parameters_invalid",
            "source_metadata": redact_sensitive_data({
                "query_id": query.query_id,
                "unexpected_parameters": unexpected,
                "missing_parameters": missing,
            }),
        }

    try:
        with engine.connect() as connection:
            result = connection.execute(text(query.sql), supplied)
            columns = list(result.keys())
            rows = [dict(row) for row in result.mappings().fetchmany(500)]
    except SQLAlchemyError as exc:
        return {
            "result": "blocked",
            "classification": "database_unavailable",
            "source_metadata": redact_sensitive_data({
                "query_id": query.query_id,
                "error": str(exc),
            }),
        }

    safe_rows = redact_sensitive_data(rows)
    return {
        "result": "ok",
        "classification": "database_read",
        "rows": safe_rows,
        "row_count": len(safe_rows),
        "columns": columns,
        "source_metadata": {
            "query_id": query.query_id,
            "database_ref": query.database_ref,
            "row_count": len(safe_rows),
        },
    }


def compare_api_and_database(
    api_value: Any,
    database_value: Any,
    *,
    tolerance: str = "0",
    api_result: str = "ok",
    database_result: str = "ok",
) -> dict[str, Any]:
    """Compare one API value with one approved DB value without hiding blocks."""
    if api_result != "ok" or database_result != "ok":
        return {
            "result": "blocked",
            "classification": "source_unavailable",
            "api_value": None,
            "database_value": None,
        }
    try:
        delta = abs(Decimal(str(api_value)) - Decimal(str(database_value)))
        allowed = Decimal(str(tolerance))
    except (InvalidOperation, ValueError):
        matches = str(api_value) == str(database_value)
        delta = None
        allowed = None
    else:
        matches = delta <= allowed
    return {
        "result": "pass" if matches else "fail",
        "classification": "database_api_match" if matches else "database_api_mismatch",
        "api_value": str(api_value),
        "database_value": str(database_value),
        "delta": str(delta) if delta is not None else None,
        "tolerance": str(allowed) if allowed is not None else str(tolerance),
    }

