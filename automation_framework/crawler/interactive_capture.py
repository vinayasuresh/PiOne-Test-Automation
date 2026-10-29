"""Interactive learning capture utilities.

The crawler uses this module when interactive mode is enabled. It injects a
small page overlay, highlights hovered UI elements, records user-confirmed
click/input/select events, and converts those events into both interaction
records and test-ready flow steps.
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page

from automation_framework.utils.logger import logger


_CAPTURE_SCRIPT = r"""
(() => {
  if (window.__captureInstalled) {
    window.__events = [];
    window.__continueScan = false;
    window.__retryScan = false;
    window.__endScan = false;
    const old = document.getElementById('__capture_banner');
    if (old) old.remove();
  }
  window.__captureInstalled = true;
  window.__events = [];
  window.__continueScan = false;
  window.__retryScan = false;
  window.__endScan = false;
  window.__lastHighlight = null;

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function getUniqueSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    const testId = el.getAttribute('data-testid');
    if (testId) return `[data-testid="${testId.replace(/"/g, '\\"')}"]`;
    const aria = el.getAttribute('aria-label');
    if (aria) return `${el.tagName.toLowerCase()}[aria-label="${aria.replace(/"/g, '\\"')}"]`;
    if (el.id && !/\d{4,}/.test(el.id)) return '#' + cssEscape(el.id);
    const name = el.getAttribute('name');
    if (name) return `${el.tagName.toLowerCase()}[name="${name.replace(/"/g, '\\"')}"]`;

    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body && parts.length < 4) {
      const parent = node.parentElement;
      if (!parent) break;
      const tag = node.tagName.toLowerCase();
      const siblings = Array.from(parent.children).filter(child => child.tagName === node.tagName);
      const index = siblings.indexOf(node) + 1;
      parts.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${index})` : tag);
      node = parent;
    }
    return parts.join(' > ');
  }

  function getXPath(el) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1) {
      if (node.id && !/\d{4,}/.test(node.id)) {
        parts.unshift(`*[@id=${JSON.stringify(node.id)}]`);
        break;
      }
      const tag = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const sameTag = Array.from(parent.children).filter(child => child.tagName === node.tagName);
      parts.unshift(`${tag}[${sameTag.indexOf(node) + 1}]`);
      node = parent;
    }
    return '/' + parts.join('/');
  }

  function closestMeaningfulElement(target) {
    if (!target || !target.closest) return target;
    const standard = target.closest(
      "button, [role='button'], input, textarea, select, [role='combobox'], "
      + "[contenteditable='true'], mat-select, mat-form-field, mat-table, "
      + "[aria-haspopup='listbox'], [onclick], [ng-click], [tabindex]:not([tabindex='-1'])"
    );
    if (standard) return standard;

    let node = target;
    while (node && node.nodeType === 1 && node !== document.body) {
      const tag = node.tagName.toLowerCase();
      if (tag.startsWith('app-') || tag.startsWith('mat-') || tag.startsWith('ng-') || tag.startsWith('cdk-')) {
        return node;
      }
      node = node.parentElement;
    }
    return target;
  }

  function describe(el) {
    if (!el) return '';
    const aria = el.getAttribute && el.getAttribute('aria-label');
    const title = el.getAttribute && el.getAttribute('title');
    const placeholder = el.getAttribute && el.getAttribute('placeholder');
    const text = (el.innerText || el.textContent || '').trim();
    return (aria || title || placeholder || text || el.value || '').slice(0, 120);
  }

  function frameworkType(el) {
    if (!el || !el.tagName) return 'native';
    const tag = el.tagName.toLowerCase();
    const cls = String(el.className || '').toLowerCase();
    if (tag.startsWith('app-') || tag.startsWith('mat-') || tag.startsWith('ng-') || tag.startsWith('cdk-')) return tag;
    if (el.hasAttribute('matInput') || el.hasAttribute('matinput')) return 'mat-input';
    if (el.hasAttribute('mat-button') || el.hasAttribute('mat-raised-button') || cls.includes('mat-button') || cls.includes('mdc-button')) return 'mat-button';
    if (cls.includes('mat-select')) return 'mat-select';
    return 'native';
  }

  function detectType(el, eventType) {
    if (!el || !el.tagName) return eventType || 'interactive';
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
    const cls = String(el.className || '').toLowerCase();
    const text = (el.innerText || el.value || '').toLowerCase();
    const fw = frameworkType(el);

    if (eventType === 'input') return 'input';
    if (eventType === 'select') return 'dropdown';
    if (tag === 'select' || tag === 'mat-select' || role === 'combobox' || popup === 'listbox' || fw === 'mat-select' || cls.includes('select') || cls.includes('dropdown')) return 'dropdown';
    if (tag === 'input' || tag === 'textarea' || role === 'textbox' || el.getAttribute('contenteditable') === 'true' || fw === 'mat-input') return 'input';
    if (tag === 'table' || tag === 'mat-table' || role === 'table' || role === 'grid') return 'table';
    if (tag === 'button' || role === 'button' || type === 'button' || type === 'submit' || fw === 'mat-button' || ['submit', 'save', 'login', 'log in', 'apply'].some(word => text.includes(word))) return 'button';
    return 'interactive';
  }

  function pushEvent(eventType, rawTarget, hasValue) {
    const target = closestMeaningfulElement(rawTarget);
    if (!target || !target.tagName || target.closest('#__capture_banner')) return;

    const selector = getUniqueSelector(target);
    const text = describe(target);
    if (!selector && !text) return;

    const record = {
      type: eventType,
      detected_type: detectType(target, eventType),
      tag: target.tagName.toLowerCase(),
      component_tag: target.tagName.toLowerCase(),
      framework_type: frameworkType(target),
      text,
      selector,
      xpath: getXPath(target),
      value: hasValue ? '<dynamic>' : '',
      page_url: location.href,
      page_title: document.title || '',
      source: 'interactive_learning',
      element_html: String(target.outerHTML || '').replace(/\s+/g, ' ').slice(0, 500),
      ts: Date.now(),
    };
    window.__events.push(record);
    // A navigation replaces window.__events.  The Playwright binding sends a
    // copy to Python immediately, so evidence survives clicks that change page.
    try {
      if (typeof window.__testmateRecordInteraction === 'function') {
        window.__testmateRecordInteraction(record);
      }
    } catch (_) {}
  }

  document.addEventListener('mouseover', (event) => {
    const target = closestMeaningfulElement(event.target);
    if (!target || !target.style || target.closest('#__capture_banner')) return;

    if (window.__lastHighlight && window.__lastHighlight !== target) {
      window.__lastHighlight.style.outline = window.__lastHighlight.__oldOutline || '';
      window.__lastHighlight.style.boxShadow = window.__lastHighlight.__oldBoxShadow || '';
    }

    if (target.__oldOutline === undefined) target.__oldOutline = target.style.outline || '';
    if (target.__oldBoxShadow === undefined) target.__oldBoxShadow = target.style.boxShadow || '';
    target.style.outline = '2px solid #2563eb';
    target.style.boxShadow = '0 0 0 4px rgba(37,99,235,.18)';
    window.__lastHighlight = target;
  }, true);

  document.addEventListener('click', (event) => {
    pushEvent('click', event.target, false);
  }, true);

  document.addEventListener('input', (event) => {
    const target = event.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    if (!['input', 'textarea'].includes(tag) && target.getAttribute('contenteditable') !== 'true') return;
    pushEvent('input', target, true);
  }, true);

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (!target || !target.tagName) return;
    const tag = target.tagName.toLowerCase();
    const role = (target.getAttribute('role') || '').toLowerCase();
    if (tag !== 'select' && role !== 'combobox') return;
    pushEvent('select', target, true);
  }, true);

  let summaryText = 'Automatic scan is complete for this module. Choose an action:';
  if (window.__reviewSummary && window.__reviewSummary.module_name) {
    const s = window.__reviewSummary;
    summaryText = `Module <b>${s.module_name}</b> complete:<br/>` +
      `• Routes: <b>${s.routes_scanned || s.route_count || 1}</b> | ` +
      `• Elements: <b>${s.elements_found || s.element_count || 0}</b> | ` +
      `• Safe actions: <b>${s.safe_actions_explored || 0}</b> | ` +
      `• Parking lot: <b>${s.parking_lot_count || 0}</b> | ` +
      `• Health: <b>${s.route_health || 'HEALTHY'}</b>` +
      (s.errors_count ? ` | • Errors: <b style="color:#ef4444">${s.errors_count}</b>` : '');
  }

  const banner = document.createElement('div');
  banner.id = '__capture_banner';
  banner.style.cssText = [
    'position:fixed', 'bottom:20px', 'right:20px', 'z-index:2147483647',
    'background:#0f172a', 'color:#fff', 'padding:14px 18px', 'border-radius:10px',
    'box-shadow:0 8px 24px rgba(0,0,0,.35)', 'font:500 13px system-ui,sans-serif',
    'display:flex', 'align-items:center', 'gap:14px', 'max-width:750px',
  ].join(';');
  banner.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:8px;line-height:1.4;">
      <span style="width:8px;height:8px;background:#10b981;border-radius:50%;box-shadow:0 0 0 4px rgba(16,185,129,.25);"></span>
      <span>${summaryText}</span>
    </span>
    <button id="__capture_continue"
            style="background:#2563eb;color:#fff;border:none;border-radius:8px;
                   padding:7px 12px;font:600 12px system-ui;cursor:pointer;white-space:nowrap;">
      Continue Next
    </button>
    <button id="__capture_retry"
            style="background:#d97706;color:#fff;border:none;border-radius:8px;
                   padding:7px 12px;font:600 12px system-ui;cursor:pointer;white-space:nowrap;">
      Retry Module
    </button>
    <button id="__capture_end"
            style="background:#334155;color:#fff;border:1px solid #64748b;border-radius:8px;
                   padding:7px 12px;font:600 12px system-ui;cursor:pointer;white-space:nowrap;">
      End & Save
    </button>`;
  document.body.appendChild(banner);
  document.getElementById('__capture_continue').addEventListener('click', () => {
    window.__continueScan = true;
    banner.style.opacity = '0.55';
  });
  document.getElementById('__capture_retry').addEventListener('click', () => {
    window.__retryScan = true;
    banner.style.opacity = '0.55';
  });
  document.getElementById('__capture_end').addEventListener('click', () => {
    window.__endScan = true;
    banner.style.opacity = '0.55';
  });
})();
"""


_REMOVE_BANNER_SCRIPT = r"""
(() => {
  if (window.__lastHighlight) {
    window.__lastHighlight.style.outline = window.__lastHighlight.__oldOutline || '';
    window.__lastHighlight.style.boxShadow = window.__lastHighlight.__oldBoxShadow || '';
  }
  const banner = document.getElementById('__capture_banner');
  if (banner) banner.remove();
})();
"""


_EVENT_TO_COMPONENT = {
    "click": "button",
    "input": "input",
    "select": "dropdown",
}


_PAGE_EVENT_BUFFERS: dict[int, list[dict[str, Any]]] = {}
_CAPTURE_BINDING = "__testmateRecordInteraction"


def _install_persistent_capture(page: Page) -> list[dict[str, Any]]:
    """Install a cross-navigation event bridge and return this page's buffer.

    ``page.evaluate`` listeners disappear on a page navigation.  Interactive
    learning intentionally lets a human click navigation controls, so keeping
    the evidence only in ``window`` lost precisely the most valuable clicks.
    An init script is applied to every later document and the exposed binding
    copies each record to Python before navigation can discard it.
    """
    key = id(page)
    buffer = _PAGE_EVENT_BUFFERS.setdefault(key, [])

    if not getattr(page, "_testmate_capture_binding_installed", False):
        def record_from_page(_source: Any, record: Any) -> None:
            if isinstance(record, dict):
                buffer.append(dict(record))

        try:
            page.expose_binding(_CAPTURE_BINDING, record_from_page)
            setattr(page, "_testmate_capture_binding_installed", True)
        except Exception as exc:
            # A mocked Page and an already-installed binding are both safe
            # fallbacks: current-document extraction still works.
            logger.debug("Interactive capture binding unavailable: %s", exc)

    try:
        if not getattr(page, "_testmate_capture_init_script_installed", False):
            page.add_init_script(_CAPTURE_SCRIPT)
            setattr(page, "_testmate_capture_init_script_installed", True)
    except Exception as exc:
        logger.debug("Interactive capture init script unavailable: %s", exc)
    return buffer


def inject_capture(page: Page, summary: dict[str, Any] | None = None) -> None:
    """Install the page overlay, highlighter, and event listeners."""
    try:
        _install_persistent_capture(page).clear()
        if summary:
            page.evaluate("(s) => { window.__reviewSummary = s; }", summary)
        page.evaluate(_CAPTURE_SCRIPT)
        logger.info("Interactive capture: confirmation overlay injected")
    except Exception as exc:
        logger.warning(f"Interactive capture: failed to inject recorder ({exc})")


def wait_for_user(page: Page, timeout_seconds: int = 45, run_id: str | None = None) -> str:
    """Return ``continue``, ``retry``, ``end``, or ``timeout`` from the review overlay or API."""
    deadline = time.monotonic() + max(1, timeout_seconds)
    logger.info(f"Interactive capture: waiting up to {timeout_seconds}s for user confirmation")
    while time.monotonic() < deadline:
        if run_id:
            try:
                from api.services.scan_control_service import is_scan_stop_requested, pop_user_decision
                if is_scan_stop_requested(run_id):
                    logger.info("Interactive capture: scan stop requested via API control")
                    return "end"
                api_choice = pop_user_decision(run_id)
                if api_choice:
                    logger.info(f"Interactive capture: user selected {api_choice} via API control")
                    return api_choice
            except Exception:
                pass
        try:
            choice = page.evaluate("() => window.__endScan ? 'end' : (window.__retryScan ? 'retry' : (window.__continueScan ? 'continue' : ''))")
        except Exception:
            return "timeout"
        if choice:
            logger.info("Interactive capture: user selected %s", choice)
            return str(choice)
        page.wait_for_timeout(500)
    logger.info("Interactive capture: timed out waiting for confirmation")
    return "timeout"


def extract_events(page: Page) -> list[dict[str, Any]]:
    """Read recorded DOM events and remove the overlay."""
    bridged_events = list(_PAGE_EVENT_BUFFERS.get(id(page), []))
    try:
        events = page.evaluate("() => Array.isArray(window.__events) ? window.__events : []")
    except Exception as exc:
        logger.warning(f"Interactive capture: failed to read events ({exc})")
        events = []
    try:
        page.evaluate(_REMOVE_BANNER_SCRIPT)
    except Exception:
        pass
    return bridged_events + (events or [])


def events_to_interactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw browser events into stable interaction records."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []

    for event in events:
        event_type = (event.get("type") or "").lower()
        if event_type not in _EVENT_TO_COMPONENT:
            continue

        selector = (event.get("selector") or "").strip()
        label = (event.get("text") or "").strip()
        tag = (event.get("tag") or "").lower()
        detected_type = (event.get("detected_type") or "").lower()

        if not selector and not label:
            continue
        if event_type == "click" and tag in {"body", "html", ""}:
            continue

        key = (event_type, selector, label.lower())
        if key in seen:
            continue
        seen.add(key)

        component = _component_for(event_type, detected_type)
        out.append(
            {
                "type": event_type,
                "component": component,
                "detected_type": detected_type or component,
                "label": label or selector,
                "selector": selector,
                "tag": tag,
                "framework_type": event.get("framework_type") or "native",
                "component_tag": event.get("component_tag") or tag,
                "page_url": event.get("page_url") or "",
                "page_title": event.get("page_title") or "",
                "xpath": event.get("xpath") or "",
                "source": event.get("source") or "interactive_learning",
                "element_html": event.get("element_html") or "",
                "timestamp": event.get("ts") or 0,
                "value": event.get("value") or "",
            }
        )

    return out


def interactions_to_flow(interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert interactions into test-ready flow steps."""
    flow: list[dict[str, Any]] = []
    for interaction in interactions:
        event_type = interaction.get("type")
        if event_type == "click":
            step = "click"
        elif event_type == "input":
            step = "fill"
        elif event_type == "select":
            step = "select"
        else:
            continue

        item = {
            "step": step,
            "selector": interaction.get("selector", ""),
            "page": interaction.get("page_url", ""),
            "element_type": interaction.get("detected_type") or interaction.get("component", ""),
            "text": interaction.get("label", ""),
            "xpath": interaction.get("xpath", ""),
            "source": interaction.get("source", "interactive_learning"),
        }
        if step in {"fill", "select"}:
            item["value"] = "<dynamic>"
        flow.append(item)
    return flow


def interactions_to_targets(
    interactions: list[dict[str, Any]],
    framework: str = "unknown",
) -> list[dict[str, Any]]:
    """Convert user-confirmed interactions into automation targets."""
    targets: list[dict[str, Any]] = []
    for interaction in interactions:
        selector_value = interaction.get("selector", "")
        label = interaction.get("label") or selector_value
        element_type = interaction.get("detected_type") or interaction.get("component") or "interactive"
        category = _category_for_element_type(element_type)
        interaction_type = {
            "button": "click",
            "input": "input",
            "dropdown": "select",
            "table": "validate_table",
            "interactive": "interact",
        }.get(element_type, interaction.get("type", "interact"))

        targets.append(
            {
                "id": f"{category}::{label[:80]}",
                "category": category,
                "type": element_type,
                "label": label,
                "purpose": f"User-confirmed {element_type}: {label}",
                "interaction_type": interaction_type,
                "relevance": "HIGH",
                "selector": {
                    "primary": {"strategy": "css", "value": selector_value},
                    "fallback": [],
                },
                "text": label,
                "framework": framework,
                "framework_type": interaction.get("framework_type") or "native",
                "component_tag": interaction.get("component_tag") or interaction.get("tag") or "",
                "is_dynamic": framework == "angular" or interaction.get("framework_type") != "native",
                "is_user_confirmed": True,
                "is_validation_target": category in {"dropdowns", "tables", "forms", "filters"},
            }
        )
    return targets


def _component_for(event_type: str, detected_type: str) -> str:
    if detected_type == "dropdown":
        return "dropdown"
    if detected_type == "input":
        return "input"
    if detected_type == "table":
        return "table"
    if detected_type == "interactive":
        return "interactive"
    return _EVENT_TO_COMPONENT.get(event_type, "interactive")


def _category_for_element_type(element_type: str) -> str:
    return {
        "button": "actions",
        "input": "inputs",
        "dropdown": "dropdowns",
        "table": "tables",
        "interactive": "interactive",
    }.get(element_type, "interactive")
