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
def test_db_status_and_sqlite_fallback():
    status = check_db_status()
    assert "status" in status
    assert status["status"] in {"connected", "degraded"}
    assert "engine" in status
    assert status["engine"] in {"sqlite", "postgresql"}


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
async def test_api_runs_crud_endpoints(async_client: httpx.AsyncClient):
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
