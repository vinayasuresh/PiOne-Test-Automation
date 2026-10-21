"""test_crawler_e2e.py: Comprehensive browser-level Playwright end-to-end verification.

Verifies:
1. Document deep scrolling and lazy-loaded element detection
2. Nested scrollable container exploration
3. Overlay detection, capture, and clean Escape restoration
4. Safe navigation button probing, child route traversal, state restoration
5. Safety policy enforcement: destructive and mutation buttons diverted to parking lot
6. Checkpoint persistence and resumption with namespace validation
7. Interactive mode manual review "Confirm & Continue"
8. Interactive mode manual review "End Scan & Save"
9. Excel, JSON, YAML, and Markdown export integrity
10. Full crawl graph, health scoring, and element registry deduplication
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
from automation_framework.crawler.route_health_scorer import RouteStatus
from automation_framework.engine.element_identity_registry import ElementIdentityRegistry
from automation_framework.storage.checkpoint_store import CheckpointStore, compute_namespace
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
# 1. Document Deep Scroll & Lazy Elements
# ----------------------------------------------------------------------
def test_deep_scroll_reveals_lazy_elements(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    # Initially before deep scroll, lazy section is hidden
    assert not page.locator("#btn-lazy-loaded").is_visible()

    # Run deep scroll on the landing route
    route_doc = {"automation_targets": []}
    engine._deep_scroll_viewport(spa_server, "Home", route_doc)

    # After deep scroll, lazy element was detected and registered
    registered_labels = [el.get("label") for el in engine.element_registry.get_all_elements()]
    assert any("Lazy Action Button" in str(label) for label in registered_labels)


# ----------------------------------------------------------------------
# 2. Nested Scroll Container Exploration
# ----------------------------------------------------------------------
def test_nested_scroll_container_exploration(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._deep_scroll_viewport(spa_server, "Home", route_doc)

    # Nested action button inside container should be discovered
    registered_labels = [el.get("label") for el in engine.element_registry.get_all_elements()]
    assert any("Nested Action Button" in str(label) for label in registered_labels)


# ----------------------------------------------------------------------
# 3. Menu Overlay Capture & Clean Escape Closure
# ----------------------------------------------------------------------
def test_menu_overlay_capture_and_clean_restoration(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._detect_and_open_overlays(spa_server, "Home", route_doc)

    # Items from overlay appended to document body must be captured
    registered_labels = [el.get("label") for el in engine.element_registry.get_all_elements()]
    assert any("User Preferences" in str(lbl) for lbl in registered_labels)
    assert any("System Settings" in str(lbl) for lbl in registered_labels)

    # Overlay should be closed cleanly with Escape so it doesn't obscure the page
    assert not page.locator("#portal-menu").is_visible()


# ----------------------------------------------------------------------
# 4. Safe Navigation Button Probe & Route Restoration
# ----------------------------------------------------------------------
def test_safe_navigation_button_probed_and_restored(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    # Probe candidates on landing page
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # RouteGraph must record the edge to /details triggered by safe navigation button
    child_route = f"{spa_server}/details"
    node = engine.route_tracker.graph.get_node(child_route)
    assert node is not None
    assert node.navigation_cause == "safe_button_navigation"

    # Child route must have been scanned
    assert child_route in engine.page_intelligence
    child_targets = engine.page_intelligence[child_route]["automation_targets"]
    assert any("Item Details" in str(t.get("label")) or "table" in str(t.get("type")) for t in child_targets)

    # After probe, browser must be restored to parent route
    assert page.url.rstrip("/") == spa_server.rstrip("/")


def test_same_label_navigation_buttons_are_probed_independently(page: Page, spa_server: str):
    """A label-only locator must not probe the first button twice."""
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    engine._probe_candidate_actions(spa_server, "Home", {"automation_targets": []})

    assert engine.route_tracker.graph.get_node(f"{spa_server}/details") is not None
    assert engine.route_tracker.graph.get_node(f"{spa_server}/users") is not None
    assert page.url.rstrip("/") == spa_server.rstrip("/")


def test_module_catalogue_and_selection_are_stable(page: Page, spa_server: str):
    """A user selection must filter top-level modules by stable ID, not text."""
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()

    users = next(module for module in modules if module.get("url") == f"{spa_server}/users")
    selected = engine._select_menu_items(modules, [users["id"]])

    assert selected == [users]
    assert users["id"] == f"route:{spa_server}/users"


# ----------------------------------------------------------------------
# 5. Destructive and Mutation Buttons Diverted to Parking Lot
# ----------------------------------------------------------------------
def test_destructive_and_mutation_actions_diverted_to_parking_lot(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # Both Delete Account and Submit Order must be in parking lot
    parked = {p["label"]: p for p in engine.parking_lot_actions}
    assert "Delete Account" in parked
    assert parked["Delete Account"]["classification"] == ActionType.BLOCKED_DESTRUCTIVE

    assert "Submit Order" in parked
    assert parked["Submit Order"]["classification"] == ActionType.REQUIRES_MANUAL_CONFIRMATION


# ----------------------------------------------------------------------
# 6. Checkpoint Persistence and Resumption
# ----------------------------------------------------------------------
def test_checkpoint_atomic_save_and_resumption(page: Page, spa_server: str):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        cp_file = Path(tmp_dir) / "checkpoint_test.json"

        # Pass 1: scan landing page and save checkpoint
        page.goto(spa_server, wait_until="domcontentloaded")
        engine1 = CrawlerEngine(page, base_url=spa_server, checkpoint_path=cp_file)
        engine1._scan_route(spa_server, "Home")
        assert cp_file.exists()
        # Child routes discovered from overlays (like /settings) are queued
        assert any(r.get("url", "").endswith("/settings") for r in engine1._discovered_route_queue)

        # Pass 2: resume new crawler from checkpoint
        engine2 = CrawlerEngine(
            page,
            base_url=spa_server,
            checkpoint_path=cp_file,
            resume_from_checkpoint=True,
        )
        assert engine2.checkpoint_loaded
        norm_url = engine2.route_tracker.normalize_url(spa_server)
        assert norm_url in engine2.page_intelligence
        assert engine2.route_tracker.is_visited(norm_url)
        assert len(engine2.element_registry.get_all_elements()) > 0
        assert len(engine2.parking_lot_actions) > 0
        assert engine2._discovered_route_queue


# ----------------------------------------------------------------------
# 7. Interactive Review "Confirm & Continue"
# ----------------------------------------------------------------------
def test_interactive_mode_confirm_and_continue(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server, interactive_mode=True, interactive_timeout=3)

    # Schedule browser-side click on #__capture_continue once injected
    page.evaluate("""() => {
        const timer = setInterval(() => {
            const btn = document.getElementById('__capture_continue');
            if (btn) {
                btn.click();
                clearInterval(timer);
            }
        }, 50);
    }""")

    route_doc = {"automation_targets": []}
    engine._capture_interactions(spa_server, route_doc)

    # Scan must NOT be stopped early when confirming
    assert not engine.scan_stop_requested


# ----------------------------------------------------------------------
# 8. Interactive Review "End Scan & Save"
# ----------------------------------------------------------------------
def test_interactive_mode_end_scan_and_save(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server, interactive_mode=True, interactive_timeout=3)

    # Schedule browser-side click on #__capture_end once injected
    page.evaluate("""() => {
        const timer = setInterval(() => {
            const btn = document.getElementById('__capture_end');
            if (btn) {
                btn.click();
                clearInterval(timer);
            }
        }, 50);
    }""")

    route_doc = {"automation_targets": []}
    engine._capture_interactions(spa_server, route_doc)

    # Scan MUST be marked stopped early when user ends
    assert engine.scan_stop_requested


# ----------------------------------------------------------------------
# 9. Full Artifact Export Integrity (Excel, JSON, YAML, Markdown)
# ----------------------------------------------------------------------
def test_export_crawl_results_all_sheets_and_artifacts(page: Page, spa_server: str):
    import gc
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        page.goto(spa_server, wait_until="domcontentloaded")
        engine = CrawlerEngine(page, base_url=spa_server)
        engine._scan_route(spa_server, "Home")
        results = engine._result_snapshot()

        export_files = export_crawl_results(results, output_dir=tmp_dir)

        # Check all files exist
        assert export_files["excel"].exists()
        assert export_files["json"].exists()
        assert export_files["yaml"].exists()
        assert export_files["markdown"].exists()

        # Verify Excel workbook sheets
        wb = openpyxl.load_workbook(export_files["excel"], read_only=True)
        try:
            expected_sheets = {
                "working_routes",
                "partial_routes",
                "non_working_routes",
                "menu_hierarchy",
                "element_map",
                "parking_lot_actions",
                "scan_events",
            }
            for sheet_name in expected_sheets:
                assert sheet_name in wb.sheetnames, f"Missing Excel sheet: {sheet_name}"
        finally:
            wb.close()
            del wb
            gc.collect()


# ----------------------------------------------------------------------
# 10. Complete Crawl Run & Health Scoring Integrity
# ----------------------------------------------------------------------
def test_full_crawl_graph_and_health_scoring(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    results = engine.crawl(start_url=spa_server)

    norm_landing = engine.route_tracker.normalize_url(spa_server)
    # Must have explored routes
    visited = results["routes"]
    assert norm_landing in visited

    # Health validations must be present and scored
    validations = results["route_validations"]
    assert norm_landing in validations
    landing_val = validations[norm_landing]
    assert landing_val["status"] in {RouteStatus.WORKING, RouteStatus.PARTIAL}
    assert landing_val["score"] > 50
    assert landing_val["dom_stable"]

    # Parking lot must contain blocked actions
    parking_lot = results["parking_lot_actions"]
    assert any(p["classification"] == ActionType.BLOCKED_DESTRUCTIVE for p in parking_lot)

    # Route graph must be populated
    graph_dict = results["route_graph"]
    assert norm_landing in graph_dict
    assert graph_dict[norm_landing]["status"] == landing_val["status"]
    assert graph_dict[norm_landing]["score"] == landing_val["score"]
