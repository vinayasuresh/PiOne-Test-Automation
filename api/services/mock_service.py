"""Pre-defined demo data returned when ``mock: true``.

Mock mode is critical for demo reliability: it bypasses Playwright entirely
and returns realistic data instantly.
"""

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


def get_mock_response(url: str) -> ScanResponse:  # noqa: ARG001 — url kept for parity
    """Return the demo dataset shaped exactly like a real scan."""
    routes = [
        RouteResult(
            path=r["path"],
            menu_name=r["menu_name"],
            purpose=r["purpose"],
            components=ComponentGroups(
                buttons=r["buttons"],
                inputs=r["inputs"],
                dropdowns=r["dropdowns"],
                tables=r["tables"],
                charts=r["charts"],
                maps=r["maps"],
            ),
            interactions=[Interaction(**i) for i in r.get("interactions", [])],
        )
        for r in _MOCK_ROUTES
    ]

    total_buttons = sum(len(r.components.buttons) for r in routes)
    total_inputs  = sum(len(r.components.inputs)  for r in routes)
    total_tables  = sum(len(r.components.tables)  for r in routes)

    return ScanResponse(
        status="success",
        routes=routes,
        summary=ScanSummary(
            total_routes=len(routes),
            total_buttons=total_buttons,
            total_inputs=total_inputs,
            total_tables=total_tables,
            scan_duration_seconds=0.04,
        ),
    )
