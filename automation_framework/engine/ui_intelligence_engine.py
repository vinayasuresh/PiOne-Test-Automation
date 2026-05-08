from typing import Any

from playwright.sync_api import Error as PlaywrightError, Locator, Page

from automation_framework.utils.logger import logger


SCHEMA_VERSION = "1.0"

MAX_ITEMS_PER_CATEGORY = 20
MAX_LABEL_LENGTH = 80

MIN_VISUAL_WIDTH = 120
MIN_VISUAL_HEIGHT = 80
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 2

HIGH_RELEVANCE = "HIGH"
MEDIUM_RELEVANCE = "MEDIUM"
ALLOWED_RELEVANCE = {HIGH_RELEVANCE, MEDIUM_RELEVANCE}

VALIDATION_CATEGORIES = {
    "tables",
    "forms",
    "charts",
    "maps",
    "analytics",
    "dialogs",
    "filters",
}

CATEGORY_ORDER = (
    "filters",
    "tables",
    "forms",
    "charts",
    "maps",
    "actions",
    "inputs",
    "navigation",
    "dialogs",
    "tabs",
    "analytics",
)

CONTENT_ROOT_SELECTOR = (
    "main, [role='main'], [class*='content' i], [class*='dashboard' i], "
    "[class*='page' i]"
)

SHELL_SELECTOR = (
    "header, aside, nav, footer, [role='navigation'], [class*='sidebar' i], "
    "[class*='navbar' i], [class*='top-nav' i]"
)

CATEGORY_RULES: dict[str, dict[str, str]] = {
    "filters": {
        "selector": (
            "form, [role='search'], [aria-label*='filter' i], [class*='filter' i], "
            "[class*='search' i], select, [role='combobox']"
        ),
        "interaction_type": "filter",
        "relevance": HIGH_RELEVANCE,
    },
    "tables": {
        # Structural only: tag, role="table", role="grid". Class-based matches are
        # rejected to avoid treating text blobs / wrappers as tables.
        "selector": "table, [role='table'], [role='grid']",
        "interaction_type": "validate_table",
        "relevance": HIGH_RELEVANCE,
    },
    "forms": {
        "selector": "form, [role='form']",
        "interaction_type": "submit_form",
        "relevance": HIGH_RELEVANCE,
    },
    "charts": {
        "selector": "[aria-label*='chart' i], [class*='chart' i], [class*='graph' i]",
        "interaction_type": "validate_visualization",
        "relevance": HIGH_RELEVANCE,
    },
    "maps": {
        "selector": "[aria-label*='map' i], [class*='mapbox' i], [class*='leaflet' i], [id*='map' i]",
        "interaction_type": "validate_map",
        "relevance": HIGH_RELEVANCE,
    },
    "actions": {
        "selector": "button, [role='button'], input[type='submit'], input[type='button']",
        "interaction_type": "click",
        "relevance": HIGH_RELEVANCE,
    },
    "inputs": {
        # Bare inputs/textareas not already inside a form/filter container.
        # Hidden inputs are excluded explicitly; inFilterOrForm is filtered
        # in the main loop so we never double-count fields owned by a form.
        "selector": "input:not([type='hidden']):not([type='submit']):not([type='button']), textarea",
        "interaction_type": "input",
        "relevance": MEDIUM_RELEVANCE,
    },
    "navigation": {
        "selector": "main a[href], [role='main'] a[href], main [role='link'], [role='main'] [role='link']",
        "interaction_type": "navigate",
        "relevance": MEDIUM_RELEVANCE,
    },
    "dialogs": {
        "selector": "dialog, [role='dialog'], [role='alertdialog'], [aria-modal='true']",
        "interaction_type": "validate_dialog",
        "relevance": MEDIUM_RELEVANCE,
    },
    "tabs": {
        "selector": "[role='tab'], [role='tablist'], [class*='tab' i]",
        "interaction_type": "switch_tab",
        "relevance": MEDIUM_RELEVANCE,
    },
    "analytics": {
        "selector": "[class*='analytics' i], [class*='metric' i], [class*='kpi' i], [class*='stat' i]",
        "interaction_type": "validate_metric",
        "relevance": HIGH_RELEVANCE,
    },
}

BUSINESS_ACTION_WORDS = (
    "add", "apply", "create", "delete", "download", "edit", "export", "filter",
    "generate", "open", "refresh", "reset", "save", "search", "submit", "upload", "view",
)

# Labels treated as map UI noise (legend text, scale bar, generic title).
MAP_LABEL_DENYLIST = {"map", "maps", "satellite", "terrain"}
MAP_SCALE_PATTERN = (" km", " mi", " m ", "meters", "kilometers")

# Selector used to detect when an element is already inside a classified
# filter/form region (prevents the same control showing up as both filter and action).
FILTER_OR_FORM_SELECTOR = (
    "form, [role='form'], [role='search'], "
    "[aria-label*='filter' i], [class*='filter' i], [class*='search' i]"
)

# ---------------------------------------------------------------------------
# Batch JS evaluation script
#
# evaluate_all() runs this script once per category against the matched NodeList,
# returning a plain list of dicts.  This collapses what was previously 8–12
# separate Playwright round-trips per element down to 1 call per category.
# ---------------------------------------------------------------------------

_ELEMENT_DATA_SCRIPT = """
(elements) => {
    /* Walk up the ancestor chain and test each node — avoids CSS-selector
       case-insensitive quirks inside closest() across browser engines. */
    function isInShell(el) {
        const SHELL_TAGS = new Set(['header', 'aside', 'nav', 'footer']);
        let c = el.parentElement;
        while (c && c !== document.body) {
            const tag = c.tagName.toLowerCase();
            if (SHELL_TAGS.has(tag)) return true;
            if ((c.getAttribute('role') || '') === 'navigation') return true;
            const cls = (c.className || '').toLowerCase();
            if (cls.includes('sidebar') || cls.includes('navbar') || cls.includes('top-nav')) return true;
            c = c.parentElement;
        }
        return false;
    }

    function isInFilterOrForm(el) {
        let c = el.parentElement;
        while (c && c !== document.body) {
            const tag = c.tagName.toLowerCase();
            const role = (c.getAttribute('role') || '');
            if (tag === 'form' || role === 'form' || role === 'search') return true;
            if ((c.getAttribute('aria-label') || '').toLowerCase().includes('filter')) return true;
            const cls = (c.className || '').toLowerCase();
            if (cls.includes('filter') || cls.includes('search')) return true;
            c = c.parentElement;
        }
        return false;
    }

    function getContainerKey(el) {
        let c = el;
        while (c && c !== document.body) {
            const cls = (c.className || '').toLowerCase();
            const id  = (c.id || '').toLowerCase();
            if (cls.includes('map') || cls.includes('mapbox') || cls.includes('leaflet') ||
                cls.includes('chart') || cls.includes('graph') || id.includes('map')) {
                return c.id || c.getAttribute('data-testid') || c.className || '';
            }
            c = c.parentElement;
        }
        return '';
    }

    return elements.map(el => {
        const style = window.getComputedStyle(el);
        const box   = el.getBoundingClientRect();
        const isVisible = (
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            parseFloat(style.opacity || '1') > 0 &&
            (box.width > 0 || box.height > 0)
        );

        /* Table metrics */
        const rows        = el.querySelectorAll("tr, [role='row']");
        const firstRow    = rows[0] || null;
        const firstRowCols = firstRow
            ? firstRow.querySelectorAll("th, td, [role='columnheader'], [role='cell'], [role='gridcell']").length
            : 0;

        /* Form control count */
        const controlCount = el.querySelectorAll(
            "input:not([type='hidden']), textarea, select, button, [role='combobox']"
        ).length;

        return {
            ariaLabel:    el.getAttribute('aria-label') || '',
            title:        el.getAttribute('title') || '',
            id:           el.id || '',
            testId:       el.getAttribute('data-testid') || '',
            placeholder:  el.getAttribute('placeholder') || '',
            name:         el.getAttribute('name') || '',
            role:         el.getAttribute('role') || '',
            tagName:      el.tagName.toLowerCase(),
            text:         (el.innerText || '').trim().slice(0, 120),
            isVisible,
            inShell:         isInShell(el),
            inFilterOrForm:  isInFilterOrForm(el),
            width:        box.width,
            height:       box.height,
            rowCount:     rows.length,
            firstRowCols,
            controlCount,
            containerKey: getContainerKey(el),
        };
    });
}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_ui_intelligence(
    page: Page,
    feature_name: str = "current_page",
    menu_name: str | None = None,
    global_seen: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Produce a normalized Route document for the current page.

    Output shape (see docs/output_schema.md):
        { route, menu_name, purpose, sections, automation_targets }

    `global_seen` is an optional cross-route registry keyed by
    (label_lower, primary_selector). Components already in it are skipped so
    the same button/filter does not appear in every route.
    """
    logger.info(f"Building UI intelligence for route: {page.url}")

    if global_seen is None:
        global_seen = set()

    automation_targets: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, str, str]] = set()
    seen_visual_containers: set[str] = set()
    content_root = _content_root(page)

    # Per-category counts surfaced in logs so a route's detection profile is
    # immediately visible (e.g. "Detected 5 buttons on /dashboard").
    category_counts: dict[str, int] = {}

    for category in CATEGORY_ORDER:
        rule = CATEGORY_RULES[category]

        if rule["relevance"] not in ALLOWED_RELEVANCE:
            continue  # LOW relevance is dropped entirely

        # One JS round-trip per category instead of N calls per element.
        try:
            elements_data: list[dict] = content_root.locator(rule["selector"]).evaluate_all(
                _ELEMENT_DATA_SCRIPT
            )
        except Exception:
            logger.debug(f"evaluate_all failed for category '{category}', skipping")
            continue

        kept = 0

        for data in elements_data:
            if not _is_meaningful_from_data(data, category):
                continue

            # Prevent filter controls from being re-classified as actions.
            if category == "actions" and data.get("inFilterOrForm"):
                continue

            # Bare inputs only — fields inside a form/filter are already
            # represented via that container's controlCount.
            if category == "inputs" and data.get("inFilterOrForm"):
                continue

            # Map/chart: dedupe by visual container so markers/overlays never
            # create separate components.
            if category in {"maps", "charts"}:
                container_key = (data.get("containerKey") or "").strip()
                if container_key and container_key in seen_visual_containers:
                    continue
                if container_key:
                    seen_visual_containers.add(container_key)

            target = _build_target_from_data(data, category, rule)
            if target is None:
                continue

            label_lower = target["label"].lower()
            primary = target["selector"]["primary"]["value"]

            # Per-route dedup: same category+label+selector cannot repeat.
            local_signature = (target["category"], label_lower, primary)
            if local_signature in seen_signatures:
                continue

            # Global dedup: same component must not appear across routes.
            global_key = (label_lower, primary)
            if global_key in global_seen:
                continue

            seen_signatures.add(local_signature)
            global_seen.add(global_key)
            automation_targets.append(target)
            kept += 1

            if kept >= MAX_ITEMS_PER_CATEGORY:
                break

        if kept:
            category_counts[category] = kept

    sections = _build_sections(automation_targets)

    # Surface per-category detection counts so each route's profile is logged.
    route_label = page.url
    for category in ("actions", "inputs", "filters", "tables", "charts", "maps"):
        n = category_counts.get(category, 0)
        if n:
            human = {"actions": "buttons", "inputs": "inputs", "filters": "filters",
                     "tables": "tables", "charts": "charts", "maps": "maps"}[category]
            logger.info(f"Detected {n} {human} on {route_label}")

    return {
        "route": page.url,
        "menu_name": menu_name or feature_name,
        "purpose": _infer_page_purpose(page, feature_name),
        "sections": sections,
        "automation_targets": automation_targets,
    }


def build_component_registry(
    page_models: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Flatten components across routes, grouped by category."""
    registry: dict[str, list[dict[str, Any]]] = {}

    for route, page_model in page_models.items():
        for target in page_model.get("automation_targets", []):
            category = target["category"]
            registry.setdefault(category, []).append(
                {
                    "route": route,
                    "label": target["label"],
                    "purpose": target["purpose"],
                    "interaction_type": target["interaction_type"],
                    "relevance": target["relevance"],
                    "selector": target["selector"],
                }
            )

    return registry


# ---------------------------------------------------------------------------
# Data-dict helpers — work from pre-fetched JS data, no extra round-trips
# ---------------------------------------------------------------------------


def _element_label_from_data(data: dict) -> str:
    for key in ("ariaLabel", "title", "placeholder", "name"):
        value = (data.get(key) or "").strip()
        if value:
            return _truncate(value)
    text = (data.get("text") or "").strip()
    if text:
        return _truncate(text)
    return ""


def _implicit_role_from_data(data: dict, category: str) -> str:
    tag = data.get("tagName", "")
    if tag == "button" or category == "actions":
        return "button"
    if tag == "a" or category == "navigation":
        return "link"
    if tag == "table" or category == "tables":
        return "table"
    if category == "tabs":
        return "tab"
    return ""


def _build_selector_from_data(data: dict, category: str, label: str) -> dict | None:
    candidates: list[tuple[str, str]] = []

    test_id = data.get("testId", "")
    if test_id:
        candidates.append(("testid", f'[data-testid="{test_id}"]'))

    aria_label = data.get("ariaLabel", "")
    if aria_label:
        candidates.append(("aria", f'[aria-label="{aria_label}"]'))

    role = data.get("role", "") or _implicit_role_from_data(data, category)
    if role and label:
        candidates.append(("role", f'role={role}[name="{label}"]'))

    element_id = data.get("id", "")
    if element_id and not _looks_dynamic(element_id):
        candidates.append(("id", f"#{element_id}"))

    placeholder = data.get("placeholder", "")
    if placeholder:
        candidates.append(("aria", f'[placeholder="{placeholder}"]'))

    name_attr = data.get("name", "")
    if name_attr:
        tag = data.get("tagName", "") or "*"
        candidates.append(("id", f'{tag}[name="{name_attr}"]'))

    if not candidates:
        return None

    primary_strategy, primary_value = candidates[0]
    fallback = [value for _, value in candidates[1:]]
    return {
        "primary": {"strategy": primary_strategy, "value": primary_value},
        "fallback": fallback,
    }


def _is_meaningful_from_data(data: dict, category: str) -> bool:
    if not data.get("isVisible"):
        return False
    if data.get("inShell"):
        return False

    label = _element_label_from_data(data)

    if category in {"actions", "navigation", "tabs"} and not label:
        return False
    if category == "actions" and not _is_business_action(label):
        return False
    if category == "inputs" and not label:
        # Require a label/placeholder/name so anonymous text boxes are dropped.
        return False
    if category in {"filters", "forms"}:
        if data.get("controlCount", 0) == 0 and not label:
            return False
    if category == "tables":
        if data.get("rowCount", 0) < MIN_TABLE_ROWS:
            return False
        if data.get("firstRowCols", 0) < MIN_TABLE_COLUMNS:
            return False
    if category in {"charts", "maps"}:
        if data.get("width", 0) < MIN_VISUAL_WIDTH or data.get("height", 0) < MIN_VISUAL_HEIGHT:
            return False
    if category == "maps" and _is_map_label_noise(label):
        return False

    return True


def _build_target_from_data(data: dict, category: str, rule: dict) -> dict | None:
    label = _element_label_from_data(data)
    selector = _build_selector_from_data(data, category, label)
    if not selector:
        return None

    return {
        "id": f"{category}::{(label or selector['primary']['value'])[:MAX_LABEL_LENGTH]}",
        "category": category,
        "label": label,
        "purpose": _purpose_for(category, label),
        "interaction_type": rule["interaction_type"],
        "relevance": rule["relevance"],
        "selector": selector,
        "is_validation_target": category in VALIDATION_CATEGORIES,
    }


# ---------------------------------------------------------------------------
# Target construction (Locator-based — kept for potential future use)
# ---------------------------------------------------------------------------


def _build_target(
    element: Locator,
    category: str,
    rule: dict[str, str],
) -> dict[str, Any] | None:
    label = _element_label(element)
    selector = _build_selector(element, category, label)

    if not selector:
        return None

    return {
        "id": f"{category}::{(label or selector['primary']['value'])[:MAX_LABEL_LENGTH]}",
        "category": category,
        "label": label,
        "purpose": _purpose_for(category, label),
        "interaction_type": rule["interaction_type"],
        "relevance": rule["relevance"],
        "selector": selector,
        "is_validation_target": category in VALIDATION_CATEGORIES,
    }


def _build_selector(
    element: Locator,
    category: str,
    label: str,
) -> dict[str, Any] | None:
    """Return {primary: {strategy, value}, fallback: [...]} or None."""
    candidates: list[tuple[str, str]] = []

    test_id = _attr(element, "data-testid")
    if test_id:
        candidates.append(("testid", f'[data-testid="{test_id}"]'))

    aria_label = _attr(element, "aria-label")
    if aria_label:
        candidates.append(("aria", f'[aria-label="{aria_label}"]'))

    role = _attr(element, "role") or _implicit_role(element, category)
    if role and label:
        candidates.append(("role", f'role={role}[name="{label}"]'))

    element_id = _attr(element, "id")
    if element_id and not _looks_dynamic(element_id):
        candidates.append(("id", f"#{element_id}"))

    placeholder = _attr(element, "placeholder")
    if placeholder:
        candidates.append(("aria", f'[placeholder="{placeholder}"]'))

    name_attr = _attr(element, "name")
    if name_attr:
        tag = _tag_name(element) or "*"
        candidates.append(("id", f'{tag}[name="{name_attr}"]'))

    if not candidates:
        return None

    primary_strategy, primary_value = candidates[0]
    fallback = [value for _, value in candidates[1:]]

    return {
        "primary": {"strategy": primary_strategy, "value": primary_value},
        "fallback": fallback,
    }


def _build_sections(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for target in targets:
        counts[target["category"]] = counts.get(target["category"], 0) + 1

    return [
        {"name": category, "count": counts[category]}
        for category in CATEGORY_ORDER
        if counts.get(category, 0) > 0
    ]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _is_meaningful(element: Locator, category: str) -> bool:
    if not _visible(element):
        return False

    if _inside_shell(element):
        return False

    label = _element_label(element)

    if category in {"actions", "navigation", "tabs"} and not label:
        return False

    if category == "actions" and not _is_business_action(label):
        return False

    if category in {"filters", "forms"}:
        if _count_controls(element) == 0 and label == "":
            return False

    if category == "tables" and not _is_valid_table(element):
        return False

    if category in {"charts", "maps"} and not _has_visual_size(element):
        return False

    if category == "maps" and _is_map_label_noise(label):
        return False

    return True


def _is_map_label_noise(label: str) -> bool:
    """Reject labels like 'Map', '300 km', '5 mi' that are scale/legend text."""
    if not label:
        return False
    normalized = label.lower().strip()
    if normalized in MAP_LABEL_DENYLIST:
        return True
    return any(token in normalized for token in MAP_SCALE_PATTERN)


def _inside_filter_or_form(element: Locator) -> bool:
    """True when the element is inside a filter/form region."""
    try:
        return bool(
            element.evaluate(
                "(el, selector) => !!el.closest(selector)",
                FILTER_OR_FORM_SELECTOR,
            )
        )
    except PlaywrightError:
        return False


def _is_valid_table(element: Locator) -> bool:
    """A real table must have multiple rows AND multiple columns.

    Rejects text blobs and class-name false positives that match `[class*='table']`.
    """
    try:
        row_count = element.locator("tr, [role='row']").count()
        if row_count < MIN_TABLE_ROWS:
            return False

        first_row = element.locator("tr, [role='row']").first
        column_count = first_row.locator(
            "th, td, [role='columnheader'], [role='cell'], [role='gridcell']"
        ).count()
        return column_count >= MIN_TABLE_COLUMNS
    except PlaywrightError:
        return False


def _has_visual_size(element: Locator) -> bool:
    """Filter out icon-sized SVG/canvas/map containers."""
    try:
        box = element.bounding_box()
    except PlaywrightError:
        return False

    if not box:
        return False

    return box["width"] >= MIN_VISUAL_WIDTH and box["height"] >= MIN_VISUAL_HEIGHT


def _visual_container_id(element: Locator) -> str:
    """Identify the parent container for maps/charts so duplicates are merged."""
    try:
        return str(
            element.evaluate(
                """
                (el) => {
                    const container = el.closest(
                        "[class*='map' i], [class*='mapbox' i], [class*='leaflet' i], "
                        + "[class*='chart' i], [class*='graph' i], [id*='map' i]"
                    ) || el;
                    return container.id
                        || container.getAttribute('data-testid')
                        || container.className
                        || '';
                }
                """
            )
        )
    except PlaywrightError:
        return ""


def _is_business_action(label: str) -> bool:
    normalized = label.lower()
    return any(word in normalized for word in BUSINESS_ACTION_WORDS)


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------


def _content_root(page: Page) -> Locator:
    # Fast path: grab the first match and verify it is visible.
    # Avoids counting all matches and iterating when one good root exists.
    root = page.locator(CONTENT_ROOT_SELECTOR).first
    try:
        if root.is_visible():
            return root
    except Exception:
        pass
    return page.locator("body")


def _inside_shell(element: Locator) -> bool:
    try:
        return bool(
            element.evaluate(
                "(element, selector) => !!element.closest(selector)",
                SHELL_SELECTOR,
            )
        )
    except PlaywrightError:
        return False


def _count_controls(element: Locator) -> int:
    try:
        return element.locator(
            "input:not([type='hidden']), textarea, select, button, [role='combobox']"
        ).count()
    except PlaywrightError:
        return 0


def _implicit_role(element: Locator, category: str) -> str:
    tag = _tag_name(element)
    if tag == "button" or category == "actions":
        return "button"
    if tag == "a" or category == "navigation":
        return "link"
    if tag == "table" or category == "tables":
        return "table"
    if category == "tabs":
        return "tab"
    return ""


def _element_label(element: Locator) -> str:
    for attribute in ("aria-label", "title", "placeholder", "name"):
        value = _attr(element, attribute)
        if value:
            return _truncate(value)

    text = _text(element)
    if text:
        return _truncate(text)

    return ""


def _purpose_for(category: str, label: str) -> str:
    readable = label or category.replace("_", " ")
    return {
        "filters": f"Filter data by {readable}",
        "tables": f"Validate tabular data for {readable}",
        "forms": f"Complete form workflow for {readable}",
        "charts": f"Validate chart or visualization for {readable}",
        "maps": f"Validate map behavior for {readable}",
        "actions": f"Execute action: {readable}",
        "navigation": f"Navigate to {readable}",
        "dialogs": f"Validate dialog: {readable}",
        "tabs": f"Switch tab: {readable}",
        "analytics": f"Validate analytics metric: {readable}",
    }.get(category, readable)


def _infer_page_purpose(page: Page, feature_name: str) -> str:
    title = ""
    try:
        title = page.title()
    except PlaywrightError:
        pass

    source = feature_name if feature_name != "current_page" else title or page.url.rsplit("/", 1)[-1]
    source = source.replace("-", " ").replace("_", " ").strip()
    return f"Understand and validate {source}" if source else "Understand and validate page behavior"


def _visible(element: Locator) -> bool:
    try:
        return element.is_visible()
    except PlaywrightError:
        return False


def _attr(element: Locator, name: str) -> str:
    try:
        return _clean(element.get_attribute(name) or "")
    except PlaywrightError:
        return ""


def _text(element: Locator) -> str:
    try:
        return _clean(element.inner_text(timeout=500))
    except PlaywrightError:
        return ""


def _tag_name(element: Locator) -> str:
    try:
        return str(element.evaluate("element => element.tagName.toLowerCase()"))
    except PlaywrightError:
        return ""


def _looks_dynamic(value: str) -> bool:
    digit_count = sum(character.isdigit() for character in value)
    return digit_count >= 4 or len(value) > 60


def _truncate(value: str) -> str:
    cleaned = _clean(value)
    return cleaned[:MAX_LABEL_LENGTH]


def _clean(value: str) -> str:
    return " ".join(value.split())
