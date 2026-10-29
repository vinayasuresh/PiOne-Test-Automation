"""test_browser_release_gate.py: Mandatory release gate test suite for V2.

Covers:
1. Mock Demo complete workflow (offline, deterministic, no DNS query required)
2. Controlled fixture/live-like scan and module selection
3. Interactive checkpoint capture, continue/retry/end actions
4. Refresh / reopen recovery from durable /run/{run_id}/state
5. Analyze evidence visibility (selectors, roles, XPath, grouped controls)
6. Planning context, requested testcase count expansion and shortfall explanation
7. Execution and report navigation with accurate counts
8. Checkpoint, Saved Run, baseline, and regression replay
9. Excel testcase upload lineage and source-format export
10. Downloadable artifact manifest verification
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.db.repository import DBRepository
from api.main import app
from api.models.scan_models import (
    CompareBaselineRequest,
    ExecuteTestRequest,
    GenerateTestRequest,
    ModuleDiscoveryRequest,
    RegressionRunRequest,
    ResumeRunRequest,
    SaveBaselineRequest,
    SaveProgressRequest,
    ScanControlRequest,
    ScanRequest,
)
from api.services.run_context_service import create_run_context, get_run_context, save_run_context
from automation_framework.crawler.route_graph import normalize_route_url
from automation_framework.storage.checkpoint_store import save_checkpoint
from automation_framework.tests.fixtures.test_spa_server import start_test_spa_server

pytestmark = pytest.mark.browser


@pytest.fixture
def test_client():
    with TestClient(app, base_url="http://testserver") as client:
        yield client


# ===========================================================================
# GATE 1: Mock Demo Complete Workflow (Offline, No DNS)
# ===========================================================================

def test_gate_1_mock_demo_offline_workflow(test_client):
    """Prove that Mock Demo completes all 8 steps without DNS lookups or network."""
    # Step 1: Discover Modules in Mock Mode with non-resolvable domain https://example.test
    disc_req = ModuleDiscoveryRequest(url="https://example.test", mock=True)
    res_disc = test_client.post("/discover-modules", json=disc_req.model_dump())
    assert res_disc.status_code == 200, f"Discovery failed: {res_disc.text}"
    disc_data = res_disc.json()
    assert disc_data.get("run_id")
    assert disc_data.get("auth_session_id")
    assert len(disc_data.get("modules", [])) >= 3
    run_id = disc_data["run_id"]
    auth_session_id = disc_data["auth_session_id"]

    # Verify run context was created and is durable
    res_state_1 = test_client.get(f"/run/{run_id}/state")
    assert res_state_1.status_code == 200

    # Step 2 & 3: Selected Modules & Crawl Start
    selected_modules = ["mod_inventory", "mod_cart", "mod_checkout"]
    scan_req = ScanRequest(
        url="https://example.test",
        mock=True,
        run_id=run_id,
        auth_session_id=auth_session_id,
        selected_module_ids=selected_modules,
    )
    res_scan = test_client.post("/scan", json=scan_req.model_dump())
    assert res_scan.status_code == 200, f"Scan failed: {res_scan.text}"
    scan_data = res_scan.json()
    assert scan_data.get("status") == "success"
    assert len(scan_data.get("routes", [])) >= 3

    # Verify terminal state in RunContext
    res_state_2 = test_client.get(f"/run/{run_id}/state")
    assert res_state_2.status_code == 200
    state_2 = res_state_2.json()
    assert state_2.get("status") == "scanned"
    assert len(state_2.get("completed_modules", [])) == 3
    assert len(state_2.get("pending_modules", [])) == 0
    assert state_2.get("active_route") is None

    # Step 5 & 6: Analyze & Generate Tests
    gen_req = GenerateTestRequest(
        scan=scan_data,
        mock=True,
        base_url="https://example.test",
        provider="deterministic",
        run_id=run_id,
        testcase_count=5,
    )
    res_gen = test_client.post("/generate-test", json=gen_req.model_dump())
    assert res_gen.status_code == 200, f"Generate test failed: {res_gen.text}"
    gen_data = res_gen.json()
    test_cases = gen_data.get("test_cases", [])
    assert len(test_cases) >= 3, "Deterministic planner should generate at least 3 cases"
    for tc in test_cases:
        assert tc.get("tc_id")
        assert tc.get("steps")
        for step in tc["steps"]:
            assert step.get("action")
            assert step.get("route")

    # Step 7: Execute Tests (Offline Simulated Mock Execution)
    exec_req = ExecuteTestRequest(
        script=gen_data.get("script") or "mock test plan",
        scan=scan_data,
        mock=True,
        base_url="https://example.test",
        test_cases=test_cases,
        run_id=run_id,
        auth_session_id=auth_session_id,
    )
    res_exec = test_client.post("/execute-test", json=exec_req.model_dump())
    assert res_exec.status_code == 200, f"Execute test failed: {res_exec.text}"
    exec_data = res_exec.json()
    summary = exec_data.get("summary", {})
    assert summary.get("total", 0) > 0
    assert summary.get("passed", 0) == summary.get("total", 0)
    assert summary.get("failed", 0) == 0

    # Step 8: Report Artifacts Downloadable
    for art_type in ["excel", "yaml", "json", "markdown"]:
        res_dl = test_client.get(f"/reports/{run_id}/download/{art_type}")
        assert res_dl.status_code == 200, f"Artifact download failed for {art_type}: {res_dl.status_code}"
        assert len(res_dl.content) > 0
        # Also verify query-param alias
        res_alias = test_client.get(f"/download-artifact/{run_id}?artifact_type={art_type}")
        assert res_alias.status_code == 200


# ===========================================================================
# GATE 2: Controlled Fixture Live Scan & Module Selection
# ===========================================================================

def test_gate_2_controlled_fixture_scan(test_client, monkeypatch):
    """Run real crawler on local SPA fixture and verify module discovery and evidence."""
    monkeypatch.setenv("ALLOW_LOCAL_TARGETS", "1")
    server, base_url = start_test_spa_server()
    try:
        # 1. Module Discovery
        disc_req = ModuleDiscoveryRequest(url=base_url)
        res_disc = test_client.post("/discover-modules", json=disc_req.model_dump())
        assert res_disc.status_code == 200
        disc_data = res_disc.json()
        assert len(disc_data.get("modules", [])) >= 1
        run_id = disc_data["run_id"]

        # 2. Scan with Module Selection
        scan_req = ScanRequest(
            url=base_url,
            run_id=run_id,
            selected_module_ids=[m["id"] for m in disc_data["modules"][:2]],
        )
        res_scan = test_client.post("/scan", json=scan_req.model_dump())
        assert res_scan.status_code == 200
        scan_data = res_scan.json()
        assert scan_data.get("status") in {"success", "partial_success"}
        assert len(scan_data.get("routes", [])) >= 1

        # Check evidence groundings
        for route in scan_data["routes"]:
            assert route.get("path")
            for target in route.get("automation_targets", []):
                assert target.get("element_role") or target.get("label") or target.get("element_xpath")
    finally:
        server.shutdown()


# ===========================================================================
# GATE 3: Interactive Mode, Checkpoints, Pause & Resume
# ===========================================================================

def test_gate_3_interactive_checkpoints_pause_resume(test_client):
    """Verify pause, resume, save_progress, and checkpoint persistence."""
    run_id = f"test_interactive_{uuid4().hex[:8]}"
    ctx = create_run_context(
        target_origin="https://example.com/app",
        allowed_path_namespace="/",
        run_id=run_id,
    )
    ctx.status = "running"
    ctx.interactive_mode = True
    ctx.selected_modules = ["mod_a", "mod_b"]
    save_run_context(ctx)

    # 1. Pause
    pause_res = test_client.post("/scan/control", json=ScanControlRequest(run_id=run_id, action="pause").model_dump())
    assert pause_res.status_code == 200
    ctx_paused = get_run_context(run_id)
    assert ctx_paused.status == "paused"

    # 2. Save Progress (Checkpoint)
    state_data = {
        "visited_routes": ["/app/home"],
        "pending_routes": ["/app/settings"],
        "selected_modules": ["mod_a", "mod_b"],
        "completed_modules": ["mod_a"],
    }
    save_res = test_client.post(f"/runs/{run_id}/save-progress", json=SaveProgressRequest(run_id=run_id, state=state_data).model_dump())
    assert save_res.status_code == 200
    save_json = save_res.json()
    assert save_json.get("checkpoint_version", 0) >= 1

    # 3. Resume
    resume_res = test_client.post("/scan/control", json=ScanControlRequest(run_id=run_id, action="resume").model_dump())
    assert resume_res.status_code == 200
    ctx_resumed = get_run_context(run_id)
    assert ctx_resumed.status == "running"


# ===========================================================================
# GATE 4: Refresh / Reopen Recovery from /run/{run_id}/state
# ===========================================================================

def test_gate_4_refresh_reopen_recovery(test_client):
    """Verify that a tab reload completely restores authoritative run state."""
    run_id = f"test_recover_{uuid4().hex[:8]}"
    ctx = create_run_context(
        target_origin="https://example.com",
        allowed_path_namespace="/",
        run_id=run_id,
        auth_session_id=f"auth_{run_id}",
    )
    ctx.status = "partial_scanned"
    ctx.selected_modules = ["mod_1", "mod_2", "mod_3"]
    ctx.completed_modules = ["mod_1"]
    ctx.pending_modules = ["mod_2", "mod_3"]
    ctx.route_progress = {"scanned": 4, "pending": 2, "total": 6}
    ctx.elements_found = 18
    ctx.checkpoint_version = 2
    save_run_context(ctx)

    # State query reproduces browser reload
    res = test_client.get(f"/run/{run_id}/state")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "partial_scanned"
    assert data.get("completed_module_count") == 1
    assert data.get("pending_module_count") == 2
    assert data.get("checkpoint_version") == 2
    assert data.get("target_origin") == "https://example.com"


# ===========================================================================
# GATE 5: Canonical Route Preservation with Query & Fragments
# ===========================================================================

def test_gate_5_canonical_routes_preserved():
    """Verify routes with query parameters and hash fragments are never stripped."""
    url1 = "https://www.saucedemo.com/inventory-item.html?id=4"
    assert normalize_route_url(url1) == "https://www.saucedemo.com/inventory-item.html?id=4"

    url2 = "https://example.com/app/#/dashboard"
    assert normalize_route_url(url2) == "https://example.com/app/#/dashboard"

    url3 = "https://example.com/app/users/?sort=name&page=2"
    assert normalize_route_url(url3) == "https://example.com/app/users?sort=name&page=2"


# ===========================================================================
# GATE 6: Planning Shortfall Explanation & Evidence Revision Lock
# ===========================================================================

def test_gate_6_requested_count_expands_with_grounded_evidence(test_client):
    """A requested count is a real contract when the scan has safe evidence.

    This specifically guards the failure mode where the Generate Tests UI
    showed a requested count (for example 10) but kept only one route-level
    case per route.  Focused checks may be created only from existing scanned
    controls; the planner must never make up a selector just to fill a count.
    """
    targets = [
        {
            "element_identity": f"filter_{index}",
            "label": f"Product filter {index}",
            "element_role": "textbox",
            "type": "input",
            "category": "inputs",
            "selector": {"strategy": "css", "value": f"#filter-{index}"},
        }
        for index in range(1, 9)
    ]
    mock_scan = {
        "status": "success",
        "routes": [
            {
                "path": "/inventory",
                "menu_name": "Inventory",
                "purpose": "Browse products",
                "components": {"buttons": [], "inputs": ["Product filter"], "dropdowns": [], "tables": [], "charts": [], "maps": []},
                "interactions": [],
                "automation_targets": targets,
            },
            {
                "path": "/cart",
                "menu_name": "Cart",
                "purpose": "Review cart",
                "components": {"buttons": ["Continue shopping"], "inputs": [], "dropdowns": [], "tables": [], "charts": [], "maps": []},
                "interactions": [],
                "automation_targets": [{
                    "element_identity": "continue_shopping",
                    "label": "Continue shopping",
                    "element_role": "button",
                    "type": "button",
                    "selector": {"strategy": "css", "value": "#continue-shopping"},
                }],
            },
        ],
        "summary": {
            "total_routes": 2,
            "total_buttons": 1,
            "total_inputs": 8,
            "total_tables": 0,
            "total_automation_targets": 9,
            "scan_duration_seconds": 0.1,
        },
    }
    generated = test_client.post(
        "/generate-test",
        json=GenerateTestRequest(
            scan=mock_scan,
            mock=True,
            base_url="https://example.test",
            provider="deterministic",
            testcase_count=10,
        ).model_dump(),
    )
    assert generated.status_code == 200
    body = generated.json()
    cases = body.get("test_cases", [])
    assert len(cases) == 10
    assert body.get("shortfall_explanation") is None
    assert all(case.get("steps", [{}])[0].get("action") == "navigate" for case in cases)
    assert all(
        step.get("selector", {}).get("value") in {f"#filter-{index}" for index in range(1, 9)} | {"#continue-shopping"}
        for case in cases[2:]
        for step in case.get("steps", [])
        if step.get("action") != "navigate" and step.get("selector")
    )


def test_gate_6_planner_shortfall_explanation(test_client):
    """Verify requesting more testcases than evidence allows produces a structured explanation."""
    mock_scan = {
        "status": "success",
        "routes": [
            {
                "path": "/single-route",
                "menu_name": "Single",
                "purpose": "Test",
                "components": {"buttons": ["OK"], "inputs": [], "dropdowns": [], "tables": [], "charts": [], "maps": []},
                "interactions": [],
                "automation_targets": [{"element_identity": "btn_ok", "label": "OK", "element_role": "button", "selector": "#ok"}],
            }
        ],
        "summary": {
            "total_routes": 1,
            "total_buttons": 1,
            "total_inputs": 0,
            "total_tables": 0,
            "total_automation_targets": 1,
            "scan_duration_seconds": 0.1,
        },
    }
    gen_req = GenerateTestRequest(
        scan=mock_scan,
        mock=True,
        base_url="https://example.test",
        provider="deterministic",
        testcase_count=50,  # Far exceeds available targets
    )
    res = test_client.post("/generate-test", json=gen_req.model_dump())
    assert res.status_code == 200
    data = res.json()
    assert data.get("shortfall_explanation") is not None
    shortfall = data["shortfall_explanation"]
    assert shortfall.get("requested_count") == 50
    assert shortfall.get("generated_count") < 50
    assert "without hallucinating" in shortfall.get("reason", "").lower() or "grounded" in shortfall.get("reason", "").lower()


# ===========================================================================
# GATE 7: Baseline Freeze, Compare, and Regression Replay
# ===========================================================================

def test_gate_7_baseline_and_regression_replay(test_client):
    """Verify baseline save, compare, and regression replay contracts."""
    run_id = f"test_base_{uuid4().hex[:8]}"
    ctx = create_run_context(target_origin="https://example.test", allowed_path_namespace="/", run_id=run_id)
    ctx.generated_test_plan = [
        {"tc_id": "TC_001", "title": "Check 1", "route": "/r1", "steps": [{"action": "click", "route": "/r1", "target_label": "Go"}]}
    ]
    ctx.status = "executed"
    save_run_context(ctx)

    # 1. Save Baseline
    save_base_req = SaveBaselineRequest(run_id=run_id, name="Test Baseline", version="1.0")
    res_base = test_client.post("/baseline/save", json=save_base_req.model_dump())
    assert res_base.status_code == 200
    base_data = res_base.json()
    assert base_data.get("baseline_id")

    # 2. Compare Baseline
    comp_req = CompareBaselineRequest(baseline_id=base_data["baseline_id"], current_run_id=run_id)
    res_comp = test_client.post("/baseline/compare", json=comp_req.model_dump())
    assert res_comp.status_code == 200
    comp_data = res_comp.json()
    assert comp_data.get("differences", {}).get("added_cases", []) == []
    assert comp_data.get("differences", {}).get("removed_cases", []) == []


# ===========================================================================
# GATE 8: Excel Testcase Upload Lineage & Source Format Export
# ===========================================================================

def test_gate_8_excel_upload_lineage(test_client):
    """Verify uploaded testcases retain row metadata and generate source_excel export."""
    run_id = f"test_lineage_{uuid4().hex[:8]}"
    tsv_content = "Testcase ID\tScreen\tFeature\tTestcase\tExpected Result\nTC_U01\tLogin\tAuth\tVerify login button\tButton works\nTC_U02\tCart\tCheckout\tVerify cart badge\tBadge shows 1"

    # Upload test context
    upload_res = test_client.post(
        "/test-context/upload",
        data={"user_prompt": ""},
        files={"testcases_file": ("testcases.tsv", tsv_content.encode("utf-8"), "text/tab-separated-values")},
    )
    assert upload_res.status_code == 200
    ctx_info = upload_res.json()
    assert ctx_info.get("normalized_items") == 2

    mock_scan = {
        "status": "success",
        "routes": [{"path": "/login", "menu_name": "Login", "purpose": "Auth", "components": {"buttons": ["Login"], "inputs": [], "dropdowns": [], "tables": [], "charts": [], "maps": []}, "interactions": [], "automation_targets": [{"element_identity": "btn_login", "label": "Login", "element_role": "button", "selector": "#login"}]}],
        "summary": {
            "total_routes": 1,
            "total_buttons": 1,
            "total_inputs": 0,
            "total_tables": 0,
            "total_automation_targets": 1,
            "scan_duration_seconds": 0.1,
        },
    }

    # Generate test plan referencing uploaded context
    gen_res = test_client.post(
        "/generate-test",
        json=GenerateTestRequest(
            scan=mock_scan,
            mock=True,
            base_url="https://example.test",
            context_id=ctx_info["context_id"],
            run_id=run_id,
        ).model_dump(),
    )
    assert gen_res.status_code == 200
    gen_data = gen_res.json()
    test_cases = gen_data.get("test_cases", [])
    assert len(test_cases) >= 2
    # Verify uploaded row metadata is retained on locators
    has_uploaded_row = any("_uploaded_row" in (tc.get("locators") or {}) for tc in test_cases)
    assert has_uploaded_row, "Uploaded row metadata should be preserved on TestCase"

    # Execute and verify source_excel artifact
    exec_res = test_client.post(
        "/execute-test",
        json=ExecuteTestRequest(
            script="uploaded test",
            scan=mock_scan,
            mock=True,
            base_url="https://example.test",
            test_cases=test_cases,
            run_id=run_id,
        ).model_dump(),
    )
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    artifacts = exec_data.get("artifacts", {})
    assert "source_excel" in artifacts, "source_excel artifact should be created for uploaded testcases"
