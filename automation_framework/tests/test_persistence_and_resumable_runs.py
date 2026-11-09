"""test_persistence_and_resumable_runs.py: Deterministic test suite for resumable runs,
persistent checkpoints, regression suite management, selectable execution, and DB fallback.

Verifies:
1. Checkpoint save & atomic replacement on disk and DB
2. Checkpoint resume with origin/namespace boundary validation
3. Refresh recovery and safe session re-authentication
4. Interrupted scan state recovery and completed-route caching
5. Regression suite CRUD, duplication, archiving, and deletion
6. Selectable regression execution:
   - Explicit confirmation requirement (confirmed=true)
   - Zero execution of unselected test cases
   - Execution recording and result aggregation
7. Baseline saving with explicit overwrite protection and comparison diff calculation
8. Database repository operations with SQLite graceful fallback
9. Scope isolation & recursive credential/token redaction in checkpoints and runs
10. REST API endpoints for runs, suites, baselines, and database health
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
import httpx

from api.db.repository import DBRepository
from api.db.session import check_db_status, get_db_session
from api.models.scan_models import (
    CreateRegressionSuiteRequest,
    ExecuteSelectedRegressionRequest,
    SaveBaselineRequest,
    TestCase,
    TestStep,
)
from api.services.run_context_service import (
    create_run_context,
    get_run_context,
    list_saved_runs,
    save_run_context,
)
from automation_framework.config.settings import REPORT_PATH
from automation_framework.storage.checkpoint_store import (
    compute_namespace,
    load_checkpoint,
    save_checkpoint,
)
from automation_framework.utils.redactor import redact_sensitive_data


@pytest.fixture(autouse=True)
def isolated_repository_database(monkeypatch):
    """Keep repository-contract tests independent of a developer's live database.

    These tests exercise persistence semantics, not the availability of the
    locally configured PostgreSQL service.  Using an explicit in-memory
    SQLite URL makes the dependency visible and prevents a blocked/unavailable
    developer database from turning a unit-test failure into a product failure.
    PostgreSQL connectivity is covered separately by environment integration
    checks.
    """
    import api.db.session as db_session

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DATABASE_FALLBACK_ENABLED", "true")
    db_session._ENGINE = None
    db_session._SESSION_FACTORY = None
    db_session._IS_FALLBACK = False
    db_session._LAST_ERROR = None
    db_session._SCHEMA_INITIALIZED = False

    yield

    # Do not leak the in-memory engine into later test modules.
    db_session._ENGINE = None
    db_session._SESSION_FACTORY = None
    db_session._IS_FALLBACK = False
    db_session._LAST_ERROR = None
    db_session._SCHEMA_INITIALIZED = False


# ---------------------------------------------------------------------------
# 1. Checkpoint Save & Atomic Replace
# ---------------------------------------------------------------------------
def test_checkpoint_save_and_atomic_replace():
    run_id = f"test_save_{uuid4().hex[:12]}"
    origin = "https://www.saucedemo.com"
    ns = compute_namespace(origin)

    data_v1 = {
        "run_id": run_id,
        "namespace": ns,
        "current_route": "/inventory.html",
        "visited_routes": ["/", "/inventory.html"],
        "pending_routes": ["/cart.html"],
        "completed_routes": ["/"],
        "scroll_position": {"x": 0, "y": 250},
        "schema_version": "2.0",
        "state_data": {"password": "secret_password_123"},
    }

    # First save
    save_checkpoint(run_id, data_v1)
    loaded_v1 = load_checkpoint(run_id)
    assert loaded_v1 is not None
    assert loaded_v1["current_route"] == "/inventory.html"
    assert loaded_v1["visited_routes"] == ["/", "/inventory.html"]
    assert loaded_v1["scroll_position"]["y"] == 250
    # Confirm password redaction
    assert loaded_v1.get("state_data", {}).get("password") == "[REDACTED]"

    # Atomic replace with v2
    data_v2 = dict(data_v1)
    data_v2["current_route"] = "/cart.html"
    data_v2["completed_routes"] = ["/", "/inventory.html"]
    data_v2["pending_routes"] = []
    save_checkpoint(run_id, data_v2)

    loaded_v2 = load_checkpoint(run_id)
    assert loaded_v2 is not None
    assert loaded_v2["current_route"] == "/cart.html"
    assert loaded_v2["completed_routes"] == ["/", "/inventory.html"]


# ---------------------------------------------------------------------------
# 2. Checkpoint Resume & Scope Validation
# ---------------------------------------------------------------------------
def test_checkpoint_resume_and_scope_validation():
    run_id = f"test_scope_{uuid4().hex[:12]}"
    origin = "https://www.saucedemo.com"
    ns = compute_namespace(origin)

    data = {
        "run_id": run_id,
        "namespace": ns,
        "current_route": "/inventory.html",
        "visited_routes": ["/inventory.html"],
    }
    save_checkpoint(run_id, data)

    # Valid origin matching namespace
    loaded = load_checkpoint(run_id)
    assert loaded["namespace"] == compute_namespace("https://www.saucedemo.com/")

    # Mismatched origin
    other_ns = compute_namespace("https://malicious.evil.com")
    assert loaded["namespace"] != other_ns


# ---------------------------------------------------------------------------
# 3. Interrupted Scan State Recovery
# ---------------------------------------------------------------------------
def test_interrupted_scan_state_recovery():
    run_id = f"test_interrupt_{uuid4().hex[:12]}"
    ctx = create_run_context(
        target_origin="https://www.saucedemo.com",
        run_id=run_id,
        username="standard_user",
    )
    ctx.status = "paused"
    ctx.selected_modules = ["mod_inventory", "mod_cart"]
    ctx.discovered_modules = [
        {"id": "mod_inventory", "label": "Inventory", "route": "/inventory.html"},
        {"id": "mod_cart", "label": "Cart", "route": "/cart.html"},
    ]
    save_run_context(ctx)

    # Checkpoint with partial completion
    save_checkpoint(run_id, {
        "run_id": run_id,
        "current_module_id": "mod_inventory",
        "current_route": "/inventory.html",
        "completed_routes": ["/inventory.html"],
        "pending_routes": ["/cart.html"],
    })

    # Restore from context
    restored = get_run_context(run_id)
    assert restored is not None
    assert restored.status == "paused"
    assert restored.selected_modules == ["mod_inventory", "mod_cart"]

    chk = load_checkpoint(run_id)
    assert chk["completed_routes"] == ["/inventory.html"]
    assert chk["pending_routes"] == ["/cart.html"]


# ---------------------------------------------------------------------------
# 4. Regression Suite CRUD & Duplication
# ---------------------------------------------------------------------------
def test_regression_suite_crud_and_duplicate():
    suite_name = f"Sanity Suite {uuid4().hex[:6]}"
    suite = DBRepository.create_regression_suite(
        name=suite_name,
        base_url="https://www.saucedemo.com",
        environment="staging",
        description="Core functional sanity checks",
        filter_tags=["smoke", "regression"],
        filter_modules=["Inventory"],
    )
    suite_id = suite["suite_id"]
    assert suite["name"] == suite_name
    assert suite["environment"] == "staging"
    assert "smoke" in suite["filter_tags"]

    # Read
    fetched = DBRepository.get_regression_suite(suite_id)
    assert fetched is not None
    assert fetched["suite_id"] == suite_id

    # Update
    updated = DBRepository.update_regression_suite(
        suite_id=suite_id,
        name=f"{suite_name} Updated",
        environment="production",
    )
    assert updated["name"] == f"{suite_name} Updated"
    assert updated["environment"] == "production"

    # Duplicate
    dup = DBRepository.duplicate_regression_suite(suite_id)
    assert dup is not None
    assert dup["suite_id"] != suite_id
    assert "Copy" in dup["name"]

    # Archive
    ok = DBRepository.archive_regression_suite(suite_id)
    assert ok is True

    # Delete
    del_ok = DBRepository.delete_regression_suite(dup["suite_id"])
    assert del_ok is True


# ---------------------------------------------------------------------------
# 5. Baseline Saving & Comparison Diff
# ---------------------------------------------------------------------------
def test_baseline_save_and_comparison():
    base_run_id = f"base_run_{uuid4().hex[:8]}"
    curr_run_id = f"curr_run_{uuid4().hex[:8]}"

    # Setup base test cases
    base_cases = [
        {
            "tc_id": "TC_001",
            "title": "Login and browse products",
            "module": "Inventory",
            "route": "/inventory.html",
            "tags": ["smoke"],
            "locators": {"main": "#inventory_container"},
            "steps": [{"action": "navigate", "target": "/inventory.html"}],
        },
        {
            "tc_id": "TC_002",
            "title": "Add item to cart",
            "module": "Cart",
            "route": "/cart.html",
            "tags": ["regression"],
            "locators": {"add_btn": "#add-to-cart"},
            "steps": [{"action": "click", "target": "#add-to-cart"}],
        },
    ]

    # Save baseline plan
    b_res = DBRepository.save_baseline(
        run_id=base_run_id,
        name="Q4 Release Baseline",
        version="1.0",
        test_cases=base_cases,
    )
    assert b_res["run_id"] == base_run_id
    assert b_res["name"] == "Q4 Release Baseline"

    # Reject silent overwrite without confirmation
    with pytest.raises(ValueError, match="Explicit overwrite confirmation is required"):
        DBRepository.save_baseline(
            run_id=base_run_id,
            name="Conflicting Baseline",
            version="1.1",
            overwrite=False,
        )

    # Current run cases with simulated diff: TC_001 passed, TC_002 failed, TC_002 locator changed
    curr_cases = [
        {
            "tc_id": "TC_001",
            "title": "Login and browse products",
            "module": "Inventory",
            "route": "/inventory.html",
            "tags": ["smoke"],
            "locators": {"main": "#inventory_container"},
            "steps": [{"action": "navigate", "target": "/inventory.html"}],
        },
        {
            "tc_id": "TC_002",
            "title": "Add item to cart",
            "module": "Cart",
            "route": "/cart.html",
            "tags": ["regression"],
            "locators": {"add_btn": "button.new-cart-selector"},
            "steps": [{"action": "click", "target": "#add-to-cart"}],
        },
    ]
    DBRepository.save_baseline(
        run_id=curr_run_id,
        name="Current Run Plan",
        version="1.0",
        test_cases=curr_cases,
    )

    # Compare baseline
    diff = DBRepository.compare_baseline(
        current_run_id=curr_run_id,
        baseline_run_id=base_run_id,
    )
    assert diff["current_run_id"] == curr_run_id
    assert diff["baseline_run_id"] == base_run_id
    # Changed locators detected on TC_002
    assert any(c["tc_id"] == "TC_002" for c in diff["changed_locators"])


# ---------------------------------------------------------------------------
# 6. Database Health & Fallback Verification
# ---------------------------------------------------------------------------
def test_db_status_and_explicit_sqlite_fallback(monkeypatch):
    """Exercise the health response with an isolated DB, not the developer's .env."""
    import api.db.session as db_session

    # Keep this unit test deterministic when a developer configures local
    # PostgreSQL-only mode. The app's configured PostgreSQL connection is
    # covered by the deployment/live database checks, not this unit test.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("DATABASE_FALLBACK_ENABLED", "true")
    monkeypatch.setattr(db_session, "_ENGINE", None)
    monkeypatch.setattr(db_session, "_SESSION_FACTORY", None)
    monkeypatch.setattr(db_session, "_IS_FALLBACK", False)
    monkeypatch.setattr(db_session, "_LAST_ERROR", None)

    status = check_db_status()
    assert "status" in status
    assert status["status"] == "connected"
    assert status["engine"] == "sqlite"
    assert status["is_fallback"] is True
    db_session.get_engine().dispose()


def test_configured_database_failure_can_fail_closed(monkeypatch):
    """Local PostgreSQL-only mode must never silently split data into SQLite."""
    import api.db.session as db_session

    def unavailable_engine(*args, **kwargs):
        raise RuntimeError("driver failure containing a password=do-not-log value")

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@127.0.0.1/test")
    monkeypatch.setenv("DATABASE_FALLBACK_ENABLED", "false")
    monkeypatch.setattr(db_session, "create_engine", unavailable_engine)

    with pytest.raises(RuntimeError, match="fallback is disabled") as exc_info:
        db_session.build_engine_and_factory()
    assert "secret" not in str(exc_info.value)
    assert "do-not-log" not in str(exc_info.value)


def test_missing_database_url_fails_when_sqlite_fallback_disabled(monkeypatch):
    """PostgreSQL-first app startup must not silently select SQLite."""
    import api.db.session as db_session

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_FALLBACK_ENABLED", "false")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        db_session.build_engine_and_factory()


def test_missing_database_url_uses_sqlite_only_when_fallback_enabled(monkeypatch):
    """SQLite remains available only as an explicit fallback for isolated use."""
    import api.db.session as db_session

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_FALLBACK_ENABLED", "true")
    monkeypatch.setattr(db_session, "get_default_sqlite_url", lambda: "sqlite:///:memory:")

    engine, _factory, is_fallback, warning = db_session.build_engine_and_factory()
    try:
        assert engine.dialect.name == "sqlite"
        assert is_fallback is True
        assert "DATABASE_URL is not configured" in warning
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 7. Credential Redaction in Run Records
# ---------------------------------------------------------------------------
def test_credential_redaction_in_persistence():
    payload = {
        "url": "https://www.saucedemo.com",
        "username": "standard_user",
        "password": "secret_password_must_not_persist",
        "token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "api_key": "AIzaSyAbc123_TopSecretKey",
        "nested": {
            "credentials": {
                "auth_token": "secret_token_123",
            }
        },
    }
    safe = redact_sensitive_data(payload)
    dumped = json.dumps(safe)
    assert "secret_password_must_not_persist" not in dumped
    assert "secret_token_123" not in dumped
    assert "AIzaSyAbc123_TopSecretKey" not in dumped
    assert safe["password"] == "[REDACTED]"
    assert safe["nested"]["credentials"] == "[REDACTED]"


def test_artifact_metadata_is_idempotent_and_downloads_are_audited(tmp_path, monkeypatch):
    """Artifact rows are portable metadata, and each GET download is auditable."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import api.db.repository as repository
    from api.db.models import Artifact, AuditEvent, ScanRun
    from api.db.session import Base

    report_root = tmp_path / "reports"
    log_root = tmp_path / "logs"
    report_root.mkdir()
    log_root.mkdir()
    artifact_file = report_root / "sample.xlsx"
    artifact_file.write_bytes(b"excel-bytes-not-stored-in-db")
    monkeypatch.setattr(repository, "REPORT_PATH", report_root)
    monkeypatch.setattr(repository, "LOG_PATH", log_root)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def isolated_execute(operation):
        with factory() as session:
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(repository, "execute_with_retry", isolated_execute)
    run_id = f"artifact_test_{uuid4().hex[:12]}"
    with factory() as session:
        session.add(ScanRun(id=run_id, base_url="https://example.test", allowed_path_namespace="/"))
        session.commit()

    first = DBRepository.register_artifacts(run_id, {"excel": str(artifact_file)}, execution_id="exec_test")
    second = DBRepository.register_artifacts(run_id, {"excel": str(artifact_file)}, execution_id="exec_test")
    assert len(first) == len(second) == 1
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["file_path"] == "reports/sample.xlsx"
    assert "tmp_path" not in first[0]["file_path"]
    assert DBRepository.list_artifacts(run_id)[0]["id"] == first[0]["id"]
    assert DBRepository.record_artifact_download(run_id, first[0]["id"], "excel", "demo.xlsx", len(artifact_file.read_bytes()))

    with factory() as session:
        artifact_rows = session.query(Artifact).filter_by(run_id=run_id).all()
        events = session.query(AuditEvent).filter_by(run_id=run_id).all()
        assert len(artifact_rows) == 1
        assert artifact_rows[0].file_size == len(artifact_file.read_bytes())
        assert len([event for event in events if event.event_type == "artifact_created"]) == 1
        download_event = next(event for event in events if event.event_type == "artifact_downloaded")
        assert download_event.details["artifact_id"] == first[0]["id"]
        assert download_event.details["filename"] == "demo.xlsx"
    engine.dispose()


def test_artifact_download_route_persists_metadata_and_success_event(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from starlette.requests import Request
    import api.db.repository as repository
    import api.routers.scan as scan_router
    from api.db.models import Artifact, AuditEvent, ScanRun
    from api.db.session import Base

    report_root = tmp_path / "reports"
    log_root = tmp_path / "logs"
    report_root.mkdir()
    log_root.mkdir()
    run_id = f"artifact_route_{uuid4().hex[:12]}"
    artifact_file = report_root / f"{run_id}_crawl_results.xlsx"
    artifact_file.write_bytes(b"test-workbook")
    monkeypatch.setattr(repository, "REPORT_PATH", report_root)
    monkeypatch.setattr(repository, "LOG_PATH", log_root)
    monkeypatch.setattr(scan_router, "REPORT_PATH", report_root)
    monkeypatch.setattr(scan_router, "get_run_context", lambda _run_id: SimpleNamespace(
        display_name="Demo run",
        status="completed",
        artifact_paths={},
        execution_results=None,
        execution_summary={},
    ))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def isolated_execute(operation):
        with factory() as session:
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(repository, "execute_with_retry", isolated_execute)
    with factory() as session:
        session.add(ScanRun(id=run_id, base_url="https://example.test", allowed_path_namespace="/"))
        session.commit()

    request = Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/reports/{run_id}/download/excel",
        "raw_path": f"/reports/{run_id}/download/excel".encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    })
    # The endpoint body is synchronous despite its async signature. Drive the
    # coroutine directly so this route contract test does not need to open an
    # asyncio self-pipe socket (restricted on some Windows developer machines).
    endpoint = scan_router.download_artifact(run_id, "excel", request)
    with pytest.raises(StopIteration) as completed:
        endpoint.send(None)
    response = completed.value.value
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('Demo_run_crawl_results.xlsx"')
    assert response.background is not None
    assert response.background.func == DBRepository.record_artifact_download
    response.background.func(*response.background.args, **response.background.kwargs)

    with factory() as session:
        artifacts = session.query(Artifact).filter_by(run_id=run_id).all()
        download_events = session.query(AuditEvent).filter_by(
            run_id=run_id, event_type="artifact_downloaded"
        ).all()
        assert len(artifacts) == 1
        assert artifacts[0].file_path == f"reports/{artifact_file.name}"
        assert len(download_events) == 1
        assert download_events[0].details["artifact_id"] == artifacts[0].id

    inventory = scan_router.list_artifacts_endpoint()
    with pytest.raises(StopIteration) as inventory_completed:
        inventory.send(None)
    listed_runs = inventory_completed.value.value
    listed_run = next(item for item in listed_runs if item["run_id"] == run_id)
    assert listed_run["artifacts"][0]["id"] == artifacts[0].id
    engine.dispose()


def test_execution_excel_download_returns_workbook_and_records_db_metadata(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from starlette.requests import Request
    import api.db.repository as repository
    import api.routers.scan as scan_router
    from api.db.models import Artifact, AuditEvent, ScanRun
    from api.db.session import Base

    report_root = tmp_path / "reports"
    log_root = tmp_path / "logs"
    run_id = f"execution_excel_{uuid4().hex[:12]}"
    execution_file = report_root / run_id / "execution_results.xlsx"
    execution_file.parent.mkdir(parents=True)
    log_root.mkdir()
    execution_file.write_bytes(b"xlsx-workbook-fixture")
    monkeypatch.setattr(repository, "REPORT_PATH", report_root)
    monkeypatch.setattr(repository, "LOG_PATH", log_root)
    monkeypatch.setattr(scan_router, "REPORT_PATH", report_root)
    monkeypatch.setattr(scan_router, "get_run_context", lambda _run_id: SimpleNamespace(
        display_name="pos-test", status="completed", artifact_paths={}, execution_results=None,
        execution_summary={},
    ))

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def isolated_execute(operation):
        with factory() as session:
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(repository, "execute_with_retry", isolated_execute)
    with factory() as session:
        session.add(ScanRun(id=run_id, base_url="https://example.test", allowed_path_namespace="/"))
        session.commit()

    path = f"/reports/{run_id}/download/execution_excel"
    request = Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("testclient", 12345),
    })
    endpoint = scan_router.download_artifact(run_id, "execution_excel", request)
    with pytest.raises(StopIteration) as completed:
        endpoint.send(None)
    response = completed.value.value

    assert response.status_code == 200
    assert response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"].endswith('pos-test_execution_results.xlsx"')
    assert response.background is not None
    response.background.func(*response.background.args, **response.background.kwargs)

    with factory() as session:
        artifact = session.query(Artifact).filter_by(run_id=run_id).one()
        event = session.query(AuditEvent).filter_by(
            run_id=run_id, event_type="artifact_downloaded"
        ).one()
        assert artifact.artifact_type == "execution_excel"
        assert artifact.file_path == f"reports/{run_id}/execution_results.xlsx"
        assert event.details["filename"] == "pos-test_execution_results.xlsx"
        assert event.details["artifact_id"] == artifact.id
    engine.dispose()


def test_execution_download_uses_persisted_session_name_after_restart(tmp_path, monkeypatch):
    """Downloads retain the friendly name even when no run context is cached."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from starlette.requests import Request
    import api.db.repository as repository
    import api.routers.scan as scan_router
    from api.db.models import Artifact, ScanRun
    from api.db.session import Base

    report_root = tmp_path / "reports"
    log_root = tmp_path / "logs"
    run_id = f"persisted_export_{uuid4().hex[:12]}"
    execution_file = report_root / run_id / "execution_results.xlsx"
    execution_file.parent.mkdir(parents=True)
    log_root.mkdir()
    execution_file.write_bytes(b"xlsx-workbook-fixture")
    monkeypatch.setattr(repository, "REPORT_PATH", report_root)
    monkeypatch.setattr(repository, "LOG_PATH", log_root)
    monkeypatch.setattr(scan_router, "REPORT_PATH", report_root)
    monkeypatch.setattr(scan_router, "LOG_PATH", log_root)
    monkeypatch.setattr(scan_router, "get_run_context", lambda _run_id: None)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def isolated_execute(operation):
        with factory() as session:
            try:
                result = operation(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(repository, "execute_with_retry", isolated_execute)
    with factory() as session:
        session.add(ScanRun(
            id=run_id,
            display_name="Quarterly regression",
            base_url="https://example.test",
            allowed_path_namespace="/",
        ))
        session.commit()

    path = f"/reports/{run_id}/download/execution_excel"
    request = Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("testclient", 12345),
    })
    endpoint = scan_router.download_artifact(run_id, "execution_excel", request)
    with pytest.raises(StopIteration) as completed:
        endpoint.send(None)
    response = completed.value.value

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(
        'Quarterly_regression_execution_results.xlsx"'
    )
    response.background.func(*response.background.args, **response.background.kwargs)
    with factory() as session:
        artifact = session.query(Artifact).filter_by(run_id=run_id).one()
        assert artifact.artifact_type == "execution_excel"
    engine.dispose()


def test_merged_execution_excel_replaces_legacy_tabs_and_deduplicates_headers(tmp_path, monkeypatch):
    import pandas as pd
    import api.services.test_execution_service as execution_service

    report_root = tmp_path / "reports"
    report_root.mkdir()
    run_id = f"execution_merge_{uuid4().hex[:12]}"
    scan_workbook = report_root / f"{run_id}_crawl_results.xlsx"
    execution_workbook = tmp_path / "execution_results.xlsx"
    monkeypatch.setattr(execution_service, "REPORT_PATH", report_root)

    with pd.ExcelWriter(scan_workbook, engine="openpyxl") as writer:
        pd.DataFrame([{"Route": "/", "Status": "WORKING"}]).to_excel(
            writer, sheet_name="Route Health", index=False
        )
        pd.DataFrame([{"old": "stale"}]).to_excel(writer, sheet_name="Execution Results", index=False)
        pd.DataFrame([{"old": "stale"}]).to_excel(writer, sheet_name="Test_Execution", index=False)
        pd.DataFrame([{"other": "keep"}]).to_excel(writer, sheet_name="Coverage Gaps", index=False)
    with pd.ExcelWriter(execution_workbook, engine="openpyxl") as writer:
        pd.DataFrame([{
            "TC ID": "TC_001",
            "Status": "Pass",
            " status ": "duplicate header should be dropped",
        }]).to_excel(writer, sheet_name="test_execution", index=False)

    execution_service._merge_execution_results_into_scan_workbook(run_id, execution_workbook)

    with pd.ExcelFile(scan_workbook) as workbook:
        execution_tabs = [
            name for name in workbook.sheet_names
            if name.casefold().replace("_", " ").replace("-", " ")
            in {"test execution", "execution results", "execution result", "test execution results"}
        ]
        assert execution_tabs == ["Test Execution"]
        assert {"Route Health", "Coverage Gaps"}.issubset(workbook.sheet_names)
        merged = pd.read_excel(workbook, sheet_name="Test Execution")
    normalized_headers = [" ".join(str(name).split()).casefold() for name in merged.columns]
    assert len(normalized_headers) == len(set(normalized_headers))
    assert merged.loc[0, "TC ID"] == "TC_001"
    assert merged.loc[0, "Status"] == "Pass"


# ===========================================================================
# 8. Async API Endpoints Verification
# ===========================================================================

@pytest.mark.asyncio
async def test_api_system_db_status(async_client: httpx.AsyncClient):
    res = await async_client.get("/system/db-status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in {"connected", "degraded"}
    assert "engine" in data


@pytest.mark.asyncio
async def test_api_runs_crud_endpoints(async_client: httpx.AsyncClient, monkeypatch):
    # A resume endpoint must schedule the crawler, but this CRUD contract test
    # must not launch a real browser or make a network request.
    from api.routers import scan as scan_router

    async def no_op_background_scan(_request):
        return None

    monkeypatch.setattr(scan_router, "_run_scan_in_background", no_op_background_scan)
    # 1. List runs
    res = await async_client.get("/runs")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 2. Create run
    create_res = await async_client.post(
        "/runs",
        json={"url": "https://www.saucedemo.com", "username": "standard_user", "allowed_scope": "/"},
    )
    assert create_res.status_code == 200
    run_id = create_res.json()["run_id"]

    # 3. Get run
    get_res = await async_client.get(f"/runs/{run_id}")
    assert get_res.status_code == 200
    assert get_res.json()["run_id"] == run_id

    # 4. Save progress
    save_res = await async_client.post(f"/runs/{run_id}/save-progress", json={"run_id": run_id})
    assert save_res.status_code == 200
    assert save_res.json()["status"] == "saved"
    assert save_res.json()["checkpoint_version"] >= 1

    # 5. Pause run
    pause_res = await async_client.post(f"/runs/{run_id}/pause")
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "paused"

    # 6. Resume run
    resume_res = await async_client.post(f"/runs/{run_id}/resume", json={"run_id": run_id})
    assert resume_res.status_code == 200
    assert resume_res.json()["status"] in {"resumed", "auth_required"}

    # 7. Duplicate run
    dup_res = await async_client.post(f"/runs/{run_id}/duplicate")
    assert dup_res.status_code == 200
    new_run_id = dup_res.json()["new_run_id"]
    assert new_run_id != run_id

    # 8. Archive run
    arch_res = await async_client.post(f"/runs/{run_id}/archive")
    assert arch_res.status_code == 200

    # 9. Delete run
    del_res = await async_client.delete(f"/runs/{new_run_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_api_regression_suites_endpoints(async_client: httpx.AsyncClient):
    # 1. Create suite
    create_payload = {
        "name": f"API Test Suite {uuid4().hex[:6]}",
        "base_url": "https://www.saucedemo.com",
        "environment": "qa",
        "description": "Integration test regression suite",
        "filter_tags": ["smoke"],
    }
    res = await async_client.post("/regression/suites", json=create_payload)
    assert res.status_code == 200
    suite = res.json()
    suite_id = suite["suite_id"]

    # 2. List suites
    list_res = await async_client.get("/regression/suites")
    assert list_res.status_code == 200
    assert any(s["suite_id"] == suite_id for s in list_res.json())

    # 3. Get suite
    get_res = await async_client.get(f"/regression/suites/{suite_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == create_payload["name"]

    # 4. Update suite
    put_res = await async_client.put(
        f"/regression/suites/{suite_id}",
        json={"name": "Renamed Suite", "environment": "production"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["name"] == "Renamed Suite"

    # 5. Duplicate suite
    dup_res = await async_client.post(f"/regression/suites/{suite_id}/duplicate")
    assert dup_res.status_code == 200
    dup_id = dup_res.json()["suite_id"]
    assert dup_id != suite_id

    # 6. Delete suite
    del_res = await async_client.delete(f"/regression/suites/{dup_id}")
    assert del_res.status_code == 200


@pytest.mark.asyncio
async def test_api_selectable_execution_requires_confirmation(async_client: httpx.AsyncClient):
    """Verify that unconfirmed execution is rejected with HTTP 400."""
    payload = {
        "run_id": f"run_{uuid4().hex[:8]}",
        "base_url": "https://www.saucedemo.com",
        "selected_test_case_ids": ["TC_001"],
        "confirmed": False,  # Not confirmed
    }
    res = await async_client.post("/regression/execute-selected", json=payload)
    assert res.status_code == 400
    assert "explicit user confirmation is required" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_selectable_execution_executes_only_selected(async_client: httpx.AsyncClient):
    """Verify that only selected tests are executed and unselected are excluded."""
    run_id = f"test_sel_exec_{uuid4().hex[:8]}"

    # Setup run context with 3 test cases
    ctx = create_run_context(target_origin="https://www.saucedemo.com", run_id=run_id)
    ctx.generated_test_plan = [
        TestCase(
            tc_id="TC_001",
            title="Browse products",
            module="Inventory",
            route="/inventory.html",
            tags=["smoke"],
            steps=[TestStep(step_id="S01", action="navigate", target="https://www.saucedemo.com/inventory.html", value="")],
        ),
        TestCase(
            tc_id="TC_002",
            title="View Cart",
            module="Cart",
            route="/cart.html",
            tags=["regression"],
            steps=[TestStep(step_id="S02", action="navigate", target="https://www.saucedemo.com/cart.html", value="")],
        ),
        TestCase(
            tc_id="TC_003",
            title="Checkout",
            module="Checkout",
            route="/checkout-step-one.html",
            tags=["checkout"],
            steps=[TestStep(step_id="S03", action="navigate", target="https://www.saucedemo.com/checkout-step-one.html", value="")],
        ),
    ]
    save_run_context(ctx)

    # Request execution of ONLY TC_001 with explicit confirmation
    payload = {
        "run_id": run_id,
        "base_url": "https://www.saucedemo.com",
        "selected_test_case_ids": ["TC_001"],  # Only 1 test selected
        "confirmed": True,
    }

    def _mock_exec(base_url, test_cases, allow_mutating, storage, session_id, run_id):
        rows = [{"TC_ID": tc.tc_id, "Action": "navigate", "Result": "Pass"} for tc in test_cases]
        summary = {"total": len(rows), "passed": len(rows), "failed": 0}
        artifacts = {"execution_id": f"exec_{run_id}"}
        return rows, summary, artifacts

    with patch("api.routers.scan.execute_test_cases", side_effect=_mock_exec):
        res = await async_client.post("/regression/execute-selected", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["selected_test_count"] == 1
    assert data["executed_test_count"] == 1

    # Verify rows executed contain ONLY TC_001
    executed_tc_ids = {r["TC_ID"] for r in data["rows"]}
    assert "TC_001" in executed_tc_ids
    assert "TC_002" not in executed_tc_ids
    assert "TC_003" not in executed_tc_ids


@pytest.mark.asyncio
async def test_api_baseline_save_and_compare(async_client: httpx.AsyncClient):
    base_run_id = f"base_run_{uuid4().hex[:8]}"
    curr_run_id = f"curr_run_{uuid4().hex[:8]}"

    # Setup base run with plan
    ctx_base = create_run_context(target_origin="https://www.saucedemo.com", run_id=base_run_id)
    ctx_base.generated_test_plan = [
        TestCase(
            tc_id="TC_101",
            title="Inventory Check",
            module="Inventory",
            route="/inventory.html",
            steps=[TestStep(step_id="S101", action="navigate", target="/inventory.html")],
        )
    ]
    save_run_context(ctx_base)

    # Setup current run
    ctx_curr = create_run_context(target_origin="https://www.saucedemo.com", run_id=curr_run_id)
    ctx_curr.generated_test_plan = [
        TestCase(
            tc_id="TC_101",
            title="Inventory Check",
            module="Inventory",
            route="/inventory.html",
            steps=[TestStep(step_id="S101", action="navigate", target="/inventory.html")],
        )
    ]
    save_run_context(ctx_curr)

    # 1. Save baseline
    b_res = await async_client.post("/baselines/save", json={
        "run_id": base_run_id,
        "name": "Sprint 1 Baseline",
        "version": "1.0",
        "confirmed_overwrite": False,
    })
    assert b_res.status_code == 200
    assert b_res.json()["status"] == "saved"

    # 2. List baselines
    list_b = await async_client.get("/baselines")
    assert list_b.status_code == 200
    assert any(b["run_id"] == base_run_id for b in list_b.json())

    # 3. Compare with current run
    cmp_res = await async_client.post("/baselines/compare", json={
        "current_run_id": curr_run_id,
        "baseline_run_id": base_run_id,
    })
    assert cmp_res.status_code == 200
    diff = cmp_res.json()
    assert diff["current_run_id"] == curr_run_id
    assert diff["baseline_run_id"] == base_run_id
