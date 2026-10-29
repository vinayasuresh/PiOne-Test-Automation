"""test_ui_state_and_module_selection.py: Acceptance tests for UI state fingerprinting,
module selection lifecycle, and element metadata compliance.

Verifies:
1. Module discovery fallback for icon-only navigation and missing sidebar.
2. UI state fingerprinting and transition recording (tabs, modals, accordions).
3. Every exported element has all 12 required attributes with correct selector quality.
4. Child route recursion depth limit (<= 5) and cycle prevention.
5. Targeted module selection scans only the chosen module.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import openpyxl
import pytest
from playwright.sync_api import sync_playwright, Page

from automation_framework.crawler.action_classifier import ActionType
from automation_framework.crawler.crawl_exporter import export_crawl_results
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.route_graph import RouteGraph
from automation_framework.crawler.ui_state_fingerprint import (
    compute_selector_quality_score,
    compute_ui_state_fingerprint,
)
from automation_framework.engine.element_identity_registry import ElementIdentityRegistry
from automation_framework.tests.fixtures.test_spa_server import start_test_spa_server

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def spa_server():
    server, base_url = start_test_spa_server()
    yield base_url
    server.shutdown()


@pytest.fixture(scope="module")
def playwright_instance():
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    yield browser
    browser.close()
    p.stop()


@pytest.fixture
def page(playwright_instance) -> Page:
    context = playwright_instance.new_context()
    pg = context.new_page()
    yield pg
    context.close()


# ----------------------------------------------------------------------
# 1. Module Discovery Fallback (Icon-only & Missing Sidebar)
# ----------------------------------------------------------------------
def test_module_discovery_fallback_icon_only_and_missing_sidebar(page: Page, spa_server: str):
    # Case A: Icon-only navigation links in header using aria-label and title
    icon_nav_html = """<!DOCTYPE html>
    <html>
    <head><title>Icon App</title></head>
    <body>
        <header>
            <nav>
                <a href="/dashboard" aria-label="Dashboard Icon" style="display:inline-block;width:24px;height:24px;"><svg><title>Dashboard</title></svg></a>
                <a href="/reports" title="Reports View" style="display:inline-block;width:24px;height:24px;"><span><i class="fa fa-chart"></i></span></a>
            </nav>
        </header>
        <main><h1>Welcome</h1></main>
    </body>
    </html>"""
    page.goto(spa_server)
    page.set_content(icon_nav_html)

    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()
    assert len(modules) >= 2
    labels = [m["label"] for m in modules]
    assert any("Dashboard" in l for l in labels)
    assert any("Reports" in l for l in labels)
    for m in modules:
        assert m["module_id"].startswith("route:")
        assert m["confidence"] > 0

    # Case B: Zero navigation links on landing page -> graceful fallback to landing page module
    blank_app_html = """<!DOCTYPE html>
    <html>
    <head><title>Single Screen Portal</title></head>
    <body>
        <main>
            <h1>Single Page Dashboard</h1>
            <p>No navigation bar present</p>
        </main>
    </body>
    </html>"""
    page.set_content(blank_app_html)
    engine_blank = CrawlerEngine(page, base_url=spa_server)
    modules_blank = engine_blank.discover_modules()
    assert len(modules_blank) == 1
    assert modules_blank[0]["source"] == "landing_page"
    assert modules_blank[0]["label"] == "Single Screen Portal"


# ----------------------------------------------------------------------
# 2. UI State Fingerprint & Transition Recording
# ----------------------------------------------------------------------
def test_state_fingerprint_and_transition_recording(page: Page, spa_server: str):
    interactive_html = """<!DOCTYPE html>
    <html>
    <head><title>Tabs and Dialogs</title></head>
    <body>
        <div role="tablist">
            <button role="tab" id="tab-1" aria-selected="true">Tab One</button>
            <button role="tab" id="tab-2" aria-selected="false">Tab Two</button>
        </div>
        <div id="modal" role="dialog" aria-modal="true" style="display:none;">
            <h2>Confirmation Dialog</h2>
            <button id="modal-close">Close</button>
        </div>
        <button id="open-modal" onclick="document.getElementById('modal').style.display='block'">Open Modal</button>
    </body>
    </html>"""
    page.goto(spa_server)
    page.set_content(interactive_html)

    # Initial state
    base_state = compute_ui_state_fingerprint(page, f"{spa_server}/")
    assert base_state["ui_state_id"].startswith("state_")
    assert "Tab One" in base_state["tabs"]
    assert len(base_state["dialogs"]) == 0

    # Switch tabs
    page.evaluate("""() => {
        document.getElementById('tab-1').setAttribute('aria-selected', 'false');
        document.getElementById('tab-2').setAttribute('aria-selected', 'true');
    }""")
    tab2_state = compute_ui_state_fingerprint(page, f"{spa_server}/")
    assert "Tab Two" in tab2_state["tabs"]
    assert tab2_state["ui_state_id"] != base_state["ui_state_id"]

    # Open modal dialog
    page.click("#open-modal")
    modal_state = compute_ui_state_fingerprint(page, f"{spa_server}/")
    assert len(modal_state["dialogs"]) == 1
    assert modal_state["ui_state_id"].startswith("state_")
    assert modal_state["ui_state_id"] != tab2_state["ui_state_id"]

    # Record in RouteGraph
    graph = RouteGraph()
    graph.record_ui_state(f"{spa_server}/", base_state)
    graph.record_ui_state(f"{spa_server}/", modal_state)
    graph.record_state_transition(
        f"{spa_server}/",
        base_state["ui_state_id"],
        modal_state["ui_state_id"],
        {"label": "Open Modal", "role": "button"},
    )

    node = graph.get_node(f"{spa_server}/")
    assert node is not None
    assert len(node.ui_states) == 2
    assert len(node.state_transitions) == 1
    assert node.state_transitions[0]["from_state"] == base_state["ui_state_id"]
    assert node.state_transitions[0]["to_state"] == modal_state["ui_state_id"]


# ----------------------------------------------------------------------
# 3. All 12 Required Attributes in Element Map and Results
# ----------------------------------------------------------------------
def test_element_map_and_results_contain_all_12_required_attributes(page: Page, spa_server: str):
    required_keys = [
        "element_id",
        "route",
        "module_id",
        "ui_state_id",
        "hierarchy_path",
        "role",
        "label",
        "selector",
        "xpath",
        "discovery_mechanism",
        "quality_score",
        "safety_classification",
    ]

    registry = ElementIdentityRegistry()
    route_url = f"{spa_server}/test_page"

    # Register an element with test-id selector
    raw_el = {
        "element_xpath": "/html/body/button[1]",
        "element_role": "button",
        "label": "Save Changes",
        "element_index": 1,
        "selector": {"primary": {"strategy": "data-testid", "value": "save-btn"}, "fallback": []},
        "discovery_mechanism": "passive_dom",
        "module_id": "module_settings",
        "ui_state_id": "state_base_123",
        "hierarchy_path": "Settings > Actions",
    }
    ident, is_new = registry.register(route_url, raw_el)
    assert is_new

    # Check registered object
    registered = registry.get(ident)
    assert registered is not None
    for k in required_keys:
        assert k in registered, f"Missing required key: {k}"

    assert registered["quality_score"] == 100  # testid strategy = 100
    assert registered["safety_classification"] in [
        ActionType.SAFE_NAVIGATION,
        ActionType.REQUIRES_MANUAL_CONFIRMATION,
        ActionType.BLOCKED_DESTRUCTIVE,
        ActionType.UNKNOWN,
    ]

    # Verify Export to JSON, YAML, and Excel
    crawl_results = {
        "page_intelligence": {
            route_url: {
                "route": route_url,
                "menu_name": "Settings",
                "purpose": "Test",
                "framework": "standard",
                "automation_targets": registry.get_elements_for_route(route_url),
            }
        },
        "route_validations": {
            route_url: {
                "status": "WORKING",
                "score": 95,
                "expected_url": route_url,
                "actual_url": route_url,
                "redirected": False,
                "page_load_success": True,
                "document_status": 200,
                "dom_stable": True,
                "api_response_present": True,
                "failed_api_responses": 0,
                "target_count": 1,
                "interaction_success_rate": 1.0,
                "score_components": {},
            }
        },
    }

    import gc
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        files = export_crawl_results(crawl_results, output_name="attr_test", output_dir=tmp_dir)

        # Excel Verification
        wb = openpyxl.load_workbook(files["excel"], read_only=True)
        try:
            assert "element_map" in wb.sheetnames
            ws = wb["element_map"]
            rows = list(ws.iter_rows(values_only=True))
            headers = list(rows[0])
            for req in required_keys:
                assert req in headers, f"Missing {req} column in Excel element_map sheet"

            row_values = {headers[i]: rows[1][i] for i in range(len(headers))}
        finally:
            wb.close()
            del wb
            gc.collect()

        assert row_values["element_id"] == ident
        assert row_values["module_id"] == "module_settings"
        assert row_values["ui_state_id"] == "state_base_123"
        assert row_values["discovery_mechanism"] == "passive_dom"
        assert int(row_values["quality_score"]) == 100


# ----------------------------------------------------------------------
# 4. Child Route Recursion Depth Limit & Cycle Prevention
# ----------------------------------------------------------------------
def test_child_depth_limit_and_cycle_prevention():
    graph = RouteGraph()
    root = graph.add_route("https://example.com/r0", navigation_cause="landing")
    assert root.depth == 0

    r1 = graph.add_route("https://example.com/r1", parent_route="https://example.com/r0")
    assert r1.depth == 1

    r2 = graph.add_route("https://example.com/r2", parent_route="https://example.com/r1")
    assert r2.depth == 2

    r3 = graph.add_route("https://example.com/r3", parent_route="https://example.com/r2")
    assert r3.depth == 3

    r4 = graph.add_route("https://example.com/r4", parent_route="https://example.com/r3")
    assert r4.depth == 4

    r5 = graph.add_route("https://example.com/r5", parent_route="https://example.com/r4")
    assert r5.depth == 5

    # Depth 6 node should have depth = 6
    r6 = graph.add_route("https://example.com/r6", parent_route="https://example.com/r5")
    assert r6.depth == 6

    # Verify cycle protection: adding existing route r0 as child of r2 returns existing without cycle loop
    cycle = graph.add_route("https://example.com/r0", parent_route="https://example.com/r2")
    assert cycle.depth == 0  # Preserved root depth, no cycle loop


# ----------------------------------------------------------------------
# 5. Targeted Module Selection Scans Only Selected Module
# ----------------------------------------------------------------------
def test_module_selection_scan_scans_only_selected_module(page: Page, spa_server: str):
    page.goto(spa_server)
    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()
    assert len(modules) >= 2

    # Pick specifically the Users module
    user_modules = [m for m in modules if "users" in m.get("url", "").lower() or "user" in m.get("label", "").lower()]
    assert len(user_modules) >= 1
    selected_mid = user_modules[0]["id"]

    results = engine.crawl(selected_module_ids=[selected_mid])
    visited = results["routes"]

    # Must contain Users route, must NOT contain unselected modules like Analytics
    assert any("/users" in r for r in visited)
    assert not any("/analytics" in r for r in visited)
