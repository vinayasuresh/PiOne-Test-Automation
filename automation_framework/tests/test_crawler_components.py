import tempfile
from pathlib import Path

from automation_framework.crawler.route_graph import RouteGraph, RouteNode, normalize_route_url
from automation_framework.crawler.route_health_scorer import RouteStatus, score_route
from automation_framework.engine.element_identity_registry import (
    ElementIdentityRegistry,
    compute_element_identity,
    normalize_label,
)
from automation_framework.storage.checkpoint_store import CheckpointStore, compute_namespace


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
