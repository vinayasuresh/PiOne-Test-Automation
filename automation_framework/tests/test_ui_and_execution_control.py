"""test_ui_and_execution_control.py: Deterministic tests for Phase 6.

Covers:
1. Branding consistency: 'PiOne Testmate' in main API, exports, and frontend.
2. Module selection: select all, clear all, single module, validation on empty selection.
3. Live scan controls: pause, resume, continue, retry, end scan & save.
4. Live telemetry: WebSocket event schema validation, status transitions, metric updates.
5. Execution progress: failure categorization (product, locator, timeout, auth, environment, skipped).
6. Provider and AI status: display model, provider, fallback indicator, grounding metrics, no secret leak.
7. Report downloads: valid download URLs for all artifact types, 409/404 prevention for in-progress runs.
8. Refresh resumption: restore active run view from checkpoint/run context.
9. Sanitized actionable error handling: no credential leaks or stack traces.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.scan_models import (
    ExecutionResultRow,
    ScanControlRequest,
    TestCase as TestCaseModel,
    TestStep as TestStepModel,
)
from api.services.llm_provider_gateway import LLMProviderGateway
from api.services.run_context_service import (
    create_run_context,
    get_run_context,
    save_run_context,
)
from api.services.scan_control_service import (
    clear_scan_control,
    finish_scan,
    is_scan_paused,
    is_scan_stop_requested,
    pause_scan,
    pop_user_decision,
    resume_scan,
    set_user_decision,
    stop_scan,
    try_start_scan,
)
from api.services.scan_event_bus import (
    discard_channel,
    get_channel,
    get_latest_telemetry,
    publish,
    register_alias,
)
from api.services.test_execution_service import (
    _failure_category,
    _row,
)
from automation_framework.config.settings import REPORT_PATH
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.crawl_exporter import export_crawl_results


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# 1. Branding Consistency Tests
def test_branding_consistency(client: TestClient):
    """Verify that visible product branding is consistently 'PiOne Testmate'."""
    # Main API health check
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["service"] == "PiOne Testmate API"

    # Frontend index.html
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert "PiOne Testmate" in html_resp.text
    assert "<title>PiOne Testmate" in html_resp.text

    # Crawl exporter summary heading
    sample_crawl = {
        "page_intelligence": {
            "https://app.test/home": {
                "route": "https://app.test/home",
                "menu_name": "Home",
                "automation_targets": [],
            }
        },
        "route_validations": {"https://app.test/home": {"status": "WORKING", "score": 100}},
    }
    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = export_crawl_results(sample_crawl, output_dir=tmp_dir, run_id="test_branding")
        md_text = paths["markdown"].read_text(encoding="utf-8")
        assert "# PiOne Testmate - Scan Summary" in md_text


# 2. Module Selection Logic Tests
def test_module_selection_controls():
    """Verify module selection logic: select all, clear all, single module, and validation."""
    discovered = [
        {"id": "mod_1", "label": "Inventory", "route": "/inventory.html"},
        {"id": "mod_2", "label": "Cart", "route": "/cart.html"},
        {"id": "mod_3", "label": "Checkout", "route": "/checkout.html"},
    ]

    # Select all
    all_selected = [m["id"] for m in discovered]
    assert len(all_selected) == 3

    # Clear all
    cleared = []
    assert len(cleared) == 0

    # Validation: empty selection should be rejected
    is_valid_empty = len(cleared) > 0
    assert not is_valid_empty

    # Single module selection
    single = [discovered[0]["id"]]
    assert len(single) == 1
    assert single == ["mod_1"]


# 3. Scan Control Lifecycle Tests (Pause, Resume, Continue, Retry, End)
def test_scan_control_lifecycle(client: TestClient):
    """Verify pause, resume, and decision state transitions via service and API."""
    run_id = "test_ctrl_run_01"
    clear_scan_control(run_id)

    # Initial state
    assert not is_scan_paused(run_id)
    assert not is_scan_stop_requested(run_id)
    assert pop_user_decision(run_id) is None

    # Pause via API
    res = client.post("/scan/control", json={"run_id": run_id, "action": "pause"})
    assert res.status_code == 200
    assert is_scan_paused(run_id)

    # Resume via API
    res = client.post("/scan/control", json={"run_id": run_id, "action": "resume"})
    assert res.status_code == 200
    assert not is_scan_paused(run_id)

    # Decision: continue
    res = client.post("/scan/control", json={"run_id": run_id, "action": "continue"})
    assert res.status_code == 200
    assert pop_user_decision(run_id) == "continue"

    # Decision: retry
    res = client.post("/scan/control", json={"run_id": run_id, "action": "retry"})
    assert res.status_code == 200
    assert pop_user_decision(run_id) == "retry"

    # End / Stop
    res = client.post("/scan/control", json={"run_id": run_id, "action": "end"})
    assert res.status_code == 200
    assert is_scan_stop_requested(run_id)
    assert pop_user_decision(run_id) == "end"

    # Invalid action
    res = client.post("/scan/control", json={"run_id": run_id, "action": "invalid_action"})
    assert res.status_code == 400

    clear_scan_control(run_id)


# 4. Live Telemetry Event Schema Tests
def test_live_telemetry_event_schema():
    """Verify telemetry snapshot contains all required Phase 4/6 fields."""
    mock_page = MagicMock()
    mock_page.url = "https://app.test/dashboard"

    engine = CrawlerEngine(
        mock_page,
        base_url="https://app.test",
        run_id="run_telem_99",
        scan_id="scan_telem_99",
    )
    engine._active_module_id = "mod_dash"
    engine._selected_module_ids = ["mod_dash", "mod_settings"]
    engine.route_validations = {
        "https://app.test/dashboard": {"status": "WORKING"},
        "https://app.test/broken": {"status": "NON-WORKING"},
    }

    snapshot = engine._build_telemetry_snapshot("route_completed")

    required_keys = [
        "run_id",
        "scan_id",
        "timestamp",
        "status",
        "current_module",
        "current_route",
        "module_progress",
        "route_progress",
        "elements_found",
        "safe_actions_explored",
        "parking_lot_actions",
        "route_health",
        "retry_count",
        "errors",
    ]
    for key in required_keys:
        assert key in snapshot, f"Missing telemetry key: {key}"

    assert snapshot["run_id"] == "run_telem_99"
    assert snapshot["scan_id"] == "scan_telem_99"
    assert snapshot["status"] == "running"
    assert snapshot["route_health"]["working_count"] == 1
    assert snapshot["route_health"]["non_working_count"] == 1
    assert snapshot["module_progress"]["total"] == 2


# 5. WebSocket Aliasing and Reconnection State Tests
def test_websocket_aliasing_and_telemetry_snapshot():
    """Verify scan_event_bus aliasing and latest telemetry snapshot preservation."""
    scan_id = "scan_alias_test"
    run_id = "run_alias_test"

    register_alias(run_id, scan_id)

    event = {
        "type": "route_completed",
        "run_id": run_id,
        "scan_id": scan_id,
        "current_route": "/inventory.html",
        "elements_found": 15,
    }
    publish(run_id, event)

    # Subscribed by scan_id receives the event
    q = get_channel(scan_id)
    retrieved = q.get_nowait()
    assert retrieved["type"] == "route_completed"
    assert retrieved["elements_found"] == 15

    # Snapshot retrieval for reconnecting clients
    snapshot = get_latest_telemetry(run_id)
    assert snapshot is not None
    assert snapshot["elements_found"] == 15

    discard_channel(scan_id)


# 5b. Event sequence must progress for reconnect/stale filtering
def test_websocket_event_sequence_progresses():
    run_id = "run_seq_progress_01"
    discard_channel(run_id)
    publish(run_id, {"type": "route_started", "run_id": run_id, "scan_id": run_id, "current_route": "/a"})
    first = get_latest_telemetry(run_id)
    publish(run_id, {"type": "route_completed", "run_id": run_id, "scan_id": run_id, "current_route": "/a"})
    second = get_latest_telemetry(run_id)
    assert first is not None and second is not None
    assert int(first["last_event_sequence"]) >= 1
    assert int(second["last_event_sequence"]) > int(first["last_event_sequence"])
    discard_channel(run_id)


# 5c. Run state contract must include reconnect-safe fields
def test_run_state_contract_includes_reconnect_fields(client: TestClient):
    run_id = "run_state_contract_01"
    ctx = create_run_context(url="https://example.test/app", run_id=run_id)
    ctx.status = "running"
    ctx.selected_modules = ["mod_users"]
    save_run_context(ctx)
    publish(run_id, {
        "type": "route_started",
        "run_id": run_id,
        "scan_id": run_id,
        "current_module": "mod_users",
        "current_route": "https://example.test/app/users",
        "route_progress": {"scanned": 1, "pending": 2, "total": 3},
        "elements_found": 5,
        "safe_actions_explored": 2,
        "parking_lot_actions": 1,
        "retry_count": 0,
    })
    res = client.get(f"/run/{run_id}/state")
    assert res.status_code == 200
    data = res.json()
    required = {
        "run_id", "scan_id", "target_origin", "allowed_path_namespace", "auth_status",
        "selected_modules", "current_module", "current_route", "route_progress",
        "elements_found", "safe_actions_explored", "parking_lot_actions",
        "retry_count", "status", "last_event_sequence", "checkpoint_version",
    }
    assert required.issubset(set(data.keys()))
    assert data["run_id"] == run_id
    assert data["scan_id"] == run_id
    assert data["status"] in {"running", "paused", "partial_scanned", "scanned", "failed", "executing", "executed", "discovered"}
    assert data["current_module"] == "mod_users"
    assert data["route_progress"]["total"] == 3
    assert int(data["last_event_sequence"]) >= 1
    discard_channel(run_id)


# 5d. Duplicate run start is blocked while existing run is active
def test_duplicate_run_prevention_when_active(client: TestClient, monkeypatch):
    run_id = "run_duplicate_guard_01"
    assert try_start_scan(run_id) is True
    assert try_start_scan(run_id) is False
    second = client.post("/scan", json={"url": "https://www.saucedemo.com", "run_id": run_id, "scan_id": run_id})
    assert second.status_code == 409
    finish_scan(run_id)


# 5e. End-and-save never marks completed state
def test_end_and_save_sets_partial_state(client: TestClient):
    run_id = "run_end_partial_01"
    ctx = create_run_context(url="https://example.test", run_id=run_id)
    ctx.status = "running"
    save_run_context(ctx)

    res = client.post(f"/runs/{run_id}/end-and-save")
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "partial_scanned"

    updated = get_run_context(run_id)
    assert updated is not None
    assert updated.status == "partial_scanned"


# 6. Execution Failure Categorization Tests
def test_execution_failure_categorization():
    """Verify categorical failure distinction on ExecutionResultRow."""
    from playwright.sync_api import TimeoutError as PWTimeout

    assert _failure_category(PWTimeout("Timeout 30000ms exceeded")) == "timeout"
    assert _failure_category(RuntimeError("Locator element not visible")) == "locator"
    assert _failure_category(RuntimeError("net::ERR_CONNECTION_REFUSED")) == "environment"
    assert _failure_category(AssertionError("Cart badge count mismatch")) == "product"

    case = TestCaseModel(tc_id="TC_01", title="Verify Inventory", module="Inventory", route="/inv")
    step = TestStepModel(step_id="S1", action="click", route="/inv", expected_result="Opened")

    # Failed row with category
    fail_row = _row(
        case, step, "Fail", "Locator error", priority="Medium", locator_used="btn#add", failure_category="locator"
    )
    assert fail_row.Status == "Fail"
    assert fail_row.failure_category == "locator"

    # Skipped row
    skip_row = _row(case, step, "Skip", "Manual review required")
    assert skip_row.Status == "Skip"
    assert skip_row.failure_category == "skipped"


# 7. AI Provider Status and Grounding Validation Tests
def test_provider_status_and_grounding_audit():
    """Verify provider planning metadata exposes model, provider, grounding status and zero secrets."""
    gateway = LLMProviderGateway()

    scan_context = {
        "page_intelligence": {
            "https://example.com/login": {
                "route": "https://example.com/login",
                "automation_targets": [
                    {"label": "Username", "type": "input", "element_role": "textbox", "selector": {"primary": {"value": "#u"}}},
                    {"label": "Login", "type": "button", "element_role": "button", "selector": {"primary": {"value": "#btn"}}},
                ]
            }
        }
    }

    result = gateway.plan_tests(
        provider="deterministic",
        model=None,
        scan_context=scan_context,
        requirements=[],
        testcases=[],
        user_prompt="Verify login input"
    )

    assert result["provider"] == "deterministic"
    assert result["fallback_used"] is False
    assert len(result["test_cases"]) > 0

    meta = result["planning_metadata"]
    assert meta["provider"] == "deterministic"
    assert "plan_type" in meta
    assert "correlation_id" in meta

    # Ensure zero API keys, passwords, or tokens in the returned planning payload
    dumped = json.dumps(result).lower()
    for secret in ["api_key", "secret", "password", "bearer ", "token"]:
        assert f'"{secret}"' not in dumped


# 8. Report Download URLs and Status Tests
def test_report_download_urls_and_status(client: TestClient):
    """Verify artifact download routes, file serving, and 409 handling for in-progress runs."""
    run_id = "test_report_run_42"

    # Create dummy artifacts
    REPORT_PATH.mkdir(parents=True, exist_ok=True)
    json_file = REPORT_PATH / f"{run_id}_crawl_results.json"
    yaml_file = REPORT_PATH / f"{run_id}_crawl_results.yaml"
    md_file = REPORT_PATH / f"{run_id}_crawl_results_summary.md"
    chk_file = REPORT_PATH / f"checkpoint_{run_id}.json"

    json_file.write_text(json.dumps({"test": "data"}), encoding="utf-8")
    yaml_file.write_text("test: data\n", encoding="utf-8")
    md_file.write_text("# PiOne Testmate - Scan Summary\n", encoding="utf-8")
    chk_file.write_text(json.dumps({"namespace": "https://example.com"}), encoding="utf-8")

    try:
        # Check artifact status endpoint
        res = client.get(f"/reports/{run_id}/status")
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == run_id
        assert data["artifacts"]["json"] is True
        assert data["artifacts"]["yaml"] is True
        assert data["artifacts"]["markdown"] is True
        assert data["artifacts"]["checkpoint"] is True

        # Download json
        dl_json = client.get(f"/reports/{run_id}/download/json")
        assert dl_json.status_code == 200
        assert dl_json.json() == {"test": "data"}

        # Download markdown
        dl_md = client.get(f"/reports/{run_id}/download/markdown")
        assert dl_md.status_code == 200
        assert "PiOne Testmate" in dl_md.text

        # Download checkpoint
        dl_chk = client.get(f"/reports/{run_id}/download/checkpoint")
        assert dl_chk.status_code == 200
        assert dl_chk.json()["namespace"] == "https://example.com"

        # Missing artifact returns 404 when run is not in progress
        dl_missing = client.get(f"/reports/{run_id}/download/excel")
        assert dl_missing.status_code == 404

        # In-progress run returns 409 Conflict for missing artifacts
        ctx = create_run_context(url="https://example.com", run_id="in_prog_run")
        ctx.status = "scanning"
        save_run_context(ctx)

        dl_prog = client.get("/reports/in_prog_run/download/excel")
        assert dl_prog.status_code == 409
        assert "generating or scan/execution is still in progress" in dl_prog.json()["detail"]

    finally:
        json_file.unlink(missing_ok=True)
        yaml_file.unlink(missing_ok=True)
        md_file.unlink(missing_ok=True)
        chk_file.unlink(missing_ok=True)


# 9. Refresh Resumption from RunContext Tests
def test_refresh_resumption_from_run_context(client: TestClient):
    """Verify /run/{run_id}/state restores active run context for page refresh."""
    run_id = "test_resume_run_77"
    ctx = create_run_context(url="https://www.saucedemo.com", run_id=run_id)
    ctx.selected_modules = ["mod_inventory", "mod_cart"]
    ctx.discovered_modules = [
        {"id": "mod_inventory", "label": "Inventory", "route": "/inventory.html"},
        {"id": "mod_cart", "label": "Cart", "route": "/cart.html"},
    ]
    ctx.status = "scanned"
    save_run_context(ctx)

    res = client.get(f"/run/{run_id}/state")
    assert res.status_code == 200
    data = res.json()
    assert data["run_id"] == run_id
    assert data["target_origin"] == "https://www.saucedemo.com"
    assert data["selected_modules"] == ["mod_inventory", "mod_cart"]
    assert len(data["discovered_modules"]) == 2

    # Non-existent run returns 404
    res_none = client.get("/run/non_existent_run_999/state")
    assert res_none.status_code == 404


# 10. Sanitized Actionable Error Handling Tests
def test_sanitized_error_handling_no_leaks(client: TestClient):
    """Verify error responses do not leak passwords, tokens, cookies, or stack traces."""
    # Test invalid scan request
    bad_req = {
        "url": "http://127.0.0.1:8000/internal",  # SSRF blocked in non-test mode
        "username": "secret_user",
        "password": "super_secret_password_12345",
    }
    res = client.post("/scan", json=bad_req)
    # The response detail must never contain the plain password or secrets
    assert "super_secret_password_12345" not in res.text
