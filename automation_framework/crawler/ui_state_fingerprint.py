"""ui_state_fingerprint.py: Deterministic DOM state fingerprinting and selector quality scoring.

Guarantees:
- Models distinct UI states for modern SPAs beyond raw URL changes:
  - Open modals, dialogs, and overlays
  - Active tabs and sub-views
  - Expanded accordions, drawers, and panels
- Computes a deterministic `ui_state_id`
- Computes selector quality scores (0-100) based on locator resilience
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page


_STATE_INSPECTOR_SCRIPT = """() => {
    // 1. Visible dialogs / modals
    const dialogs = Array.from(document.querySelectorAll(
        "dialog[open], [role='dialog'], [role='alertdialog'], [aria-modal='true'], "
        + ".cdk-overlay-pane, .mat-dialog-container, .mat-mdc-dialog-container, .p-dialog"
    )).filter(el => {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    }).map(el => {
        return el.id || el.getAttribute('aria-label') || el.getAttribute('data-testid') || 'dialog';
    });

    // 2. Active tabs
    const activeTabs = Array.from(document.querySelectorAll(
        "[role='tab'][aria-selected='true'], [role='tab'].active, [role='tab'].mat-tab-label-active, "
        + ".nav-tabs .active, .p-tabview-nav li.p-highlight"
    )).filter(el => {
        const s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden';
    }).map(el => {
        return (el.getAttribute('aria-label') || el.innerText || el.textContent || '').trim().slice(0, 50);
    });

    // 3. Expanded collapsible panels / accordions / drawers
    const expanded = Array.from(document.querySelectorAll(
        "[aria-expanded='true'], details[open], mat-expansion-panel.mat-expanded, "
        + ".p-accordion-header.p-highlight"
    )).filter(el => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
    }).map(el => {
        return el.id || el.getAttribute('aria-controls') || (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 40);
    });

    return {
        dialogs: dialogs.sort(),
        tabs: activeTabs.sort(),
        expanded: expanded.sort()
    };
}"""


def compute_ui_state_fingerprint(page: Page, route: str) -> dict[str, Any]:
    """Compute a deterministic DOM state fingerprint for the current page."""
    parsed = urlparse(route or "")
    norm_route = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        "", "", "",
    ))

    try:
        raw_state = page.evaluate(_STATE_INSPECTOR_SCRIPT)
    except Exception:
        raw_state = {"dialogs": [], "tabs": [], "expanded": []}

    dialogs = raw_state.get("dialogs", [])
    tabs = raw_state.get("tabs", [])
    expanded = raw_state.get("expanded", [])

    is_base = not dialogs and not tabs and not expanded

    # Create deterministic state digest
    state_payload = json.dumps({
        "route": norm_route,
        "dialogs": dialogs,
        "tabs": tabs,
        "expanded": expanded,
    }, sort_keys=True)
    digest = hashlib.sha256(state_payload.encode("utf-8")).hexdigest()[:12]

    if is_base:
        ui_state_id = f"state_base_{digest[:8]}"
    else:
        ui_state_id = f"state_{digest}"

    return {
        "ui_state_id": ui_state_id,
        "route": norm_route,
        "dialogs": dialogs,
        "tabs": tabs,
        "expanded": expanded,
        "is_base_state": is_base,
    }


def compute_selector_quality_score(selector_dict: dict[str, Any] | None) -> int:
    """Calculate resilience score (0-100) for an element's selector.

    Scoring:
    - 100: Resilient test identifier (data-testid, data-test, data-cy)
    - 90: Semantic accessible name/role (aria-label, role with name)
    - 85: Unique static element ID
    - 75: Form field name or placeholder
    - 60: Scoped semantic CSS selector
    - 50: Hierarchical XPath
    - 40: Fallback / text-only selector
    """
    if not selector_dict or not isinstance(selector_dict, dict):
        return 40

    primary = selector_dict.get("primary", {})
    if not isinstance(primary, dict):
        return 40

    strategy = str(primary.get("strategy", "")).lower().strip()
    val = str(primary.get("value", "")).lower().strip()

    if strategy in {"testid", "data-testid", "test-id"}:
        return 100
    if "data-testid=" in val or "data-test=" in val or "data-cy=" in val:
        return 100
    if strategy in {"aria", "role"} or "aria-label=" in val or "role=" in val:
        return 90
    if strategy == "id" or (val.startswith("#") and not any(c.isdigit() for c in val[-4:])):
        return 85
    if strategy in {"name", "placeholder"}:
        return 75
    if strategy == "css":
        return 60
    if strategy == "xpath" or val.startswith("//") or val.startswith("/html/"):
        return 50
    return 40
