import tempfile
from types import SimpleNamespace
from pathlib import Path

from automation_framework.crawler.route_graph import RouteGraph, RouteNode, normalize_route_url
from automation_framework.crawler.route_health_scorer import RouteStatus, score_route
from automation_framework.engine.element_identity_registry import (
    ElementIdentityRegistry,
    compute_element_identity,
    normalize_label,
)
from automation_framework.storage.checkpoint_store import CheckpointStore, compute_namespace
from automation_framework.utils.redactor import redact_sensitive_data
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.route_tracker import RouteTracker


def test_route_health_scorer_working():
    res = score_route(
        expected_url="https://example.com/app/users",
        actual_url="https://example.com/app/users",
        stable=True,
        target_count=6,
        api_responses=[{"url": "https://example.com/api/users", "status": 200}],
        interaction_stats={"attempted": 2, "succeeded": 2},
    )
    assert res["status"] == RouteStatus.WORKING
    assert res["score"] >= 80
    assert not res["redirected"]
    assert res["dom_stable"]


def test_route_health_scorer_partial_on_failed_api_or_redirect():
    res = score_route(
        expected_url="https://example.com/app/users",
        actual_url="https://example.com/app/login",  # redirected
        stable=True,
        target_count=3,
        api_responses=[{"url": "https://example.com/api/users", "status": 500}],  # failed API
    )
    assert res["status"] == RouteStatus.PARTIAL or res["status"] == RouteStatus.NON_WORKING


def test_route_health_scorer_non_working_on_unstable_dom_or_scan_error():
    res = score_route(
        expected_url="https://example.com/app/broken",
        actual_url="https://example.com/app/broken",
        stable=False,
        target_count=0,
        scan_error=True,
    )
    assert res["status"] == RouteStatus.NON_WORKING
    assert res["score"] < 40


def test_element_identity_composite_key():
    id1 = compute_element_identity("https://example.com/page", "/html/body/div[1]/button", "button", "Submit", 1)
    id2 = compute_element_identity("https://example.com/page", "/html/body/div[2]/button", "button", "Submit", 2)
    assert id1 != id2  # Never deduplicate by label alone

    # Same element with different spacing/case should produce same normalized identity
    id3 = compute_element_identity("https://example.com/page", "/html/body/div[1]/button", "button", " Submit  ", 1)
    assert id1 == id3


def test_element_identity_registry_preserves_fallbacks():
    reg = ElementIdentityRegistry()
    el1 = {
        "element_xpath": "/html/body/button",
        "element_role": "button",
        "label": "Save",
        "element_index": 1,
        "selector": {"primary": {"strategy": "role", "value": "button[name=Save]"}, "fallback": ["#save-btn"]},
    }
    ident, is_new = reg.register("https://example.com/app", el1)
    assert is_new

    # Re-registering with an extra fallback
    el2 = {
        "element_xpath": "/html/body/button",
        "element_role": "button",
        "label": "Save",
        "element_index": 1,
        "selector": {"primary": {"strategy": "role", "value": "button[name=Save]"}, "fallback": [".btn-save"]},
    }
    ident2, is_new2 = reg.register("https://example.com/app", el2)
    assert not is_new2
    assert ident == ident2
    # Verify fallback merged
    stored = reg.get(ident)
    assert "#save-btn" in stored["selector"]["fallback"]
    assert ".btn-save" in stored["selector"]["fallback"]


def test_checkpoint_store_atomic_save_and_load():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cp_file = Path(tmp_dir) / "checkpoint_test.json"
        store = CheckpointStore(cp_file)
        data = {
            "session_id": "test_session",
            "namespace": compute_namespace("https://example.com/app"),
            "visited_routes": ["https://example.com/app/home"],
            "pending_routes": [],
            "parking_lot_actions": [{"label": "Delete All", "classification": "blocked_destructive"}],
            "page_intelligence": {
                "https://example.com/app/home": {
                    "menu_name": "Home",
                    "automation_targets": [{"label": "View"}],
                }
            },
        }

        ok = store.save(data)
        assert ok
        assert cp_file.exists()

        loaded = store.load(expected_base_url="https://example.com/app")
        assert loaded is not None
        assert loaded["session_id"] == "test_session"
        assert len(loaded["parking_lot_actions"]) == 1
        assert loaded["parking_lot_actions"][0]["label"] == "Delete All"

        # Loading with mismatched namespace should reject
        mismatched = store.load(expected_base_url="https://other.com/portal")
        assert mismatched is None


def test_checkpoint_store_retains_manual_locator_evidence():
    """Resume data must retain the human-taught locator audit trail."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = CheckpointStore(Path(tmp_dir) / "interaction_checkpoint.json")
        interaction = {
            "type": "click", "label": "Add to cart", "selector": "#add-cart",
            "xpath": "//*[@id='add-cart']", "page_url": "https://example.com/shop",
            "source": "interactive_learning", "route": "https://example.com/shop",
        }
        assert store.save({
            "session_id": "interactive_run",
            "namespace": compute_namespace("https://example.com"),
            "manual_interactions": [interaction],
            "interaction_flow": [{"step": "click", "selector": "#add-cart", "route": "https://example.com/shop"}],
        })
        loaded = store.load(expected_base_url="https://example.com")
        assert loaded is not None
        assert loaded["manual_interactions"] == [interaction]
        assert loaded["interaction_flow"][0]["selector"] == "#add-cart"


def test_redaction_preserves_url_keyed_route_graph_nodes():
    """A URL containing `token` text is a route key, not a credential field."""
    route = "https://example.com/enrollment-tokens"
    payload = {"route_graph": {route: {"route": route, "status": "WORKING_ROUTE"}}}

    redacted = redact_sensitive_data(payload)

    assert isinstance(redacted["route_graph"][route], dict)
    assert redacted["route_graph"][route]["route"] == route


def test_route_graph_load_skips_a_legacy_redacted_entry():
    good_route = "https://example.com/dashboard"
    graph = RouteGraph.from_dict({
        good_route: {"route": good_route, "status": "WORKING_ROUTE", "visited": True},
        "https://example.com/enrollment-tokens": "[REDACTED]",
    })

    assert graph.get_node(good_route) is not None
    assert graph.is_visited(good_route)
    assert graph.get_node("https://example.com/enrollment-tokens") is None


def test_menu_list_preserves_rendered_dom_order_across_navigation_buckets():
    """Menu discovery must follow the target DOM, never bucket/alphabetical order."""
    engine = object.__new__(CrawlerEngine)
    engine.base_url = "https://example.com"
    engine.page = SimpleNamespace(url="https://example.com")
    engine.route_tracker = RouteTracker()
    engine.feature_routes = {}
    shell = {
        "global_navigation": {
            "sidebar_menus": [{"selector": "nav", "items": [
                {"text": "First", "href": "/first", "selector": "#first", "dom_order": 3},
            ]}],
            "top_navigation_menus": [{"selector": "header", "items": [
                {"text": "Second", "href": "/second", "selector": "#second", "dom_order": 1},
                {"text": "Third", "href": "/third", "selector": "#third", "dom_order": 2},
                {"text": "Same route action", "href": "/second", "selector": "#same-route", "dom_order": 4},
            ]}],
        }
    }
    modules = engine._build_menu_list(shell)
    assert [item["label"] for item in modules] == ["Second", "Third", "First", "Same route action"]
    assert len({item["id"] for item in modules}) == 4


def test_menu_list_reconstructs_parent_links_for_nested_navigation():
    """Nested target menus retain a stable parent module for audit/export use."""
    engine = object.__new__(CrawlerEngine)
    engine.base_url = "https://example.com"
    engine.page = SimpleNamespace(url="https://example.com")
    engine.route_tracker = RouteTracker()
    engine.feature_routes = {}
    shell = {"global_navigation": {"sidebar_menus": [{"selector": "aside", "items": [
        {"text": "Assets", "href": "/assets", "selector": "#assets", "dom_order": 1, "hierarchy_level": 0},
        {"text": "Hardware", "href": "/assets/hardware", "selector": "#hardware", "dom_order": 2, "hierarchy_level": 1},
        {"text": "Software", "href": "/assets/software", "selector": "#software", "dom_order": 3, "hierarchy_level": 1},
    ]}]}}
    modules = engine._build_menu_list(shell)
    assert modules[1]["parent_module"] == modules[0]["id"]
    assert modules[2]["parent_module"] == modules[0]["id"]


def test_checkpoint_route_selection_is_restored_when_not_in_current_navigation():
    """Resume must execute every selected checkpoint route, not only visible menus."""
    menu_items = [{"id": "control:#menu|menu", "label": "Menu", "url": ""}]
    selected_items = list(menu_items)
    saved_route_id = "route:https://example.com/inventory"

    restored = CrawlerEngine._restore_missing_saved_route_selections(
        menu_items=menu_items,
        selected_items=selected_items,
        selected_module_ids=["control:#menu|menu", saved_route_id],
        base_url="https://example.com",
    )

    assert restored == 1
    assert [item["id"] for item in menu_items] == ["control:#menu|menu", saved_route_id]
    recovered = menu_items[-1]
    assert recovered["url"] == "https://example.com/inventory"
    assert recovered["source"] == "checkpoint_route_selection"
    assert recovered in selected_items


def test_checkpoint_route_selection_does_not_restore_other_origins():
    menu_items: list[dict] = []
    selected_items: list[dict] = []

    restored = CrawlerEngine._restore_missing_saved_route_selections(
        menu_items=menu_items,
        selected_items=selected_items,
        selected_module_ids=["route:https://outside.example/steal"],
        base_url="https://example.com",
    )

    assert restored == 0
    assert not menu_items
