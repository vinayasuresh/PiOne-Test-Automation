"""Phase-4 tests for the approved, read-only database cross-read adapter."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

import api.db.repository as repository_module
import api.routers.v3_assurance as assurance_router
from api.db.repository import DBRepository
from api.models.v3_database_models import DatabaseQueryRegistrationRequest, DatabaseReadRequest
from api.models.v3_data_assertion_models import DataAssertionEvaluationRequest
from pydantic import ValidationError
from api.routers.v3_assurance import register_database_query
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


def test_database_driver_errors_do_not_echo_sql_or_schema_details():
    query = ApprovedDatabaseQuery(
        query_id="missing-table",
        sql="SELECT secret FROM definitely_missing_table",
    )
    result = read_approved_database(_engine(), query)
    assert result["classification"] == "database_unavailable"
    assert result["source_metadata"]["error"] == "approved database read failed"
    assert "definitely_missing_table" not in str(result)


def test_negative_or_non_finite_tolerance_is_rejected():
    for value in ("-1", "NaN", "Infinity"):
        try:
            DatabaseReadRequest(tolerance=value)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid tolerance accepted: {value}")

    assert compare_api_and_database("10", "9", tolerance="-1")["classification"] == "invalid_tolerance"
    assert compare_api_and_database("10", "10", tolerance="NaN")["classification"] == "invalid_tolerance"


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


def test_database_query_registration_persists_approved_definition_without_sql(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    from api.db.session import Base
    import api.db.models  # noqa: F401
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(repository_module, "execute_with_retry", lambda operation: operation(session))
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")

    response = register_database_query(DatabaseQueryRegistrationRequest(
        query_id="asset-count",
        name="Asset count by tenant",
        sql="SELECT COUNT(*) AS total FROM assets WHERE tenant = :tenant",
        parameter_names=["tenant"],
        environment="staging",
        approved=True,
    ))
    assert response["query"]["query_id"] == "asset-count"
    assert "sql" not in response["query"]
    assert DBRepository.get_v3_database_query("asset-count")["is_approved"] is True
    session.close()


def test_database_query_registration_rejects_unapproved_and_production(monkeypatch):
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    request = DatabaseQueryRegistrationRequest(
        query_id="not-approved", name="Not approved", sql="SELECT 1", approved=False,
    )
    try:
        register_database_query(request)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "explicitly approved" in str(exc.detail)
    else:
        raise AssertionError("unapproved query was registered")

    production = DatabaseQueryRegistrationRequest(
        query_id="production-query", name="Production", sql="SELECT 1", environment="production", approved=True,
    )
    try:
        register_database_query(production)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Production" in str(exc.detail)
    else:
        raise AssertionError("production query was accepted")


def test_bound_database_query_is_included_in_dashboard_lineage(monkeypatch):
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setattr(assurance_router.DBRepository, "get_v3_data_assertion_scenario", lambda _: {
        "data_assertion_scenario_id": "scenario-1", "name": "Metric", "route": "/dashboard",
        "ui_selector": "#metric", "source_operation_id": "op-1", "database_query_id": "metric-db",
        "source_field": "total", "source_timestamp_field": "", "expected_filters": {},
        "freshness_window_minutes": 60, "tolerance": "0", "value_format": "number", "is_approved": True,
    })
    monkeypatch.setattr(assurance_router.DBRepository, "get_v3_api_operation", lambda _: {
        "contract": {"base_url": "https://example.test"}, "operation": {"path": "/metrics"},
    })
    monkeypatch.setattr(assurance_router, "read_approved_source", lambda *args: {
        "result": "ok", "source_value": "10", "source_fresh_at": None,
        "source_metadata": {"path": "/metrics"},
    })
    monkeypatch.setattr(assurance_router.DBRepository, "get_v3_database_query_definition", lambda _: {
        "query_id": "metric-db", "sql": "SELECT total FROM metrics", "parameter_names": [],
        "database_ref": "configured-test", "environment": "test", "is_approved": True,
    })
    monkeypatch.setattr(assurance_router, "read_approved_database", lambda *args: {
        "result": "ok", "rows": [{"total": 9}], "source_metadata": {"query_id": "metric-db"},
    })
    monkeypatch.setattr(assurance_router.DBRepository, "add_v3_data_assertion_observation", lambda payload: payload)

    result = assurance_router.evaluate_data_assertion_scenario(
        "scenario-1", DataAssertionEvaluationRequest(ui_value="10"),
    )
    assert result["comparison"]["result"] == "fail"
    assert result["comparison"]["classification"] == "database_api_mismatch"
    assert result["comparison"]["database_query_id"] == "metric-db"
    assert result["observation"]["source_metadata"]["database_query_id"] == "metric-db"

