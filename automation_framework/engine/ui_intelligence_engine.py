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
    "dropdowns",
}

CATEGORY_ORDER = (
    "filters",
    "tables",
    "forms",
    "dropdowns",
    "charts",
    "maps",
    "actions",
    "inputs",
    "interactive",
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
            "[class*='search' i], select, [role='combobox'], mat-select, "
            "[aria-haspopup='listbox'], div[aria-haspopup='listbox']"
        ),
        "interaction_type": "filter",
        "relevance": HIGH_RELEVANCE,
    },
    "tables": {
        # Structural only: tag, role="table", role="grid". Class-based matches are
        # rejected to avoid treating text blobs / wrappers as tables.
        "selector": "table, [role='table'], [role='grid'], mat-table, cdk-table",
        "interaction_type": "validate_table",
        "relevance": HIGH_RELEVANCE,
    },
    "forms": {
        "selector": "form, [role='form']",
        "interaction_type": "submit_form",
        "relevance": HIGH_RELEVANCE,
    },
    "dropdowns": {
        "selector": (
            "select, [role='combobox'], mat-select, [aria-haspopup='listbox'], "
            "div[aria-haspopup='listbox'], [class*='select' i], [class*='dropdown' i]"
        ),
        "interaction_type": "select",
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
        "selector": (
            "button, [role='button'], input[type='submit'], input[type='button'], "
            "div[click], span[click], [onclick], [mat-button], [mat-raised-button], "
            "[mat-flat-button], [mat-stroked-button], [mat-icon-button], mat-button, "
            "[class*='mat-button' i], [class*='mdc-button' i]"
        ),
        "interaction_type": "click",
        "relevance": HIGH_RELEVANCE,
    },
    "inputs": {
        # Bare inputs/textareas not already inside a form/filter container.
        # Hidden inputs are excluded explicitly; inFilterOrForm is filtered
        # in the main loop so we never double-count fields owned by a form.
        "selector": (
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "textarea, [contenteditable='true'], [role='textbox'], [matInput], "
            "[matinput], mat-input, mat-form-field input, mat-form-field textarea"
        ),
        "interaction_type": "input",
        "relevance": MEDIUM_RELEVANCE,
    },
    "interactive": {
        "selector": (
            "[tabindex]:not([tabindex='-1']), [onclick], [style*='cursor'], "
            "[class*='button' i], [class*='btn' i], [class*='click' i], "
            "[class*='select' i], [class*='dropdown' i], app-root *, [ng-version] *"
        ),
        "interaction_type": "interact",
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
    "generate", "login", "log in", "open", "refresh", "reset", "save", "search",
    "sign in", "submit", "upload", "view",
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

    function getEventListenerCount(el) {
        try {
            if (typeof getEventListeners === 'function') {
                const listeners = getEventListeners(el) || {};
                return Object.keys(listeners).reduce((total, key) => total + (listeners[key] || []).length, 0);
            }
        } catch (err) {}
        return el.onclick ? 1 : 0;
    }

    function cssEscape(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
        return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
    }

    function generatedSelector(el) {
        if (el.getAttribute('data-testid')) return `[data-testid="${el.getAttribute('data-testid')}"]`;
        if (el.getAttribute('aria-label')) return `${el.tagName.toLowerCase()}[aria-label="${el.getAttribute('aria-label')}"]`;
        if (el.id && !/\\d{4,}/.test(el.id)) return `#${cssEscape(el.id)}`;
        const tag = el.tagName.toLowerCase();
        const name = el.getAttribute('name');
        if (name) return `${tag}[name="${name}"]`;
        let current = el;
        const parts = [];
        while (current && current.nodeType === 1 && current !== document.body && parts.length < 4) {
            const currentTag = current.tagName.toLowerCase();
            const parent = current.parentElement;
            if (!parent) break;
            const sameTagSiblings = Array.from(parent.children).filter(child => child.tagName === current.tagName);
            const index = sameTagSiblings.indexOf(current) + 1;
            parts.unshift(sameTagSiblings.length > 1 ? `${currentTag}:nth-of-type(${index})` : currentTag);
            current = parent;
        }
        return parts.length ? parts.join(' > ') : tag;
    }

    function classifyElement(el, style, eventListenerCount) {
        const tag = el.tagName.toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        const text = (el.innerText || el.value || '').trim();
        const lowerText = text.toLowerCase();
        const cls = String(el.className || '').toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const ariaHasPopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
        const frameworkType = tag.startsWith('mat-') || tag.startsWith('app-') || tag.startsWith('ng-') || tag.startsWith('cdk-')
            ? tag
            : (
                el.hasAttribute('matInput') || el.hasAttribute('matinput') ? 'mat-input'
                : el.hasAttribute('mat-button') || el.hasAttribute('mat-raised-button') || el.hasAttribute('mat-flat-button')
                    || el.hasAttribute('mat-stroked-button') || el.hasAttribute('mat-icon-button') ? 'mat-button'
                : cls.includes('mat-mdc-select') || cls.includes('mat-select') ? 'mat-select'
                : cls.includes('mat-mdc-button') || cls.includes('mat-button') || cls.includes('mdc-button') ? 'mat-button'
                : 'native'
            );
        const isAngularComponent = frameworkType !== 'native' || tag.startsWith('app-') || tag.startsWith('ng-');
        const clickable = !el.disabled && (
            style.cursor === 'pointer' ||
            eventListenerCount > 0 ||
            el.hasAttribute('onclick') ||
            el.hasAttribute('click') ||
            el.hasAttribute('ng-click') ||
            role === 'button' ||
            tag === 'button' ||
            tag === 'a' ||
            type === 'button' ||
            type === 'submit'
        );

        let elementType = 'unknown';
        if (tag === 'table' || role === 'table' || role === 'grid' || tag === 'mat-table' || tag === 'cdk-table') {
            elementType = 'table';
        } else if (
            tag === 'select' || tag === 'mat-select' || role === 'combobox' ||
            ariaHasPopup === 'listbox' || frameworkType === 'mat-select' ||
            cls.includes('select') || cls.includes('dropdown')
        ) {
            elementType = 'dropdown';
        } else if (
            tag === 'input' || tag === 'textarea' || role === 'textbox' ||
            el.getAttribute('contenteditable') === 'true' || frameworkType === 'mat-input' ||
            tag === 'mat-input'
        ) {
            elementType = 'input';
        } else if (
            tag === 'button' || role === 'button' || type === 'submit' || type === 'button' ||
            frameworkType === 'mat-button' || ['submit', 'save', 'login', 'log in', 'apply'].some(word => lowerText.includes(word))
        ) {
            elementType = 'button';
        } else if (clickable) {
            elementType = 'interactive';
        }

        return {
            type: elementType === 'unknown' && clickable ? 'interactive' : elementType,
            selector: generatedSelector(el),
            text,
            framework_type: frameworkType,
            is_clickable: clickable,
            is_dynamic: isAngularComponent || !!el.closest('[ng-version], app-root')
        };
    }

    return elements.map(el => {
        const style = window.getComputedStyle(el);
        const box   = el.getBoundingClientRect();
        const eventListenerCount = getEventListenerCount(el);
        const classification = classifyElement(el, style, eventListenerCount);
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
            "input:not([type='hidden']), textarea, select, button, [role='combobox'], "
            + "mat-select, mat-form-field, [contenteditable='true']"
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
            value:        el.value || '',
            generatedSelector: classification.selector,
            classifiedType: classification.type,
            frameworkType: classification.framework_type,
            isClickable: classification.is_clickable,
            isDynamic: classification.is_dynamic,
            eventListenerCount,
            cursor:       style.cursor || '',
            disabled:     !!el.disabled || el.getAttribute('aria-disabled') === 'true',
            ariaHasPopup: el.getAttribute('aria-haspopup') || '',
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
    framework = _detect_framework(page)

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

            target = _build_target_from_data(data, category, rule, framework)
            if target is None:
                continue

            if target["type"] == "dropdown" and _probe_dropdown_behavior(page, target):
                target["opens_listbox"] = True
                target["is_dynamic"] = True

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
    for category in ("actions", "inputs", "dropdowns", "interactive", "filters", "tables", "charts", "maps"):
        n = category_counts.get(category, 0)
        if n:
            human = {"actions": "buttons", "inputs": "inputs", "filters": "filters",
                     "dropdowns": "dropdowns", "interactive": "interactive elements",
                     "tables": "tables", "charts": "charts", "maps": "maps"}[category]
            logger.info(f"Detected {n} {human} on {route_label}")

    return {
        "route": page.url,
        "menu_name": menu_name or feature_name,
        "framework": framework,
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
                    "type": target.get("type", ""),
                    "purpose": target["purpose"],
                    "interaction_type": target["interaction_type"],
                    "relevance": target["relevance"],
                    "selector": target["selector"],
                    "framework": target.get("framework", ""),
                    "framework_type": target.get("framework_type", ""),
                    "component_tag": target.get("component_tag", ""),
                    "is_dynamic": target.get("is_dynamic", False),
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
    classified = data.get("classifiedType", "")
    if tag == "button" or category == "actions":
        return "button"
    if classified == "button":
        return "button"
    if tag == "a" or category == "navigation":
        return "link"
    if tag == "table" or category == "tables" or classified == "table":
        return "table"
    if classified == "dropdown":
        return "combobox"
    if classified == "input":
        return "textbox"
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

    generated = data.get("generatedSelector", "")
    if generated:
        candidates.append(("css", generated))

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
    if data.get("disabled"):
        return False
    if data.get("inShell"):
        return False

    label = _element_label_from_data(data)
    classified_type = data.get("classifiedType", "unknown")

    if category in {"actions", "navigation", "tabs"} and not label:
        return False
    if category == "actions" and not (
        _is_business_action(label)
        or classified_type == "button"
        or data.get("isClickable")
    ):
        return False
    if category == "inputs" and not label:
        # Require a label/placeholder/name so anonymous text boxes are dropped.
        return classified_type == "input"
    if category == "dropdowns":
        return classified_type == "dropdown" or bool(label)
    if category == "interactive":
        if classified_type not in {"button", "dropdown", "input", "interactive"}:
            return False
        if not label and not data.get("generatedSelector"):
            return False
    if category in {"filters", "forms"}:
        if data.get("controlCount", 0) == 0 and not label:
            return False
    if category == "tables":
        if classified_type == "table" and data.get("tagName") in {"mat-table", "cdk-table"}:
            return True
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


def _build_target_from_data(
    data: dict,
    category: str,
    rule: dict,
    framework: str,
) -> dict | None:
    label = _element_label_from_data(data)
    selector = _build_selector_from_data(data, category, label)
    if not selector:
        return None
    element_type = _element_type_from_data(data, category)
    target_category = _category_from_element_type(element_type, category)
    interaction_type = _interaction_type_for_element(element_type, rule["interaction_type"])
    component_tag = data.get("tagName", "") or ""
    framework_type = data.get("frameworkType", "") or "native"

    return {
        "id": f"{target_category}::{(label or selector['primary']['value'])[:MAX_LABEL_LENGTH]}",
        "category": target_category,
        "type": element_type,
        "label": label,
        "purpose": _purpose_for(category, label),
        "interaction_type": interaction_type,
        "relevance": rule["relevance"],
        "selector": selector,
        "text": data.get("text", "") or label,
        "framework": framework,
        "framework_type": framework_type,
        "component_tag": component_tag,
        "is_dynamic": bool(data.get("isDynamic")) or framework == "angular",
        "is_clickable": bool(data.get("isClickable")),
        "event_listener_count": int(data.get("eventListenerCount") or 0),
        "cursor": data.get("cursor", "") or "",
        "is_validation_target": target_category in VALIDATION_CATEGORIES,
    }


def _element_type_from_data(data: dict, category: str) -> str:
    classified = (data.get("classifiedType") or "unknown").lower()
    if classified in {"button", "input", "dropdown", "table", "interactive"}:
        return classified
    if category == "actions":
        return "button"
    if category == "inputs":
        return "input"
    if category == "dropdowns":
        return "dropdown"
    if category == "tables":
        return "table"
    if category == "interactive":
        return "interactive"
    return "interactive"


def _category_from_element_type(element_type: str, fallback_category: str) -> str:
    return {
        "button": "actions",
        "input": "inputs",
        "dropdown": "dropdowns",
        "table": "tables",
        "interactive": "interactive",
    }.get(element_type, fallback_category)


def _interaction_type_for_element(element_type: str, fallback: str) -> str:
    return {
        "button": "click",
        "input": "input",
        "dropdown": "select",
        "table": "validate_table",
        "interactive": "interact",
    }.get(element_type, fallback)


def _detect_framework(page: Page) -> str:
    try:
        if page.locator("[ng-version]").count() > 0:
            return "angular"
    except PlaywrightError:
        pass
    try:
        has_angular = page.evaluate(
            "() => !!(window.angular || window.ng || document.querySelector('app-root, [ng-version]'))"
        )
        if has_angular:
            return "angular"
    except PlaywrightError:
        pass
    return "unknown"


def _probe_dropdown_behavior(page: Page, target: dict[str, Any]) -> bool:
    """Click a dropdown-looking element and confirm a listbox appears.

    This is intentionally scoped to elements already classified as dropdowns,
    avoiding broad click probing that could navigate or submit forms.
    """
    selector = ((target.get("selector") or {}).get("primary") or {}).get("value", "")
    if not selector:
        return False
    try:
        element = page.locator(selector).first
        if not element.is_visible() or not element.is_enabled():
            return False
        before = page.locator("[role='listbox'], mat-option, [role='option']").count()
        element.hover(timeout=700)
        element.focus(timeout=700)
        element.click(timeout=1000)
        page.wait_for_timeout(250)
        after = page.locator("[role='listbox'], mat-option, [role='option']").count()
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        return after > before or after > 0
    except Exception:
        return False


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


def classify_element(element: Locator) -> dict[str, Any]:
    """Classify one DOM element using framework-aware behavior signals."""
    try:
        return element.evaluate(
            """
            (el) => {
                const style = window.getComputedStyle(el);
                const tag = el.tagName.toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const text = (el.innerText || el.value || '').trim();
                const lowerText = text.toLowerCase();
                const cls = String(el.className || '').toLowerCase();
                const typeAttr = (el.getAttribute('type') || '').toLowerCase();
                const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
                let listenerCount = el.onclick ? 1 : 0;
                try {
                    if (typeof getEventListeners === 'function') {
                        const listeners = getEventListeners(el) || {};
                        listenerCount = Object.keys(listeners)
                            .reduce((total, key) => total + (listeners[key] || []).length, listenerCount);
                    }
                } catch (err) {}
                const frameworkType = tag.startsWith('mat-') || tag.startsWith('app-') || tag.startsWith('ng-') || tag.startsWith('cdk-')
                    ? tag
                    : (
                        el.hasAttribute('matInput') || el.hasAttribute('matinput') ? 'mat-input'
                        : el.hasAttribute('mat-button') || el.hasAttribute('mat-raised-button') || el.hasAttribute('mat-flat-button')
                            || el.hasAttribute('mat-stroked-button') || el.hasAttribute('mat-icon-button') ? 'mat-button'
                        : cls.includes('mat-mdc-select') || cls.includes('mat-select') ? 'mat-select'
                        : cls.includes('mat-mdc-button') || cls.includes('mat-button') || cls.includes('mdc-button') ? 'mat-button'
                        : 'native'
                    );
                const clickable = !el.disabled && (
                    style.cursor === 'pointer' || listenerCount > 0 || el.hasAttribute('onclick') ||
                    el.hasAttribute('click') || role === 'button' || tag === 'button' ||
                    tag === 'a' || typeAttr === 'button' || typeAttr === 'submit'
                );
                let elementType = 'unknown';
                if (tag === 'table' || role === 'table' || role === 'grid' || tag === 'mat-table' || tag === 'cdk-table') elementType = 'table';
                else if (tag === 'select' || tag === 'mat-select' || role === 'combobox' || popup === 'listbox' || frameworkType === 'mat-select' || cls.includes('select') || cls.includes('dropdown')) elementType = 'dropdown';
                else if (tag === 'input' || tag === 'textarea' || role === 'textbox' || el.getAttribute('contenteditable') === 'true' || frameworkType === 'mat-input' || tag === 'mat-input') elementType = 'input';
                else if (tag === 'button' || role === 'button' || typeAttr === 'submit' || typeAttr === 'button' || frameworkType === 'mat-button' || ['submit', 'save', 'login', 'log in', 'apply'].some(word => lowerText.includes(word))) elementType = 'button';
                else if (clickable) elementType = 'interactive';
                return {
                    type: elementType === 'unknown' && clickable ? 'interactive' : elementType,
                    selector: el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` : (el.id ? `#${el.id}` : tag),
                    text,
                    framework_type: frameworkType
                };
            }
            """
        )
    except PlaywrightError:
        return {"type": "unknown", "selector": "", "text": "", "framework_type": "native"}


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
        "dropdowns": f"Select option for {readable}",
        "tables": f"Validate tabular data for {readable}",
        "forms": f"Complete form workflow for {readable}",
        "charts": f"Validate chart or visualization for {readable}",
        "maps": f"Validate map behavior for {readable}",
        "actions": f"Execute action: {readable}",
        "interactive": f"Interact with {readable}",
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
