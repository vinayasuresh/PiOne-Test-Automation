"""Pre-defined demo data returned when ``mock: true``.

Mock mode is critical for demo reliability: it bypasses Playwright entirely
and returns realistic data instantly.
"""

import re

from api.models.scan_models import (
    ComponentGroups,
    Interaction,
    RouteResult,
    ScanResponse,
    ScanSummary,
)


_MOCK_ROUTES: list[dict] = [
    {
        "path": "/dashboard",
        "menu_name": "Dashboard",
        "purpose": "Understand and validate Dashboard",
        "buttons":   ["Export Report", "Refresh Data", "Add Widget", "View Details"],
        "inputs":    ["Search", "Date Range"],
        "dropdowns": ["Region", "Department"],
        "tables":    ["Recent Activity"],
        "charts":    ["Revenue Trend", "User Growth"],
        "maps":      [],
        "interactions": [
            {"type": "click",  "component": "button",   "label": "Export Report", "selector": "button#export",   "tag": "button"},
            {"type": "select", "component": "dropdown", "label": "Region",        "selector": "select[name='region']", "tag": "select"},
        ],
    },
    {
        "path": "/users",
        "menu_name": "User Management",
        "purpose": "Understand and validate User Management",
        "buttons":   ["Create User", "Edit User", "Delete User", "Export CSV", "Import Users"],
        "inputs":    ["Search Users"],
        "dropdowns": ["Role Filter", "Status Filter"],
        "tables":    ["Users List"],
        "charts":    [],
        "maps":      [],
        "interactions": [
            {"type": "click", "component": "button", "label": "Create User",  "selector": "button[data-testid='create-user']", "tag": "button"},
            {"type": "input", "component": "input",  "label": "Search Users", "selector": "input[name='q']",                  "tag": "input"},
        ],
    },
    {
        "path": "/reports",
        "menu_name": "Reports",
        "purpose": "Understand and validate Reports",
        "buttons":   ["Generate Report", "Download PDF", "Schedule Report", "Share Report"],
        "inputs":    ["Start Date", "End Date"],
        "dropdowns": ["Report Type", "Format"],
        "tables":    ["Saved Reports", "Scheduled Reports"],
        "charts":    ["Usage by Period"],
        "maps":      [],
    },
    {
        "path": "/settings",
        "menu_name": "Settings",
        "purpose": "Understand and validate Settings",
        "buttons":   ["Save Settings", "Reset to Default", "Cancel"],
        "inputs":    ["Email Notifications"],
        "dropdowns": ["Timezone", "Language"],
        "tables":    [],
        "charts":    [],
        "maps":      [],
    },
    {
        "path": "/analytics",
        "menu_name": "Analytics",
        "purpose": "Understand and validate Analytics",
        "buttons":   ["Apply Filters", "Export Data", "Compare Periods"],
        "inputs":    [],
        "dropdowns": ["Metric Type", "Time Period", "Segment By"],
        "tables":    ["Top Sources"],
        "charts":    ["Conversion Funnel", "Engagement"],
        "maps":      ["User Geo Distribution"],
    },
]


def _mock_target(path: str, category: str, label: str) -> dict:
    """Build deterministic evidence with the same shape as a live crawl.

    Mock mode exists to validate the workflow and must not teach users that a
    label-only component list is equivalent to a grounded element registry.
    These selectors are explicitly synthetic but structurally identical to
    live records, making downstream UI, export, and planning paths testable.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "control"
    type_by_category = {
        "actions": "button",
        "inputs": "input",
        "dropdowns": "dropdown",
        "tables": "table",
        "charts": "chart",
        "maps": "map",
    }
    type_name = type_by_category[category]
    interaction = {
        "button": "click",
        "input": "input",
        "dropdown": "select",
        "table": "validate_table",
    }.get(type_name, "observe")
    manual_review = any(term in label.lower() for term in ("delete", "reset", "create user"))
    selector = f'[data-testid="mock-{slug}"]'
    identity = f"{path}|mock-{slug}|{type_name}"
    return {
        "id": identity,
        "element_id": identity,
        "element_identity": identity,
        "route": path,
        "category": category,
        "type": type_name,
        "label": label,
        "purpose": f"Mock {type_name} evidence for {label}",
        "interaction_type": interaction,
        "selector": {"primary": {"strategy": "testid", "value": selector}, "fallback": []},
        "element_xpath": f'//*[@data-testid="mock-{slug}"]',
        "element_role": {"button": "button", "input": "textbox", "dropdown": "combobox"}.get(type_name, type_name),
        "discovery_mechanism": "mock_fixture",
        "is_clickable": type_name == "button",
        "safety_classification": "manual_review" if manual_review else "safe_observation",
        "is_mock": True,
    }


def _mock_targets(route: dict) -> list[dict]:
    targets: list[dict] = []
    for source_key, category in (
        ("buttons", "actions"),
        ("inputs", "inputs"),
        ("dropdowns", "dropdowns"),
        ("tables", "tables"),
        ("charts", "charts"),
        ("maps", "maps"),
    ):
        targets.extend(_mock_target(route["path"], category, label) for label in route[source_key])
    return targets


def get_mock_response(url: str) -> ScanResponse:  # noqa: ARG001 — url kept for parity
    """Return the demo dataset shaped exactly like a real scan."""
    routes = []
    for route_data in _MOCK_ROUTES:
        targets = _mock_targets(route_data)
        routes.append(RouteResult(
            path=route_data["path"],
            url=route_data["path"],
            menu_name=route_data["menu_name"],
            purpose=route_data["purpose"],
            components=ComponentGroups(
                buttons=route_data["buttons"],
                inputs=route_data["inputs"],
                dropdowns=route_data["dropdowns"],
                tables=route_data["tables"],
                charts=route_data["charts"],
                maps=route_data["maps"],
            ),
            interactions=[Interaction(**i) for i in route_data.get("interactions", [])],
            automation_targets=targets,
            total_automation_targets=len(targets),
        ))

    total_buttons = sum(len(r.components.buttons) for r in routes)
    total_inputs  = sum(len(r.components.inputs)  for r in routes)
    total_tables  = sum(len(r.components.tables)  for r in routes)
    total_targets = sum(len(r.automation_targets) for r in routes)

    return ScanResponse(
        status="success",
        routes=routes,
        summary=ScanSummary(
            total_routes=len(routes),
            total_buttons=total_buttons,
            total_inputs=total_inputs,
            total_tables=total_tables,
            total_automation_targets=total_targets,
            scan_duration_seconds=0.04,
        ),
    )
