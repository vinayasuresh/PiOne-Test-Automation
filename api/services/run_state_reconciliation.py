"""Helpers for consistent run-state progress and terminal snapshot reconciliation."""

from __future__ import annotations

from typing import Any, Mapping


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def calculate_module_progress(data: Mapping[str, Any]) -> dict[str, Any]:
    selected = [str(m) for m in _as_list(data.get("selected_modules")) if str(m).strip()]
    completed = [str(m) for m in _as_list(data.get("completed_modules")) if str(m).strip()]
    pending = [m for m in selected if m not in completed]
    total = len(selected)
    done = len(completed)
    current = min(total, done + 1) if total else 0
    return {
        "current": current,
        "total": total,
        "completed_count": done,
        "pending_count": len(pending),
        "completed_modules": completed,
        "pending_modules": pending,
    }


def calculate_route_progress(data: Mapping[str, Any]) -> dict[str, Any]:
    route_progress = data.get("route_progress") if isinstance(data.get("route_progress"), Mapping) else {}
    scanned = int(route_progress.get("scanned", 0) or 0)
    pending = int(route_progress.get("pending", 0) or 0)
    total = int(route_progress.get("total", 0) or 0)

    if total <= 0:
        scan_results = data.get("scan_results") if isinstance(data.get("scan_results"), Mapping) else {}
        page_intel = scan_results.get("page_intelligence") if isinstance(scan_results.get("page_intelligence"), Mapping) else {}
        scanned = max(scanned, len(page_intel))
        total = max(total, scanned + max(0, pending))

    if total < scanned:
        total = scanned
    if pending < 0:
        pending = 0

    percentage = round((scanned * 100.0 / total), 1) if total > 0 else 0.0
    return {"scanned": scanned, "pending": pending, "total": total, "percentage": percentage}
