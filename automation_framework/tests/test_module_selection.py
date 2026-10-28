"""test_module_selection.py: Deterministic tests for Phase 3 Module-Level Interactive Workflow.

Covers:
1. Module discovery (authenticated / shell discovery).
2. Selecting one module.
3. Selecting multiple modules.
4. Selecting all modules.
5. Explicit decision: continue to next module.
6. Explicit decision: retry current module.
7. Explicit decision: end scan and save.
8. Checkpoint persistence and resume across engine instances.
9. Resumption after browser refresh.
10. Authentication expiry detection and recovery during module scanning.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from playwright.sync_api import sync_playwright, Page

from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError
from automation_framework.storage.checkpoint_store import CheckpointStore
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
# 1. Module Discovery
# ----------------------------------------------------------------------
def test_module_discovery(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)

    modules = engine.discover_modules()
    assert len(modules) >= 3, f"Expected at least 3 modules, found {len(modules)}"

    labels = [m["label"] for m in modules]
    assert "Home" in labels
    assert "Users" in labels
    assert "Analytics" in labels

    for mod in modules:
        assert "id" in mod
        assert "url" in mod
        assert "confidence" in mod
        assert mod["id"].startswith("route:") or mod["id"].startswith("control:")


# ----------------------------------------------------------------------
# 2. Selecting One Module
# ----------------------------------------------------------------------
def test_selecting_one_module(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()

    users_mod = next(m for m in modules if m["label"] == "Users")
    users_mod_id = users_mod["id"]

    results = engine.crawl(start_url=spa_server, selected_module_ids=[users_mod_id])

    # Visited Users
    visited = engine.route_tracker.get_visited_routes()
    assert any("/users" in r for r in visited)
    # Did NOT visit Analytics
    assert not any("/analytics" in r for r in visited)

    assert users_mod_id in engine.completed_modules
    assert len(engine.completed_modules) == 1
    assert results["completed_modules"] == [users_mod_id]


# ----------------------------------------------------------------------
# 3. Selecting Multiple Modules
# ----------------------------------------------------------------------
def test_selecting_multiple_modules(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()

    selected_ids = [
        m["id"] for m in modules if m["label"] in {"Users", "Analytics"}
    ]
    assert len(selected_ids) == 2

    results = engine.crawl(start_url=spa_server, selected_module_ids=selected_ids)

    visited = engine.route_tracker.get_visited_routes()
    assert any("/users" in r for r in visited)
    assert any("/analytics" in r for r in visited)

    for sid in selected_ids:
        assert sid in engine.completed_modules
    assert len(engine.completed_modules) == 2


def test_resume_honours_selected_saved_route_missing_from_current_menu(page: Page, spa_server: str):
    """A checkpoint route selection must not leave a run permanently partial.

    ``/details`` is reachable in the fixture but deliberately is not in its
    persistent navigation.  This models an authenticated landing/deep-link
    route that was selected before a later menu discovery changes.
    """
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    saved_route_id = f"route:{spa_server}/details"

    results = engine.crawl(start_url=spa_server, selected_module_ids=[saved_route_id])

    assert saved_route_id in engine.completed_modules
    assert results["completed_modules"] == [saved_route_id]
    assert any("/details" in route for route in engine.route_tracker.get_visited_routes())


# ----------------------------------------------------------------------
# 4. Selecting All Modules
# ----------------------------------------------------------------------
def test_selecting_all_modules(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    engine = CrawlerEngine(page, base_url=spa_server)
    modules = engine.discover_modules()
    all_ids = [m["id"] for m in modules]

    results = engine.crawl(start_url=spa_server, selected_module_ids=all_ids)

    visited = engine.route_tracker.get_visited_routes()
    for mod in modules:
        if mod.get("url"):
            norm = engine.route_tracker.normalize_url(mod["url"])
            assert norm in visited

    assert len(engine.completed_modules) >= 3


# ----------------------------------------------------------------------
# 5. Explicit Decision: Continue to Next Module
# ----------------------------------------------------------------------
def test_module_review_decision_continue(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    events = []
    engine = CrawlerEngine(
        page,
        base_url=spa_server,
        interactive_mode=True,
        interactive_timeout=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )

    modules = engine.discover_modules()
    selected_ids = [
        m["id"] for m in modules if m["label"] in {"Users", "Analytics"}
    ]

    # Mock wait_for_user to simulate user clicking 'Continue Next'
    with patch("automation_framework.crawler.crawler_engine.wait_for_user", return_value="continue"):
        results = engine.crawl(start_url=spa_server, selected_module_ids=selected_ids)

    # Check review events and summary data
    review_started = [e for e in events if e[0] == "module_review_started"]
    assert len(review_started) >= 2

    first_summary = review_started[0][1]["summary"]
    assert "routes_scanned" in first_summary
    assert "elements_found" in first_summary
    assert "safe_actions_explored" in first_summary
    assert "parking_lot_count" in first_summary
    assert "route_health" in first_summary
    assert "errors_count" in first_summary

    review_completed = [e for e in events if e[0] == "module_review_completed"]
    assert len(review_completed) >= 2
    assert all(rc[1]["decision"] == "continue" for rc in review_completed)

    for sid in selected_ids:
        assert engine.module_decisions.get(sid) == "continue"
        assert sid in engine.completed_modules


# ----------------------------------------------------------------------
# 6. Explicit Decision: Retry Current Module
# ----------------------------------------------------------------------
def test_module_review_decision_retry(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    events = []
    engine = CrawlerEngine(
        page,
        base_url=spa_server,
        interactive_mode=True,
        interactive_timeout=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )

    modules = engine.discover_modules()
    users_mod = next(m for m in modules if m["label"] == "Users")

    # First call returns 'retry', second returns 'continue'
    decision_sequence = ["retry", "continue"]

    def mock_decision(*args, **kwargs):
        if decision_sequence:
            return decision_sequence.pop(0)
        return "continue"

    with patch("automation_framework.crawler.crawler_engine.wait_for_user", side_effect=mock_decision):
        results = engine.crawl(start_url=spa_server, selected_module_ids=[users_mod["id"]])

    review_completed = [e for e in events if e[0] == "module_review_completed"]
    # Module Users was reviewed twice: once for retry, once for continue
    assert len(review_completed) == 2
    assert review_completed[0][1]["decision"] == "retry"
    assert review_completed[1][1]["decision"] == "continue"

    assert users_mod["id"] in engine.completed_modules
    assert engine.module_decisions[users_mod["id"]] == "continue"


# ----------------------------------------------------------------------
# 7. Explicit Decision: End Scan and Save
# ----------------------------------------------------------------------
def test_module_review_decision_end_and_save(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    events = []
    engine = CrawlerEngine(
        page,
        base_url=spa_server,
        interactive_mode=True,
        interactive_timeout=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )

    modules = engine.discover_modules()
    selected_ids = [
        m["id"] for m in modules if m["label"] in {"Users", "Analytics"}
    ]

    # User chooses 'end' at the first module
    with patch("automation_framework.crawler.crawler_engine.wait_for_user", return_value="end"):
        results = engine.crawl(start_url=spa_server, selected_module_ids=selected_ids)

    assert engine.scan_stop_requested is True
    assert results["scan_stopped_early"] is True

    # First module completed, second module was NOT visited
    assert len(engine.completed_modules) == 1
    first_id = selected_ids[0]
    assert first_id in engine.completed_modules
    second_id = selected_ids[1]
    assert second_id not in engine.completed_modules

    stop_events = [e for e in events if e[0] == "scan_stopped"]
    assert len(stop_events) >= 1


# ----------------------------------------------------------------------
# 8. Checkpoint Persistence and Resume Across Instances
# ----------------------------------------------------------------------
def test_module_checkpoint_persistence_and_resume(page: Page, spa_server: str):
    with tempfile.TemporaryDirectory() as tmp_dir:
        checkpoint_file = Path(tmp_dir) / "checkpoint_test.json"
        store = CheckpointStore(checkpoint_file)

        page.goto(spa_server, wait_until="domcontentloaded")
        engine1 = CrawlerEngine(
            page,
            base_url=spa_server,
            checkpoint_path=checkpoint_file,
            checkpoint_store=store,
        )

        modules = engine1.discover_modules()
        selected_ids = [
            m["id"] for m in modules if m["label"] in {"Users", "Analytics"}
        ]

        # Scan ONLY Users with engine 1
        engine1.crawl(start_url=spa_server, selected_module_ids=[selected_ids[0]])
        assert selected_ids[0] in engine1.completed_modules
        assert checkpoint_file.exists()

        # Engine 2 loads checkpoint
        engine2 = CrawlerEngine(
            page,
            base_url=spa_server,
            checkpoint_path=checkpoint_file,
            checkpoint_store=store,
            resume_from_checkpoint=True,
        )

        assert engine2.checkpoint_loaded is True
        assert selected_ids[0] in engine2.completed_modules

        # Resume scan with both modules selected
        results2 = engine2.crawl(start_url=spa_server, selected_module_ids=selected_ids)

        # Both modules are now completed, Users was not re-crawled unnecessarily
        assert selected_ids[0] in engine2.completed_modules
        assert selected_ids[1] in engine2.completed_modules
        assert len(engine2.completed_modules) == 2


# ----------------------------------------------------------------------
# 9. Browser Refresh Resumption
# ----------------------------------------------------------------------
def test_module_workflow_browser_refresh_resumption(page: Page, spa_server: str):
    with tempfile.TemporaryDirectory() as tmp_dir:
        checkpoint_file = Path(tmp_dir) / "checkpoint_refresh.json"
        store = CheckpointStore(checkpoint_file)

        page.goto(spa_server, wait_until="domcontentloaded")
        engine = CrawlerEngine(
            page,
            base_url=spa_server,
            checkpoint_path=checkpoint_file,
            checkpoint_store=store,
        )

        modules = engine.discover_modules()
        users_mod = next(m for m in modules if m["label"] == "Users")

        # Scan Users module
        engine.crawl(start_url=spa_server, selected_module_ids=[users_mod["id"]])
        assert users_mod["id"] in engine.completed_modules

        # Simulate browser refresh
        page.reload(wait_until="domcontentloaded")

        # Create refreshed engine with checkpoint resumption
        engine_refreshed = CrawlerEngine(
            page,
            base_url=spa_server,
            checkpoint_path=checkpoint_file,
            checkpoint_store=store,
            resume_from_checkpoint=True,
        )

        assert users_mod["id"] in engine_refreshed.completed_modules
        assert any("/users" in r for r in engine_refreshed.page_intelligence)
        assert hasattr(engine_refreshed.route_tracker, "graph")
        assert len(engine_refreshed.route_tracker.graph.nodes) > 0


# ----------------------------------------------------------------------
# 10. Authentication Expiry and Recovery During Module Scanning
# ----------------------------------------------------------------------
def test_expired_auth_recovery_during_module_scan(page: Page, spa_server: str):
    page.goto(spa_server, wait_until="domcontentloaded")
    login_attempted = False

    def mock_login(pg, user, pwd, base):
        nonlocal login_attempted
        login_attempted = True
        # Simulate successful login navigating back to dashboard
        pg.goto(spa_server, wait_until="domcontentloaded")

    with patch("automation_framework.crawler.crawler_engine.login", side_effect=mock_login):
        engine = CrawlerEngine(
            page,
            base_url=spa_server,
            credentials=("testuser", "testpassword"),
        )

        # Force browser to login page to simulate session expiration
        page.goto(f"{spa_server}/login", wait_until="domcontentloaded")

        # Calling _ensure_authenticated should trigger recovery
        engine._ensure_authenticated()

        assert login_attempted is True
        assert page.url.rstrip("/") == spa_server.rstrip("/")

    # If credentials are not available, it must raise AuthenticationExpiredError
    engine_no_creds = CrawlerEngine(page, base_url=spa_server, credentials=None)
    page.goto(f"{spa_server}/login", wait_until="domcontentloaded")
    with pytest.raises(AuthenticationExpiredError):
        engine_no_creds._ensure_authenticated()
