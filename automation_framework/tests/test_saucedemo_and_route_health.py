"""test_saucedemo_and_route_health.py: Comprehensive test suite for SauceDemo scenario and route health.

Covers:
1. Route-health aggregation across Scorer, Crawler results, ScanResponse, Excel, and Markdown.
2. Grounded safe-action planning for add-to-cart, details, continue-shopping, sorting, assert_cart_count.
3. Preservation of destructive safeguards (logout, reset, checkout, submit parked/skipped).
4. Controlled browser execution on the deterministic SauceDemo mock server.
5. Report and artifact generation parity.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import sync_playwright

from api.models.scan_models import (
    ComponentGroups,
    ExecutionResultRow,
    ExecutionSummary,
    RouteResult,
    ScanResponse,
    ScanSummary,
    TestCase,
    TestStep,
)
from api.services.llm_provider_gateway import (
    DeterministicAdapter,
    LLMProviderGateway,
    validate_plan_grounding,
)
from api.services.test_execution_service import execute_test_cases
from api.services.test_planning_service import build_test_cases
from api.utils.response_builder import build_scan_response
from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_parking_lot_action,
    is_safe_action,
)

pytestmark = pytest.mark.browser
from automation_framework.crawler.crawl_exporter import export_crawl_results
from automation_framework.crawler.route_health_scorer import (
    RouteStatus,
    is_non_working_route,
    is_partial_route,
    is_working_route,
    normalize_route_status,
    score_route,
)
from automation_framework.tests.fixtures.test_saucedemo_server import start_test_saucedemo_server


@pytest.fixture(scope="module")
def saucedemo_server():
    server, base_url = start_test_saucedemo_server()
    yield server, base_url
    server.shutdown()


# ---------------------------------------------------------------------------
# 1. Route Health Aggregation & Classification Tests
# ---------------------------------------------------------------------------

def test_route_health_classification_and_aggregation():
    """Verify route health scoring classifications and summary count parity."""
    # Route 1: Working route (high score, DOM stable, load success)
    res_working = score_route(
        expected_url="/inventory.html",
        actual_url="/inventory.html",
        stable=True,
        target_count=10,
        api_responses=[{"status": 200}],
        interaction_stats={"attempted": 2, "succeeded": 2},
        page_load_success=True,
    )
    assert is_working_route(res_working["status"])
    assert not is_partial_route(res_working["status"])
    assert not is_non_working_route(res_working["status"])
    assert res_working["score"] >= 70

    # Route 2: Partial route (load success, but some failed APIs / lower interactions)
    res_partial = score_route(
        expected_url="/cart.html",
        actual_url="/cart.html",
        stable=True,
        target_count=5,
        api_responses=[{"status": 500}],
        interaction_stats={"attempted": 2, "succeeded": 1},
        page_load_success=True,
    )
    assert is_partial_route(res_partial["status"])
    assert not is_working_route(res_partial["status"])
    assert 40 <= res_partial["score"] <= 90

    # Route 3: Non-working route (load failure or severe issues)
    res_non_working = score_route(
        expected_url="/error.html",
        actual_url="/error.html",
        stable=False,
        target_count=0,
        api_responses=[{"status": 500}],
        interaction_stats={"attempted": 0, "succeeded": 0},
        page_load_success=False,
    )
    assert is_non_working_route(res_non_working["status"])
    assert res_non_working["score"] < 40

    # Crawl result aggregation into response builder
    crawl_results = {
        "page_intelligence": {
            "/inventory.html": {
                "route": "/inventory.html",
                "menu_name": "Inventory",
                "purpose": "Product catalog",
                "automation_targets": [{"label": "Backpack", "type": "button", "category": "buttons"}],
                "route_validation": {
                    "status": res_working["status"],
                    "score": res_working["score"],
                    "dom_stable": True,
                    "page_load_success": True,
                    "redirected": False,
                    "api_response_present": True,
                    "failed_api_responses": 0,
                    "interaction_success_rate": 1.0,
                },
            },
            "/cart.html": {
                "route": "/cart.html",
                "menu_name": "Cart",
                "purpose": "Shopping cart",
                "automation_targets": [{"label": "Checkout", "type": "button", "category": "buttons"}],
                "route_validation": {
                    "status": res_partial["status"],
                    "score": res_partial["score"],
                    "dom_stable": True,
                    "page_load_success": True,
                    "redirected": False,
                    "api_response_present": True,
                    "failed_api_responses": 3,
                    "interaction_success_rate": 0.4,
                },
            },
            "/error.html": {
                "route": "/error.html",
                "menu_name": "Error",
                "purpose": "Error page",
                "automation_targets": [],
                "route_validation": {
                    "status": res_non_working["status"],
                    "score": res_non_working["score"],
                    "dom_stable": False,
                    "page_load_success": False,
                    "redirected": False,
                    "api_response_present": False,
                    "failed_api_responses": 5,
                    "interaction_success_rate": 0.0,
                },
            },
        },
        "feature_routes": {},
        "component_registry": {},
    }

    resp = build_scan_response(crawl_results, duration=2.5)

    assert resp.summary.total_routes == 3
    assert resp.summary.working_routes == 1
    assert resp.summary.partial_routes == 1
    assert resp.summary.non_working_routes == 1

    routes_by_path = {r.path: r for r in resp.routes}
    assert routes_by_path["/inventory.html"].route_status == "WORKING"
    assert routes_by_path["/inventory.html"].dom_stable is True
    assert routes_by_path["/inventory.html"].page_load_success is True

    assert routes_by_path["/cart.html"].route_status == "PARTIAL"
    assert routes_by_path["/error.html"].route_status in {"NON-WORKING", "NON_WORKING"}


# ---------------------------------------------------------------------------
# 2. Action Classifier & Grounded Safe Business Action Planning
# ---------------------------------------------------------------------------

def test_action_classifier_saucedemo_actions():
    """Verify safe vs destructive classification for SauceDemo buttons and inputs."""
    # Add to cart must be classified as safe
    res_cart = classify_action({
        "tag": "button",
        "text": "Add to cart",
        "label": "Add to cart",
        "id": "add-to-cart-sauce-labs-backpack",
        "classes": "btn_primary",
    })
    assert res_cart == ActionType.SAFE_ACTION
    assert is_safe_action(res_cart)
    assert not is_parking_lot_action(res_cart)

    # Continue shopping must be safe navigation
    res_continue = classify_action({
        "tag": "button",
        "text": "Continue Shopping",
        "label": "Continue Shopping",
        "id": "continue-shopping",
    })
    assert res_continue == ActionType.SAFE_NAVIGATION
    assert is_safe_action(res_continue)
    assert not is_parking_lot_action(res_continue)

    # Logout must be classified as destructive
    res_logout = classify_action({
        "tag": "button",
        "text": "Logout",
        "label": "Logout",
        "id": "logout_sidebar_link",
    })
    assert res_logout == ActionType.BLOCKED_DESTRUCTIVE
    assert not is_safe_action(res_logout)
    assert is_parking_lot_action(res_logout)

    # Reset App State must be classified as destructive
    res_reset = classify_action({
        "tag": "button",
        "text": "Reset App State",
        "label": "Reset App State",
        "id": "reset_sidebar_link",
    })
    assert res_reset in {ActionType.BLOCKED_DESTRUCTIVE, ActionType.REQUIRES_MANUAL_CONFIRMATION}
    assert is_parking_lot_action(res_reset)


def test_deterministic_planner_generates_safe_saucedemo_actions():
    """Verify deterministic planner generates add-to-cart, cart count assertion, sorting, and continue-shopping."""
    scan_resp = ScanResponse(
        status="success",
        base_url="https://www.saucedemo.com",
        routes=[
            RouteResult(
                path="/inventory.html",
                menu_name="Inventory",
                purpose="Product Catalog",
                components=ComponentGroups(
                    buttons=["Add to cart", "Add to basket"],
                    dropdowns=["Product Sort"],
                ),
                automation_targets=[
                    {
                        "element_identity": "/inventory.html|add-to-cart-backpack|button",
                        "label": "Add to cart",
                        "type": "button",
                        "category": "buttons",
                        "selector": {"primary": {"strategy": "css", "value": "#add-to-cart-backpack"}},
                        "safety_classification": "safe_action",
                    },
                    {
                        "element_identity": "/inventory.html|product_sort|select",
                        "label": "Product Sort",
                        "type": "select",
                        "category": "dropdowns",
                        "component_tag": "select",
                        "selector": {"primary": {"strategy": "css", "value": "#product_sort"}},
                        "safety_classification": "safe_sort",
                    },
                    {
                        "element_identity": "/inventory.html|backpack_details|link",
                        "label": "Sauce Labs Backpack",
                        "type": "link",
                        "category": "links",
                        "selector": {"primary": {"strategy": "css", "value": "#item_4_title_link"}},
                        "safety_classification": "safe_navigation",
                    },
                ],
                route_status="WORKING",
                route_score=95,
            ),
            RouteResult(
                path="/cart.html",
                menu_name="Cart",
                purpose="Cart View",
                components=ComponentGroups(buttons=["Continue Shopping", "Checkout"]),
                automation_targets=[
                    {
                        "element_identity": "/cart.html|continue-shopping|button",
                        "label": "Continue Shopping",
                        "type": "button",
                        "category": "buttons",
                        "selector": {"primary": {"strategy": "css", "value": "#continue-shopping"}},
                        "safety_classification": "safe_navigation",
                    },
                    {
                        "element_identity": "/cart.html|checkout|button",
                        "label": "Checkout",
                        "type": "button",
                        "category": "buttons",
                        "selector": {"primary": {"strategy": "css", "value": "#checkout"}},
                        "safety_classification": "destructive",
                    },
                ],
                route_status="WORKING",
                route_score=90,
            ),
        ],
        summary=ScanSummary(
            total_routes=2,
            working_routes=2,
            total_buttons=4,
            total_inputs=0,
            total_tables=0,
            total_dropdowns=1,
            scan_duration_seconds=1.0,
        ),
        feature_routes={},
        component_registry={},
    )

    cases = build_test_cases(scan_resp, {})
    assert len(cases) == 2

    # Inventory case
    inv_case = next(c for c in cases if c.route == "/inventory.html")
    inv_actions = [s.action for s in inv_case.steps]
    inv_labels = [s.target_label for s in inv_case.steps]

    assert "navigate" in inv_actions
    assert "click" in inv_actions  # Add to cart or details
    assert "assert_cart_count" in inv_actions  # Safe assertion for cart update
    assert "select" in inv_actions  # Sorting dropdown

    # Cart case
    cart_case = next(c for c in cases if c.route == "/cart.html")
    cart_labels = [s.target_label for s in cart_case.steps]
    # Checkout is destructive and should NOT be in the test steps
    assert "Checkout" not in cart_labels
    assert "Continue Shopping" in cart_labels


# ---------------------------------------------------------------------------
# 3. Strict Grounding & Destructive Action Safeguard
# ---------------------------------------------------------------------------

def test_grounding_rejects_hallucinations_and_mutations():
    """Verify validate_plan_grounding rejects hallucinated selectors and destructive actions."""
    scan_context = {
        "routes": [
            {
                "path": "/inventory.html",
                "route": "/inventory.html",
                "automation_targets": [
                    {
                        "label": "Add to cart",
                        "selector": {"primary": {"strategy": "css", "value": "#add-to-cart"}},
                        "safety_classification": "safe_action",
                    }
                ],
            }
        ],
        "parking_lot_actions": [
            {
                "route": "/inventory.html",
                "label": "Logout",
                "selector": {"primary": {"strategy": "css", "value": "#logout"}},
                "action_type": "destructive",
            }
        ],
    }

    # 1. Valid safe plan passes
    valid_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Inventory Safe Actions",
                "module": "Inventory",
                "route": "/inventory.html",
                "steps": [
                    {"step_id": "S1", "action": "navigate", "route": "/inventory.html"},
                    {"step_id": "S2", "action": "click", "route": "/inventory.html", "target_label": "Add to cart", "selector": "#add-to-cart"},
                    {"step_id": "S3", "action": "assert_cart_count", "route": "/inventory.html", "target_label": "Cart Badge"},
                ],
            }
        ]
    }
    is_valid, err_cat, reason = validate_plan_grounding(valid_plan, scan_context)
    assert is_valid is True

    # 2. Destructive keyword "Logout" is rejected
    logout_plan = {
        "test_cases": [
            {
                "tc_id": "TC_002",
                "title": "Logout Action",
                "module": "Inventory",
                "route": "/inventory.html",
                "steps": [
                    {"step_id": "S1", "action": "navigate", "route": "/inventory.html"},
                    {"step_id": "S2", "action": "click", "route": "/inventory.html", "target_label": "Logout", "selector": "#logout"},
                ],
            }
        ]
    }
    is_valid, err_cat, reason = validate_plan_grounding(logout_plan, scan_context)
    assert is_valid is False
    assert err_cat in {"MUTATING_ACTION_REJECTED", "UNSUPPORTED_ACTION"}

    # 3. Hallucinated selector is rejected
    hallucinated_plan = {
        "test_cases": [
            {
                "tc_id": "TC_003",
                "title": "Hallucinated Button",
                "module": "Inventory",
                "route": "/inventory.html",
                "steps": [
                    {"step_id": "S1", "action": "navigate", "route": "/inventory.html"},
                    {"step_id": "S2", "action": "click", "route": "/inventory.html", "target_label": "Fake Buy Now", "selector": "#fake-button"},
                ],
            }
        ]
    }
    is_valid, err_cat, reason = validate_plan_grounding(hallucinated_plan, scan_context)
    assert is_valid is False
    assert err_cat in {"HALLUCINATION", "UNSUPPORTED_ACTION"}


# ---------------------------------------------------------------------------
# 4. Live Browser Execution Against SauceDemo Mock Server
# ---------------------------------------------------------------------------

def test_live_saucedemo_mock_execution(saucedemo_server, tmp_path):
    """Run full live browser execution of safe business actions on the SauceDemo mock."""
    _, base_url = saucedemo_server

    test_cases = [
        TestCase(
            tc_id="TC_SAUCE_001",
            title="Inventory Safe Actions and Cart Update",
            module="Inventory",
            route="/inventory.html",
            purpose="Verify catalog, sorting, and add-to-cart",
            steps=[
                TestStep(
                    step_id="S01",
                    action="navigate",
                    route="/inventory.html",
                    expected_result="Inventory catalog loads.",
                ),
                TestStep(
                    step_id="S02",
                    action="select",
                    route="/inventory.html",
                    target_label="product_sort",
                    selector={"primary": {"strategy": "css", "value": "#product_sort"}},
                    value="lohi",
                    expected_result="Sort order changed.",
                ),
                TestStep(
                    step_id="S03",
                    action="click",
                    route="/inventory.html",
                    target_label="Add to cart",
                    selector={"primary": {"strategy": "css", "value": "#add-to-cart-sauce-labs-backpack"}},
                    expected_result="Item added to cart.",
                ),
                TestStep(
                    step_id="S04",
                    action="assert_cart_count",
                    route="/inventory.html",
                    target_label="Cart badge",
                    selector={"primary": {"strategy": "css", "value": "#cart-badge"}},
                    expected_result="Cart badge displays updated count.",
                ),
            ],
        ),
        TestCase(
            tc_id="TC_SAUCE_002",
            title="Cart View and Continue Shopping",
            module="Cart",
            route="/cart.html",
            purpose="Verify cart view and return to inventory",
            steps=[
                TestStep(
                    step_id="S01",
                    action="navigate",
                    route="/cart.html",
                    expected_result="Cart page loads.",
                ),
                TestStep(
                    step_id="S02",
                    action="click",
                    route="/cart.html",
                    target_label="Continue Shopping",
                    selector={"primary": {"strategy": "css", "value": "#continue-shopping"}},
                    expected_result="Return to inventory.",
                ),
                TestStep(
                    step_id="S03",
                    action="assert_url",
                    route="/inventory.html",
                    target_label="Inventory URL",
                    expected_result="Inventory URL active.",
                ),
                # Destructive checkout step must be skipped
                TestStep(
                    step_id="S04",
                    action="click",
                    route="/cart.html",
                    target_label="Checkout",
                    selector={"primary": {"strategy": "css", "value": "#checkout"}},
                    safety_classification="destructive",
                    expected_result="Checkout action parked.",
                ),
            ],
        ),
    ]

    rows, summary, artifacts = execute_test_cases(
        base_url=base_url,
        test_cases=test_cases,
        allow_mutating_actions=False,
    )

    assert summary.total == 8
    # 7 safe steps should Pass (navigate, select, click add-to-cart, assert_cart_count, navigate, click continue, assert_url)
    # 1 destructive step (Checkout) should be Skipped
    assert summary.passed == 7
    assert summary.skipped == 1
    assert summary.failed == 0

    checkout_row = next(r for r in rows if r.Step_ID == "S04" and r.TC_ID == "TC_SAUCE_002")
    assert checkout_row.Status == "Skipped"
    assert "restricted" in checkout_row.Actual_Result.lower() or "parked" in checkout_row.Actual_Result.lower()

    # Verify artifacts were generated
    assert Path(artifacts["excel"]).is_file()
    assert Path(artifacts["json"]).is_file()
    assert Path(artifacts["markdown"]).is_file()
    assert Path(artifacts["plan"]).is_file()


# ---------------------------------------------------------------------------
# 5. Artifact & Export Verification Tests
# ---------------------------------------------------------------------------

def test_export_artifacts_include_route_health_and_execution_sheets(tmp_path):
    """Verify Excel, JSON, and Markdown exports include complete Route Health and Test Execution details."""
    crawl_results = {
        "route_namespace": "https://www.saucedemo.com",
        "page_intelligence": {
            "/inventory.html": {
                "route": "/inventory.html",
                "menu_name": "Inventory",
                "purpose": "Product Catalog",
                "automation_targets": [
                    {
                        "element_identity": "/inventory.html|backpack|button",
                        "label": "Add to cart",
                        "type": "button",
                        "selector": {"strategy": "css", "value": "#add-to-cart"},
                        "safety_classification": "safe_action",
                    }
                ],
                "route_validation": {
                    "status": "WORKING",
                    "score": 100,
                    "expected_url": "/inventory.html",
                    "actual_url": "/inventory.html",
                    "dom_stable": True,
                    "page_load_success": True,
                    "redirected": False,
                    "api_response_present": True,
                    "failed_api_responses": 0,
                    "target_count": 5,
                    "interaction_success_rate": 1.0,
                },
            },
            "/cart.html": {
                "route": "/cart.html",
                "menu_name": "Cart",
                "purpose": "Cart",
                "automation_targets": [],
                "route_validation": {
                    "status": "PARTIAL",
                    "score": 75,
                    "expected_url": "/cart.html",
                    "actual_url": "/cart.html",
                    "dom_stable": True,
                    "page_load_success": True,
                    "redirected": False,
                    "api_response_present": True,
                    "failed_api_responses": 1,
                    "target_count": 2,
                    "interaction_success_rate": 0.8,
                },
            },
        },
        "execution_results": [
            {
                "TC_ID": "TC_001",
                "Step_ID": "S01",
                "Action": "click",
                "element_identity": "/inventory.html|backpack|button",
                "Status": "Pass",
                "locator_used": "css: #add-to-cart",
                "Actual_Result": "Item added to cart.",
            }
        ],
        "parking_lot_actions": [
            {
                "route": "/inventory.html",
                "label": "Logout",
                "classification": "destructive",
                "reason": "Restricted action keyword",
            }
        ],
    }

    paths = export_crawl_results(
        crawl_results=crawl_results,
        output_name="test_saucedemo_export",
        output_dir=tmp_path,
        run_id="run_123",
    )

    # 1. Excel inspection
    import pandas as pd
    excel_file = paths["excel"]
    xls = pd.ExcelFile(excel_file)
    sheet_names = xls.sheet_names

    assert "Route Health" in sheet_names or "route_health" in sheet_names
    assert "Test Execution" in sheet_names or "execution_results" in sheet_names

    rh_df = pd.read_excel(excel_file, sheet_name="Route Health")
    assert len(rh_df) == 2
    assert "status" in rh_df.columns
    assert "score" in rh_df.columns
    assert "dom_stable" in rh_df.columns

    exec_df = pd.read_excel(excel_file, sheet_name="Test Execution")
    assert len(exec_df) == 1
    assert exec_df.iloc[0]["element_identity"] == "/inventory.html|backpack|button"
    assert exec_df.iloc[0]["Action"] == "click"

    # 2. Markdown inspection
    md_content = paths["markdown"].read_text(encoding="utf-8")
    assert "**Working Routes**: 1" in md_content
    assert "**Partial Routes**: 1" in md_content
    assert "**Non-Working Routes**: 0" in md_content
    assert "| `/inventory.html` | **WORKING** | 100 |" in md_content
    assert "## Executed Test Actions" in md_content
    assert "Item added to cart." in md_content

    # 3. JSON inspection
    json_data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(json_data["routes"]) == 2
    assert json_data["routes"][0]["route_validation"]["status"] == "WORKING"


# ---------------------------------------------------------------------------
# 6. Regression Replay Diff Test
# ---------------------------------------------------------------------------

def test_saucedemo_regression_replay_diff(saucedemo_server):
    """Verify regression replay calculates accurate status changes and pass/fail diffs."""
    from api.services.run_context_service import create_run_context, get_run_context, save_run_context

    _, base_url = saucedemo_server

    ctx = create_run_context(url=base_url)
    run_id = ctx.run_id

    test_cases = [
        TestCase(
            tc_id="TC_REG_001",
            title="Catalog Navigation",
            module="Inventory",
            route="/inventory.html",
            steps=[
                TestStep(
                    step_id="S01",
                    action="navigate",
                    route="/inventory.html",
                    expected_result="Inventory loaded.",
                ),
                TestStep(
                    step_id="S02",
                    action="click",
                    route="/inventory.html",
                    target_label="Add to cart",
                    selector={"primary": {"strategy": "css", "value": "#add-to-cart-sauce-labs-backpack"}},
                    expected_result="Added to cart.",
                ),
            ],
        )
    ]

    # Baseline run
    base_rows, base_summary, base_artifacts = execute_test_cases(
        base_url=base_url,
        test_cases=test_cases,
        allow_mutating_actions=False,
        run_id=run_id,
    )

    ctx = get_run_context(run_id)
    ctx.execution_results = [r.model_dump() for r in base_rows]
    save_run_context(ctx)

    # Replay run
    replay_rows, replay_summary, replay_artifacts = execute_test_cases(
        base_url=base_url,
        test_cases=test_cases,
        allow_mutating_actions=False,
    )

    previous_results = {f"{r['TC_ID']}_{r['Step_ID']}": r["Status"] for r in ctx.execution_results}
    current_results = {f"{r.TC_ID}_{r.Step_ID}": r.Status for r in replay_rows}

    status_changes = []
    for key, curr_status in current_results.items():
        prev_status = previous_results.get(key)
        if prev_status and prev_status != curr_status:
            status_changes.append({"step": key, "from": prev_status, "to": curr_status})

    diff = {
        "previous_total": len(previous_results),
        "current_total": len(current_results),
        "status_changes": status_changes,
        "newly_passing": sum(1 for sc in status_changes if sc["to"] == "Pass"),
        "newly_failing": sum(1 for sc in status_changes if sc["to"] == "Fail"),
    }

    assert diff["previous_total"] == 2
    assert diff["current_total"] == 2
    assert diff["newly_failing"] == 0
    assert len(diff["status_changes"]) == 0
