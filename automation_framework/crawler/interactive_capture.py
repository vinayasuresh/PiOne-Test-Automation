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
    const old = document.getElementById('__capture_banner');
    if (old) old.remove();
  }
  window.__captureInstalled = true;
  window.__events = [];
  window.__continueScan = false;
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

  function closestMeaningfulElement(target) {
    if (!target || !target.closest) return target;
    const standard = target.closest(
      "button, [role='button'], input, textarea, select, [role='combobox'], "
      + "[contenteditable='true'], mat-select, mat-form-field, mat-table, "
      + "[aria-haspopup='listbox'], [onclick], [tabindex]:not([tabindex='-1']), "
      + "[class*='button' i], [class*='btn' i], [class*='select' i], [class*='dropdown' i]"
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

    window.__events.push({
      type: eventType,
      detected_type: detectType(target, eventType),
      tag: target.tagName.toLowerCase(),
      component_tag: target.tagName.toLowerCase(),
      framework_type: frameworkType(target),
      text,
      selector,
      value: hasValue ? '<dynamic>' : '',
      page_url: location.href,
      ts: Date.now(),
    });
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

  const banner = document.createElement('div');
  banner.id = '__capture_banner';
  banner.style.cssText = [
    'position:fixed', 'bottom:20px', 'right:20px', 'z-index:2147483647',
    'background:#0f172a', 'color:#fff', 'padding:12px 16px', 'border-radius:10px',
    'box-shadow:0 8px 24px rgba(0,0,0,.35)', 'font:600 13px system-ui,sans-serif',
    'display:flex', 'align-items:center', 'gap:12px', 'max-width:560px',
  ].join(';');
  banner.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:8px;line-height:1.35;">
      <span style="width:8px;height:8px;background:#10b981;border-radius:50%;box-shadow:0 0 0 4px rgba(16,185,129,.25);"></span>
      Confirm this page elements: click important controls, fill/select sample fields, then continue.
    </span>
    <button id="__capture_continue"
            style="background:#2563eb;color:#fff;border:none;border-radius:8px;
                   padding:7px 12px;font:600 12px system-ui;cursor:pointer;white-space:nowrap;">
      Confirm & Continue
    </button>`;
  document.body.appendChild(banner);
  document.getElementById('__capture_continue').addEventListener('click', () => {
    window.__continueScan = true;
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


def inject_capture(page: Page) -> None:
    """Install the page overlay, highlighter, and event listeners."""
    try:
        page.evaluate(_CAPTURE_SCRIPT)
        logger.info("Interactive capture: confirmation overlay injected")
    except Exception as exc:
        logger.warning(f"Interactive capture: failed to inject recorder ({exc})")


def wait_for_user(page: Page, timeout_seconds: int = 45) -> bool:
    """Block until the user confirms the route or the timeout elapses."""
    deadline = time.monotonic() + max(1, timeout_seconds)
    logger.info(f"Interactive capture: waiting up to {timeout_seconds}s for user confirmation")
    while time.monotonic() < deadline:
        try:
            done = page.evaluate("() => !!window.__continueScan")
        except Exception:
            return False
        if done:
            logger.info("Interactive capture: user confirmed route")
            return True
        page.wait_for_timeout(500)
    logger.info("Interactive capture: timed out waiting for confirmation")
    return False


def extract_events(page: Page) -> list[dict[str, Any]]:
    """Read recorded DOM events and remove the overlay."""
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
