"""Phase-4 tests for the approved, read-only database cross-read adapter."""

from sqlalchemy import create_engine, text

from api.services.v3_database_assertion_service import (
    ApprovedDatabaseQuery,
    compare_api_and_database,
    read_approved_database,
    validate_approved_query,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE metrics (tenant TEXT, total INTEGER, secret TEXT)"))
        connection.execute(text("INSERT INTO metrics VALUES ('acme', 1000, 'do-not-export')"))
    return engine


def test_approved_read_returns_rows_and_redacts_sensitive_values():
    query = ApprovedDatabaseQuery(
        query_id="monthly-metric",
        sql="SELECT tenant, total, secret FROM metrics WHERE tenant = :tenant",
        parameter_names=("tenant",),
    )
    result = read_approved_database(_engine(), query, {"tenant": "acme"})
    assert result["result"] == "ok"
    assert result["classification"] == "database_read"
    assert result["row_count"] == 1
    assert result["rows"][0]["tenant"] == "acme"
    assert result["rows"][0]["secret"] == "[REDACTED]"
    assert "do-not-export" not in str(result)


def test_mutation_and_multi_statement_queries_are_blocked():
    for sql in (
        "DELETE FROM metrics",
        "SELECT * FROM metrics; DELETE FROM metrics",
        "PRAGMA table_info(metrics)",
    ):
        query = ApprovedDatabaseQuery(query_id="unsafe", sql=sql)
        result = read_approved_database(_engine(), query)
        assert result["result"] == "blocked"
        assert result["classification"] == "database_query_not_approved"


def test_only_approved_parameters_are_accepted_and_missing_values_blocked():
    query = ApprovedDatabaseQuery(
        query_id="metric-by-tenant",
        sql="SELECT total FROM metrics WHERE tenant = :tenant",
        parameter_names=("tenant",),
    )
    missing = read_approved_database(_engine(), query)
    extra = read_approved_database(_engine(), query, {"tenant": "acme", "raw_sql": "x"})
    assert missing["classification"] == "database_parameters_invalid"
    assert extra["classification"] == "database_parameters_invalid"


def test_query_definition_parameters_must_match_sql():
    query = ApprovedDatabaseQuery(
        query_id="bad-definition",
        sql="SELECT total FROM metrics WHERE tenant = :tenant",
        parameter_names=(),
    )
    try:
        validate_approved_query(query)
    except ValueError as exc:
        assert "parameters" in str(exc)
    else:
        raise AssertionError("invalid approved query definition was accepted")


def test_production_database_and_api_mismatch_are_not_allowed_to_run():
    production_query = ApprovedDatabaseQuery(
        query_id="production-read", sql="SELECT total FROM metrics", environment="production",
    )
    blocked = read_approved_database(_engine(), production_query)
    assert blocked["result"] == "blocked"
    assert blocked["classification"] == "database_query_not_approved"

    mismatch = compare_api_and_database("1000", "995", tolerance="0.01")
    assert mismatch["result"] == "fail"
    assert mismatch["classification"] == "database_api_mismatch"

    within_tolerance = compare_api_and_database("1000.00", "1000.004", tolerance="0.01")
    assert within_tolerance["result"] == "pass"
    assert within_tolerance["classification"] == "database_api_match"

