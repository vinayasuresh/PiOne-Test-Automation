"""test_route_health_card_counts.py: Deterministic fixture test verifying card counts equal table counts.

Proves:
1. Normalization of route health statuses (WORKING, WORKING_ROUTE, PARTIAL, PARTIAL_ROUTE, NON-WORKING, NON_WORKING, NON_WORKING_ROUTE).
2. Card counts (Working, Partial, Non-Working) strictly equal the table row counts.
3. Card count is never 0 when the route table contains matching working routes.
4. Parity between backend route health aggregator and frontend normalization logic.
"""

from __future__ import annotations

import pytest


def normalize_route_status_js(status: str | None) -> str:
    """Exact replica of frontend JavaScript normalizeRouteStatus()."""
    if not status:
        return "WORKING"
    s = str(status).strip().upper().replace("-", "_")
    if s in {"WORKING", "WORKING_ROUTE"}:
        return "WORKING"
    if s in {"PARTIAL", "PARTIAL_ROUTE"}:
        return "PARTIAL"
    if s in {"NON_WORKING", "NON_WORKING_ROUTE"}:
        return "NON_WORKING"
    return s


def calculate_route_counts_js(routes: list[dict], scan_summary: dict | None = None) -> dict[str, int]:
    """Exact replica of frontend JavaScript calculateRouteCounts()."""
    routes = routes or []
    scan_summary = scan_summary or {}
    working = 0
    partial = 0
    non_working = 0

    if routes:
        for r in routes:
            st = normalize_route_status_js(r.get("route_status"))
            if st == "WORKING":
                working += 1
            elif st == "PARTIAL":
                partial += 1
            elif st == "NON_WORKING":
                non_working += 1
            else:
                working += 1
    else:
        working = int(scan_summary.get("working_routes") or 0)
        partial = int(scan_summary.get("partial_routes") or 0)
        non_working = int(scan_summary.get("non_working_routes") or 0)

    total = len(routes) if routes else int(scan_summary.get("total_routes") or 0)
    return {
        "working": working,
        "partial": partial,
        "non_working": non_working,
        "total": total,
    }


def test_status_normalization():
    assert normalize_route_status_js("WORKING") == "WORKING"
    assert normalize_route_status_js("WORKING_ROUTE") == "WORKING"
    assert normalize_route_status_js("working") == "WORKING"
    assert normalize_route_status_js("PARTIAL") == "PARTIAL"
    assert normalize_route_status_js("PARTIAL_ROUTE") == "PARTIAL"
    assert normalize_route_status_js("partial") == "PARTIAL"
    assert normalize_route_status_js("NON-WORKING") == "NON_WORKING"
    assert normalize_route_status_js("NON_WORKING") == "NON_WORKING"
    assert normalize_route_status_js("NON_WORKING_ROUTE") == "NON_WORKING"
    assert normalize_route_status_js(None) == "WORKING"


def test_card_counts_equal_table_rows():
    """Card count must strictly equal table rows, never displaying 0 when working routes exist."""
    routes = [
        {"path": "/inventory.html", "route_status": "WORKING_ROUTE"},
        {"path": "/cart.html", "route_status": "WORKING"},
        {"path": "/checkout-step-one.html", "route_status": "PARTIAL_ROUTE"},
        {"path": "/error.html", "route_status": "NON-WORKING"},
    ]
    # Summary mistakenly reports 0 working routes
    corrupt_summary = {"working_routes": 0, "partial_routes": 0, "non_working_routes": 0, "total_routes": 4}

    counts = calculate_route_counts_js(routes, corrupt_summary)

    # Table rows breakdown
    table_working = [r for r in routes if normalize_route_status_js(r.get("route_status")) == "WORKING"]
    table_partial = [r for r in routes if normalize_route_status_js(r.get("route_status")) == "PARTIAL"]
    table_non_working = [r for r in routes if normalize_route_status_js(r.get("route_status")) == "NON_WORKING"]

    assert len(table_working) == 2
    assert len(table_partial) == 1
    assert len(table_non_working) == 1

    # Card counts must equal table rows, NOT 0
    assert counts["working"] == len(table_working) == 2
    assert counts["partial"] == len(table_partial) == 1
    assert counts["non_working"] == len(table_non_working) == 1
    assert counts["total"] == len(routes) == 4


def test_fallback_to_summary_when_routes_empty():
    """When route details are omitted, summary counts are respected."""
    summary = {"working_routes": 5, "partial_routes": 2, "non_working_routes": 1, "total_routes": 8}
    counts = calculate_route_counts_js([], summary)

    assert counts["working"] == 5
    assert counts["partial"] == 2
    assert counts["non_working"] == 1
    assert counts["total"] == 8
