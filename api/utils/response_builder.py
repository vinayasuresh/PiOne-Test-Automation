"""Transform the raw CrawlerEngine output into a clean ScanResponse."""

from urllib.parse import urlparse

from api.models.scan_models import (
    ComponentGroups,
    Interaction,
    RouteResult,
    ScanResponse,
    ScanSummary,
)


# Cap each list so the response stays compact and readable in the UI.
_MAX_LIST_SIZE = 25


def build_scan_response(
    crawl_results: dict,
    duration: float,
    status: str = "success",
    message: str | None = None,
) -> ScanResponse:
    """Convert ``CrawlerEngine.crawl()`` output into a ScanResponse.

    Parameters
    ----------
    crawl_results : dict
        Raw output from the crawler — may be partial.
    duration : float
        Wall-clock duration of the scan in seconds.
    status : str
        ``"success"``, ``"partial_success"``, ``"timeout"``, or ``"error"``.
    message : str | None
        Optional human-readable note (e.g. why a partial result happened).
    """
    page_intelligence: dict = crawl_results.get("page_intelligence", {}) or {}

    routes: list[RouteResult] = []
    total_buttons = 0
    total_inputs  = 0
    total_tables  = 0

    for route_url, route_doc in page_intelligence.items():
        route = _build_route(route_url, route_doc)
        routes.append(route)
        total_buttons += len(route.components.buttons)
        total_inputs  += len(route.components.inputs)
        total_tables  += len(route.components.tables)

    return ScanResponse(
        status=status,
        message=message,
        routes=routes,
        summary=ScanSummary(
            total_routes=len(routes),
            total_buttons=total_buttons,
            total_inputs=total_inputs,
            total_tables=total_tables,
            scan_duration_seconds=round(duration, 2),
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_route(route_url: str, route_doc: dict) -> RouteResult:
    """Convert one page-intelligence entry into a structured RouteResult."""
    targets: list[dict] = route_doc.get("automation_targets", []) or []

    # Group every target into its UI category, preserving order of detection.
    grouped: dict[str, list[str]] = {
        "buttons":   [],
        "inputs":    [],
        "dropdowns": [],
        "tables":    [],
        "charts":    [],
        "maps":      [],
    }

    for target in targets:
        category = target.get("category", "")
        label    = (target.get("label") or "").strip()
        if not label:
            continue

        bucket = _category_to_bucket(category, target)
        if bucket is None:
            continue

        if label not in grouped[bucket]:
            grouped[bucket].append(label)

    # Truncate each list so the response stays compact.
    for key in grouped:
        grouped[key] = grouped[key][:_MAX_LIST_SIZE]

    parsed = urlparse(route_url)
    path   = parsed.path or "/"

    # Interactive learning: forward any captured user interactions.
    raw_interactions = route_doc.get("interactions") or []
    interactions: list[Interaction] = []
    for entry in raw_interactions[:_MAX_LIST_SIZE]:
        try:
            interactions.append(Interaction(**entry))
        except Exception:
            continue

    return RouteResult(
        path=path,
        menu_name=route_doc.get("menu_name", "") or "",
        purpose=route_doc.get("purpose", "") or "",
        framework=route_doc.get("framework", "") or "",
        components=ComponentGroups(**grouped),
        interactions=interactions,
        automation_targets=targets[:_MAX_LIST_SIZE],
    )


def _category_to_bucket(category: str, target: dict) -> str | None:
    """Map a crawler ``category`` to one of our UI buckets.

    Returns ``None`` when the target is not relevant for the UI response
    (e.g. nested filters, meta navigation).
    """
    if category == "actions":
        return "buttons"

    if category == "inputs":
        return "inputs"

    if category == "dropdowns":
        return "dropdowns"

    if category == "interactive":
        framework_type = (target.get("framework_type") or "").lower()
        if "select" in framework_type or "combobox" in framework_type:
            return "dropdowns"
        return "buttons"

    if category == "tables":
        return "tables"
    if category == "charts":
        return "charts"
    if category == "maps":
        return "maps"

    if category in {"filters", "forms"}:
        # Treat <select>/combobox-like controls as dropdowns; everything else
        # as plain inputs. The crawler does not always tag interaction_type
        # explicitly, so we fall back to the label/strategy heuristic.
        interaction = target.get("interaction_type", "")
        primary     = (target.get("selector") or {}).get("primary", {})
        value       = (primary.get("value") or "").lower()

        if "combobox" in value or "select" in value or interaction == "select":
            return "dropdowns"
        return "inputs"

    # navigation / dialogs / tabs / analytics: not part of the UI response.
    return None
