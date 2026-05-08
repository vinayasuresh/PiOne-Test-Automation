"""Deep exploration: safely click expandable UI to discover hidden navigation.

Triggers we look for:
- buttons with `aria-expanded` (dropdowns, accordions, popovers)
- `[role='tab']` controls
- elements with `aria-haspopup`

Safety rules:
- destructive labels (delete, submit, save, ...) are skipped
- each trigger is clicked at most once (signature-based)
- recursion depth capped by MAX_DEEP_EXPLORATION_DEPTH

Change detection:
- URL change OR
- DOM child count of <body> changes by a meaningful amount OR
- a new visible nav element appears
"""

from typing import Callable

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import MAX_DEEP_EXPLORATION_DEPTH
from automation_framework.utils.logger import logger


TRIGGER_SELECTOR = (
    "button[aria-expanded], [role='button'][aria-expanded], "
    "[aria-haspopup='true'], [aria-haspopup='menu'], [aria-haspopup='listbox'], "
    "[role='tab']"
)

DESTRUCTIVE_WORDS = (
    "delete", "remove", "submit", "save", "confirm", "logout", "log out",
    "sign out", "deactivate", "disable", "publish", "purchase", "pay",
    "send", "approve", "reject", "cancel order", "discard",
)

CHANGE_THRESHOLD = 5  # min DOM child delta on <body> to count as a "change"

# Extract label, aria-label, title, and a unique signature for every trigger
# candidate in a single JS round-trip instead of calling Playwright per element.
_TRIGGER_SNAPSHOT_SCRIPT = """
(triggers) => triggers.map(el => ({
    sig: [
        el.getAttribute('role') || el.tagName.toLowerCase(),
        el.id || el.getAttribute('data-testid') || '',
        el.getAttribute('aria-label') || el.getAttribute('title') || '',
        (el.innerText || '').trim().slice(0, 40)
    ].join('|'),
    label: (
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        (el.innerText || '').trim().slice(0, 80)
    ),
    visible: (
        window.getComputedStyle(el).display !== 'none' &&
        window.getComputedStyle(el).visibility !== 'hidden' &&
        (el.offsetWidth > 0 || el.offsetHeight > 0)
    ),
}))
"""


def explore_hidden_navigation(
    page: Page,
    on_state_change: Callable[[str], None],
) -> None:
    """Discover hidden navigation by clicking expandable triggers.

    `on_state_change(label)` is called whenever a click produces an observable
    change. The callback is responsible for scanning/storing the new state.
    """
    visited_signatures: set[str] = set()
    _explore_recursive(page, on_state_change, visited_signatures, depth=0)


def _explore_recursive(
    page: Page,
    on_state_change: Callable[[str], None],
    visited_signatures: set[str],
    depth: int,
) -> None:
    if depth >= MAX_DEEP_EXPLORATION_DEPTH:
        return

    triggers = page.locator(TRIGGER_SELECTOR)
    trigger_count = triggers.count()
    if trigger_count == 0:
        return

    logger.info(f"Deep exploration depth={depth}: {trigger_count} candidate triggers")

    # Snapshot all trigger metadata in one JS round-trip.
    try:
        snapshot: list[dict] = triggers.evaluate_all(_TRIGGER_SNAPSHOT_SCRIPT)
    except Exception:
        logger.debug("Deep exploration: evaluate_all snapshot failed, skipping depth")
        return

    candidates: list[tuple[str, str]] = []
    for item in snapshot:
        if not item.get("visible"):
            continue
        sig = item.get("sig", "")
        label = " ".join((item.get("label") or "").split())
        if not sig or sig in visited_signatures:
            continue
        # Skip destructive actions.
        if label and any(word in label.lower() for word in DESTRUCTIVE_WORDS):
            continue
        candidates.append((sig, label))
        visited_signatures.add(sig)

    for signature, label in candidates:
        # Re-locate after each click since DOM may have changed.
        trigger = page.locator(f'{TRIGGER_SELECTOR}').filter(has_text=label).first if label else None
        if trigger is None or not _safe_visible(trigger):
            continue

        before_url = page.url
        before_body_children = _body_child_count(page)

        try:
            trigger.click(timeout=2000)
        except Exception:
            logger.debug(f"Deep exploration: could not click trigger '{label}'")
            continue

        if not _wait_for_change(page, before_url, before_body_children):
            continue

        logger.info(f"Deep exploration: state change detected after clicking '{label}'")
        try:
            on_state_change(label or "expanded_state")
        except Exception:
            logger.exception("on_state_change callback failed during deep exploration")

        # Recurse into the new state to find further-hidden navigation.
        _explore_recursive(page, on_state_change, visited_signatures, depth + 1)


def _is_safe_trigger(trigger: Locator) -> bool:
    if not _safe_visible(trigger):
        return False

    label = _trigger_label(trigger).lower()
    if not label:
        return True  # unlabeled toggle (e.g. icon button) is generally safe to expand

    return not any(word in label for word in DESTRUCTIVE_WORDS)


def _trigger_label(trigger: Locator) -> str:
    for attr in ("aria-label", "title"):
        try:
            value = trigger.get_attribute(attr) or ""
        except Exception:
            value = ""
        if value:
            return " ".join(value.split())

    try:
        text = trigger.inner_text(timeout=300)
    except Exception:
        text = ""
    return " ".join(text.split())


def _trigger_signature(trigger: Locator) -> str:
    """Stable identifier for one trigger so we don't click the same one twice."""
    try:
        return str(
            trigger.evaluate(
                """
                (el) => {
                    const role = el.getAttribute('role') || el.tagName.toLowerCase();
                    const label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
                    const text = (el.innerText || '').trim().slice(0, 40);
                    const id = el.id || el.getAttribute('data-testid') || '';
                    return [role, id, label, text].join('|');
                }
                """
            )
        )
    except Exception:
        return ""


def _safe_visible(locator: Locator) -> bool:
    try:
        return locator.is_visible()
    except Exception:
        return False


def _body_child_count(page: Page) -> int:
    try:
        return int(page.evaluate("() => document.body ? document.body.querySelectorAll('*').length : 0"))
    except Exception:
        return 0


def _wait_for_change(page: Page, before_url: str, before_body_children: int) -> bool:
    """Return True if URL or DOM changed meaningfully after a click."""
    # Reduced from 2500 ms — most SPAs respond within 1–1.5 s.
    try:
        page.wait_for_load_state("networkidle", timeout=1500)
    except PlaywrightTimeoutError:
        pass

    if page.url != before_url:
        return True

    after = _body_child_count(page)
    return abs(after - before_body_children) >= CHANGE_THRESHOLD
