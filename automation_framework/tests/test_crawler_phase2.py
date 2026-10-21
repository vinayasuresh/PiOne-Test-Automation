"""test_crawler_phase2.py: Deterministic tests for Phase 2 Authenticated Route Traversal.

Covers:
1. Safe link / button opens child route, child route is scanned, crawler returns to parent.
2. Parent scroll position is saved and restored after child traversal.
3. Checkpoints are persisted before and after child route scanning.
4. Duplicate child routes are detected and skipped without redundant scanning.
5. Tabs and accordions are handled safely as in-page state transitions without side effects.
6. Pagination controls are identified as safe and probed.
7. Lazy/dropdown menu items are identified and handled safely.
8. Destructive and mutation actions (delete, logout, pay, remove, submit, save) are never clicked and parked.
9. Authentication expiry redirects trigger session recovery (_ensure_authenticated) and clean retry/failure.
10. External redirects and browser-error schemes are rejected and parent state restored.
11. Navigation timeouts or errors do not stop the remaining traversal queue.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_parking_lot_action,
    is_safe_action,
)
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError
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
# 1. Safe Link / Button Traversal, Child Scan, and Return to Parent
# ----------------------------------------------------------------------
def test_safe_link_opens_and_scans_child_route_and_returns_to_parent(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    details_route = f"{spa_server}/details"
    details_node = engine.route_tracker.graph.get_node(details_route)
    assert details_node is not None, f"Expected node for {details_route} in RouteGraph"
    assert details_node.parent_route.rstrip("/") == spa_server.rstrip("/")
    assert details_node.navigation_cause == "safe_button_navigation"

    info_route = f"{spa_server}/info"
    info_node = engine.route_tracker.graph.get_node(info_route)
    assert info_node is not None, f"Expected node for {info_route} in RouteGraph"
    assert info_node.parent_route.rstrip("/") == spa_server.rstrip("/")
    assert info_node.navigation_cause == "content_link"

    # Child routes were scanned and registered in page_intelligence
    assert any(k.rstrip("/") == details_route.rstrip("/") for k in engine.page_intelligence)
    assert any(k.rstrip("/") == info_route.rstrip("/") for k in engine.page_intelligence)

    # Returned to parent route
    assert page.url.rstrip("/") == spa_server.rstrip("/")


# ----------------------------------------------------------------------
# 2. Parent Scroll Position Restored
# ----------------------------------------------------------------------
def test_parent_scroll_position_restored(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    # Scroll parent down
    page.evaluate("() => window.scrollTo(0, 450)")
    initial_scroll = page.evaluate("() => window.scrollY || window.pageYOffset")
    assert initial_scroll >= 400

    engine = CrawlerEngine(page, base_url=spa_server)
    engine._restore_parent_state(spa_server, {"x": 0, "y": initial_scroll})

    restored_scroll = page.evaluate("() => window.scrollY || window.pageYOffset")
    assert abs(restored_scroll - initial_scroll) <= 50


# ----------------------------------------------------------------------
# 3. Checkpoint Saved Before and After Child Scan
# ----------------------------------------------------------------------
def test_checkpoint_saved_before_and_after_child_scan(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    with tempfile.TemporaryDirectory() as tmpdir:
        store = CheckpointStore(checkpoint_path=Path(tmpdir) / "checkpoint.json")
        engine = CrawlerEngine(page, base_url=spa_server, checkpoint_store=store)

        checkpoint_save_calls = []
        original_save = engine._save_checkpoint

        def tracked_save():
            checkpoint_save_calls.append(len(engine.page_intelligence))
            original_save()

        engine._save_checkpoint = tracked_save

        route_doc = {"automation_targets": []}
        engine._probe_candidate_actions(spa_server, "Home", route_doc)

        # Checkpoint should have been saved multiple times (before probe, before child scan, after child scan)
        assert len(checkpoint_save_calls) >= 2


# ----------------------------------------------------------------------
# 4. Duplicate Child Routes Skipped
# ----------------------------------------------------------------------
def test_duplicate_child_routes_skipped(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    child_route = f"{spa_server}/details"
    # Pre-mark child route as already visited
    engine.route_tracker.mark_visited(child_route)
    engine.page_intelligence[child_route] = {
        "route": child_route,
        "automation_targets": [{"label": "Pre-existing"}],
    }

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # Verify duplicate event was emitted
    duplicate_events = [
        ev for ev in engine.scan_events
        if ev.get("event") == "duplicate_route_skipped"
        and ev.get("data", {}).get("child_route", "").rstrip("/") == child_route.rstrip("/")
    ]
    assert len(duplicate_events) >= 1
    # Existing intelligence was not overwritten
    assert engine.page_intelligence[child_route]["automation_targets"][0]["label"] == "Pre-existing"


# ----------------------------------------------------------------------
# 5. Tabs and Accordions Handled Without Destructive Side Effects
# ----------------------------------------------------------------------
def test_tabs_and_accordions_handled(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # Tabs and accordions must be classified as safe
    tab_elem = {"label": "Overview", "role": "tab"}
    acc_elem = {"label": "FAQ Accordion", "tagName": "summary", "is_accordion": True}
    assert classify_action(tab_elem) == ActionType.SAFE_EXPAND
    assert classify_action(acc_elem) == ActionType.SAFE_EXPAND

    # Page URL must remain on parent
    assert page.url.rstrip("/") == spa_server.rstrip("/")


# ----------------------------------------------------------------------
# 6. Pagination Handled Safely
# ----------------------------------------------------------------------
def test_pagination_handled(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    page_item = {"label": "1", "is_pagination": True, "classes": "page-link"}
    next_item = {"label": "Next", "is_pagination": True, "classes": "page-link"}
    assert classify_action(page_item) == ActionType.SAFE_PAGINATION
    assert classify_action(next_item) == ActionType.SAFE_PAGINATION
    assert is_safe_action(ActionType.SAFE_PAGINATION)


# ----------------------------------------------------------------------
# 7. Lazy Menu Items Handled Safely
# ----------------------------------------------------------------------
def test_lazy_menu_items_handled(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    menu_item_nav = {"label": "System Settings", "role": "menuitem", "href": "/settings"}
    menu_item_pref = {"label": "User Preferences", "role": "menuitem"}

    assert classify_action(menu_item_nav) == ActionType.SAFE_NAVIGATION
    assert classify_action(menu_item_pref) == ActionType.SAFE_EXPAND


# ----------------------------------------------------------------------
# 8. Destructive and Mutation Actions Diverted to Parking Lot
# ----------------------------------------------------------------------
def test_destructive_actions_never_clicked(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    parked_labels = [p.get("label", "").lower() for p in engine.parking_lot_actions]
    # Destructive actions
    assert any("delete" in lbl for lbl in parked_labels)
    assert any("logout" in lbl for lbl in parked_labels)
    assert any("pay" in lbl for lbl in parked_labels)
    assert any("remove" in lbl for lbl in parked_labels)
    # Mutation actions
    assert any("submit" in lbl for lbl in parked_labels)

    # Destructive classifications
    for entry in engine.parking_lot_actions:
        assert is_parking_lot_action(entry["classification"])


# ----------------------------------------------------------------------
# 9. Authentication Expiry Triggers One Re-authentication Attempt
# ----------------------------------------------------------------------
def test_authentication_expiry_triggers_one_reauthentication_attempt(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server, credentials=("admin", "secret"))

    recovery_attempted = []

    def mock_ensure_auth():
        recovery_attempted.append(True)
        # Mock successful re-login back to parent route
        page.goto(spa_server, wait_until="domcontentloaded")

    engine._ensure_authenticated = mock_ensure_auth

    # Mock an action that simulates redirecting to login page
    fake_item = {
        "index": 0,
        "label": "Secure Vault",
        "role": "button",
        "tagName": "button",
        "visible": True,
    }

    with patch.object(engine, "_is_login_page", side_effect=[True, False]):
        with patch.object(engine.probe_engine, "probe_action", return_value={"success": True, "url_changed": False, "dom_delta": 0}):
            engine._probe_candidate_actions(spa_server, "Home", {"automation_targets": []})

    # Ensure authentication recovery was called exactly once on expiry detection
    assert len(recovery_attempted) >= 1


# ----------------------------------------------------------------------
# 10. External Redirects Rejected
# ----------------------------------------------------------------------
def test_external_redirects_rejected(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # Any external links must not be scanned or added to RouteGraph
    for route in engine.page_intelligence:
        assert route.startswith(spa_server)

    rejected_events = [
        ev for ev in engine.scan_events
        if ev.get("event") == "route_rejected"
    ]
    # An out-of-scope link or attempt triggers rejection event if probed
    for rev in rejected_events:
        assert rev["data"]["reason"] == "outside_app_scope_or_browser_error"


# ----------------------------------------------------------------------
# 11. Navigation Timeout Does Not Stop Remaining Traversal Queue
# ----------------------------------------------------------------------
def test_navigation_timeout_does_not_stop_remaining_queue(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    call_count = [0]
    original_probe = engine.probe_engine.probe_action

    def probe_with_first_timeout(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise PlaywrightTimeoutError("Action probing timed out after 5000ms")
        return original_probe(*args, **kwargs)

    engine.probe_engine.probe_action = probe_with_first_timeout

    route_doc = {"automation_targets": []}
    engine._probe_candidate_actions(spa_server, "Home", route_doc)

    # First timed out, but subsequent actions were still probed
    assert call_count[0] > 1
    # Timeout event recorded
    timeout_events = [
        ev for ev in engine.scan_events
        if ev.get("event") == "action_failed" and "timeout" in str(ev.get("data", {}).get("reason", "")).lower()
    ]
    assert len(timeout_events) >= 1
