"""Interactive Learning Mode capture utilities.

Injects a tiny event recorder into the page so a human user can demonstrate
the meaningful interactions on a route. The crawler then extracts those
events and converts them into structured component-style records that get
merged into the route's intelligence document.

Public API:
    inject_capture(page)            -> install window.__events + listeners
    wait_for_user(page, timeout)    -> block until user clicks Continue or
                                       the timeout elapses
    extract_events(page)            -> read window.__events
    events_to_interactions(events)  -> normalize to a stable schema
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page

from automation_framework.utils.logger import logger


# ---------------------------------------------------------------------------
# Injected JS — runs in the page context, never touched by Python directly.
# ---------------------------------------------------------------------------

# The recorder:
#   - exposes window.__events       : list of recorded events
#   - exposes window.__continueScan : flag the user / floating button flips
#   - shows a small fixed banner with a "Continue ▶" button
#   - captures click / input / change for buttons, inputs, selects, links
_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__captureInstalled) return;
  window.__captureInstalled = true;
  window.__events = [];
  window.__continueScan = false;

  // ── Selector helpers ────────────────────────────────────────────────
  function getUniqueSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const dt = el.getAttribute('data-testid');
    if (dt) return `[data-testid="${dt}"]`;
    const name = el.getAttribute('name');
    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    // Fall back to a short ancestor path (max 4 segments).
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      let seg = node.tagName.toLowerCase();
      if (node.classList && node.classList.length) {
        seg += '.' + Array.from(node.classList).slice(0, 2).map(c => CSS.escape(c)).join('.');
      }
      parts.unshift(seg);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  function describe(el) {
    if (!el) return '';
    const aria  = el.getAttribute && el.getAttribute('aria-label');
    const title = el.getAttribute && el.getAttribute('title');
    const ph    = el.getAttribute && el.getAttribute('placeholder');
    const text  = (el.innerText || el.textContent || '').trim();
    return (aria || title || ph || text || el.value || '').slice(0, 80);
  }

  // ── Event listeners ─────────────────────────────────────────────────
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.tagName) return;
    // Ignore clicks on the floating Continue button itself.
    if (t.closest && t.closest('#__capture_banner')) return;
    window.__events.push({
      type: 'click',
      tag:  t.tagName.toLowerCase(),
      text: describe(t),
      selector: getUniqueSelector(t),
      ts: Date.now(),
    });
  }, true);

  document.addEventListener('input', (e) => {
    const t = e.target;
    if (!t || !t.tagName) return;
    if (!['input', 'textarea'].includes(t.tagName.toLowerCase())) return;
    window.__events.push({
      type: 'input',
      tag:  t.tagName.toLowerCase(),
      text: describe(t),
      selector: getUniqueSelector(t),
      ts: Date.now(),
    });
  }, true);

  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!t || !t.tagName) return;
    const tag = t.tagName.toLowerCase();
    if (tag !== 'select') return;
    const opt = t.options && t.options[t.selectedIndex];
    window.__events.push({
      type: 'select',
      tag:  'select',
      text: describe(t) || (opt && opt.text) || '',
      selector: getUniqueSelector(t),
      ts: Date.now(),
    });
  }, true);

  // ── Floating Continue banner ────────────────────────────────────────
  const banner = document.createElement('div');
  banner.id = '__capture_banner';
  banner.style.cssText = [
    'position:fixed', 'bottom:20px', 'right:20px', 'z-index:2147483647',
    'background:#0f172a', 'color:#fff', 'padding:12px 16px', 'border-radius:12px',
    'box-shadow:0 8px 24px rgba(0,0,0,.35)', 'font:600 13px system-ui,sans-serif',
    'display:flex', 'align-items:center', 'gap:10px',
  ].join(';');
  banner.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:8px;">
      <span style="width:8px;height:8px;background:#10b981;border-radius:50%;
                   box-shadow:0 0 0 4px rgba(16,185,129,.25);"></span>
      Recording interactions…
    </span>
    <button id="__capture_continue"
            style="background:#2563eb;color:#fff;border:none;border-radius:8px;
                   padding:6px 12px;font:600 12px system-ui;cursor:pointer;">
      Continue ▶
    </button>`;
  document.body.appendChild(banner);
  document.getElementById('__capture_continue').addEventListener('click', () => {
    window.__continueScan = true;
    banner.style.opacity = '0.5';
  });
})();
"""


_REMOVE_BANNER_SCRIPT = r"""
(() => {
  const b = document.getElementById('__capture_banner');
  if (b) b.remove();
})();
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inject_capture(page: Page) -> None:
    """Install window.__events + listeners on the current page."""
    try:
        page.evaluate(_CAPTURE_SCRIPT)
        logger.info("Interactive capture: recorder injected")
    except Exception as exc:
        logger.warning(f"Interactive capture: failed to inject recorder ({exc})")


def wait_for_user(page: Page, timeout_seconds: int = 45) -> bool:
    """Block until the user clicks Continue or the timeout elapses.

    Returns True if the user explicitly clicked Continue, False on timeout.
    Polls every 500 ms — non-blocking on the JS side.
    """
    deadline = time.monotonic() + max(1, timeout_seconds)
    logger.info(f"Interactive capture: waiting up to {timeout_seconds}s for user")
    while time.monotonic() < deadline:
        try:
            done = page.evaluate("() => !!window.__continueScan")
        except Exception:
            return False
        if done:
            logger.info("Interactive capture: user clicked Continue")
            return True
        page.wait_for_timeout(500)
    logger.info("Interactive capture: timed out waiting for user")
    return False


def extract_events(page: Page) -> list[dict[str, Any]]:
    """Read window.__events and remove the floating banner."""
    try:
        events = page.evaluate("() => Array.isArray(window.__events) ? window.__events : []")
    except Exception as exc:
        logger.warning(f"Interactive capture: failed to read events ({exc})")
        events = []
    try:
        page.evaluate(_REMOVE_BANNER_SCRIPT)
    except Exception:
        pass
    return events or []


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------

# event.type  →  component bucket understood by the API response builder
_EVENT_TO_COMPONENT = {
    "click":  "button",
    "input":  "input",
    "select": "dropdown",
}


def events_to_interactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw browser events into stable interaction records.

    Returns a list of:
        { "type": "click"|"input"|"select",
          "component": "button"|"input"|"dropdown",
          "label": <human-readable>,
          "selector": <CSS selector>,
          "tag": <html tag> }

    Duplicates (same type+selector+label) are collapsed.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []

    for ev in events:
        ev_type = (ev.get("type") or "").lower()
        if ev_type not in _EVENT_TO_COMPONENT:
            continue
        label    = (ev.get("text") or "").strip()
        selector = (ev.get("selector") or "").strip()
        tag      = (ev.get("tag") or "").lower()

        # Discard obviously useless captures.
        if not selector and not label:
            continue
        # Clicks on body / html are noise.
        if ev_type == "click" and tag in {"body", "html", ""}:
            continue

        key = (ev_type, selector, label.lower())
        if key in seen:
            continue
        seen.add(key)

        out.append(
            {
                "type":      ev_type,
                "component": _EVENT_TO_COMPONENT[ev_type],
                "label":     label or selector,
                "selector":  selector,
                "tag":       tag,
            }
        )

    return out
