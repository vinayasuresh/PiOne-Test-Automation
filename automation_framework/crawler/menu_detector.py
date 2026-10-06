"""Bounded, deterministic discovery of navigation and overlay menus."""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page

from automation_framework.config.settings import MENU_EXPANSION_TIMEOUT, SUBMENU_TIMEOUT
from automation_framework.utils.logger import logger


ITEMS = (
    "a, button, [role='menuitem'], [role='link'], [role='button'], [role='option'], "
    "mat-option, .p-dropdown-item, .ng-option, .ant-select-item, "
    ".mat-mdc-option, .mat-option, select option"
)
NAV_CONTEXT = (
    "aside, nav, header, [role='navigation'], [role='menu'], [role='menubar'], "
    "mat-nav-list, mat-sidenav, [class*='sidebar' i], [class*='navbar' i], "
    "[class*='top-nav' i], [class*='sidenav' i]"
)
TRIGGERS = (
    "button[aria-expanded='false'], [role='button'][aria-expanded='false'], "
    "[role='menuitem'][aria-expanded='false'], [aria-haspopup='menu'], "
    "[aria-haspopup='listbox'], [aria-haspopup='true'], [role='combobox'], "
    "mat-select, ng-select, .p-dropdown, .mat-mdc-select, "
    "[class*='dropdown-toggle' i], [class*='submenu' i]"
)
OVERLAY_SELECTOR = (
    "[role='menu'], [role='menubar'], [role='listbox'], .mat-mdc-select-panel, "
    ".mat-select-panel, .ng-dropdown-panel, .p-dropdown-panel, "
    ".p-menu-overlay, .p-tieredmenu, .ant-select-dropdown, .ant-dropdown"
)
UNSAFE_LABELS = (
    "delete", "remove", "save", "submit", "logout", "sign out", "purchase",
    "pay", "confirm", "publish", "send", "approve", "reject",
)
MAX_PASSES = 4
MAX_TRIGGERS = 80


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())


def _snapshot(page: Page) -> list[dict[str, str]]:
    """Read rendered nav and overlay entries with element-specific locators."""
    script = """
    (items) => items.map(el => {
      const style = getComputedStyle(el), box = el.getBoundingClientRect();
      const nativeOption = el.tagName === 'OPTION';
      const optionParent = nativeOption ? el.closest('select') : null;
      const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
        parseFloat(style.opacity || '1') > 0 && ((box.width > 0 && box.height > 0) ||
        (nativeOption && optionParent && optionParent.getBoundingClientRect().width > 0));
      const parent = el.closest('aside, nav, header, select, [role="navigation"], [role="menu"], [role="menubar"], [role="listbox"], [role="combobox"], mat-select, ng-select, mat-nav-list, mat-sidenav, [class*="sidebar" i], [class*="navbar" i], [class*="top-nav" i], [class*="sidenav" i], .mat-mdc-select-panel, .mat-select-panel, .ng-dropdown-panel, .p-dropdown, .p-dropdown-panel, .p-menu-overlay, .p-tieredmenu, .ant-select-dropdown, .ant-dropdown');
      if (!visible || !parent) return null;
      const side = el.closest('aside, mat-sidenav, [class*="sidebar" i], [class*="sidenav" i]');
      let hierarchyLevel = 0, ancestor = el.parentElement;
      while (ancestor && ancestor !== parent) {
        if (ancestor.matches('ul, ol, [role="menu"], [role="list"], [class*="submenu" i], [class*="sub-menu" i]')) hierarchyLevel++;
        ancestor = ancestor.parentElement;
      }
      const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
      const testId = el.getAttribute('data-testid'), id = el.id;
      let selector = '';
      if (testId) selector = `[data-testid="${CSS.escape(testId)}"]`;
      else if (id) selector = `#${CSS.escape(id)}`;
      else {
        const parts = []; let node = el;
        while (node && node.nodeType === 1 && node !== document.body) {
          const siblings = Array.from(node.parentElement.children).filter(x => x.tagName === node.tagName);
          parts.unshift(`${node.tagName.toLowerCase()}[${siblings.indexOf(node) + 1}]`);
          node = node.parentElement;
        }
        selector = `xpath=/html/body/${parts.join('/')}`;
      }
      return {text, href: el.getAttribute('href') || '', selector,
        container: side ? side.tagName.toLowerCase() : parent.tagName.toLowerCase(),
        parentTag: parent.tagName.toLowerCase(), parentRole: parent.getAttribute('role') || '',
        parentClass: typeof parent.className === 'string' ? parent.className : '', hierarchyLevel};
    }).filter(Boolean)
    """
    return page.locator(ITEMS).evaluate_all(script)


def _trigger_label(trigger: Any) -> str:
    try:
        return _clean_text(trigger.get_attribute("aria-label") or trigger.get_attribute("title") or trigger.inner_text(timeout=250))[:80]
    except Exception:
        return ""


def _in_navigation_context(trigger: Any) -> bool:
    try:
        return bool(trigger.evaluate(f"el => !!el.closest({NAV_CONTEXT!r})"))
    except Exception:
        return False


def _is_dropdown_trigger(trigger: Any) -> bool:
    try:
        return bool(trigger.evaluate(
            "el => el.matches('[aria-haspopup=listbox], [role=combobox], mat-select, ng-select, .p-dropdown, .mat-mdc-select') "
            "|| !!el.closest('.p-dropdown, .mat-mdc-select, .mat-select, ng-select')"
        ))
    except Exception:
        return False


def _wait_for_overlay_or_mutation(page: Page, before_count: int) -> bool:
    """Observe a bounded mutation window and check for newly visible overlays."""
    try:
        page.wait_for_function(
            """({selector, beforeCount}) => new Promise(resolve => {
              let done = false;
              const finish = value => { if (!done) { done = true; observer.disconnect(); resolve(value); } };
              const visible = () => Array.from(document.querySelectorAll(selector)).some(el => {
                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
              });
              const observer = new MutationObserver(() => {
                if (visible() || document.querySelectorAll('*').length !== beforeCount) finish(true);
              });
              observer.observe(document.body, {childList:true, subtree:true, attributes:true});
              setTimeout(() => finish(visible()), 350);
            })""",
            arg={"selector": OVERLAY_SELECTOR, "beforeCount": before_count},
            timeout=900,
        )
        return bool(page.locator(OVERLAY_SELECTOR).evaluate_all(
            "els => els.some(el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); "
            "return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; })"
        ))
    except Exception:
        return False


def _expand_triggers(page: Page, event_callback: Any = None) -> list[dict[str, str]]:
    """Try focus, hover, then safe click for bounded navigation triggers."""
    attempted: set[str] = set()
    captured_dropdown_items: list[dict[str, str]] = []
    deadline = time.monotonic() + SUBMENU_TIMEOUT / 1000
    for _ in range(MAX_PASSES):
        if time.monotonic() >= deadline:
            break
        triggers = page.locator(TRIGGERS)
        count = min(triggers.count(), MAX_TRIGGERS)
        progressed = False
        for index in range(count):
            if time.monotonic() >= deadline:
                return captured_dropdown_items
            trigger = page.locator(TRIGGERS).nth(index)
            try:
                if not trigger.is_visible() or not (_in_navigation_context(trigger) or _is_dropdown_trigger(trigger)):
                    continue
                label = _trigger_label(trigger)
                if any(word in label.lower() for word in UNSAFE_LABELS):
                    continue
                signature = trigger.evaluate("el => [el.tagName, el.id, el.getAttribute('data-testid'), el.getAttribute('aria-label'), el.getAttribute('title'), (el.innerText||'').trim()].join('|')")
                if signature in attempted:
                    continue
                attempted.add(signature)
                before = int(page.evaluate("() => document.querySelectorAll('*').length"))
                for strategy in ("focus", "hover", "click"):
                    if time.monotonic() >= deadline:
                        return captured_dropdown_items
                    try:
                        if strategy == "focus":
                            trigger.focus(timeout=MENU_EXPANSION_TIMEOUT)
                        elif strategy == "hover":
                            trigger.hover(timeout=MENU_EXPANSION_TIMEOUT)
                        else:
                            # Only click controls that advertise expandable/menu behavior.
                            if trigger.get_attribute("aria-expanded") != "false" and trigger.get_attribute("aria-haspopup") not in {"menu", "listbox", "true"} and not _is_dropdown_trigger(trigger):
                                continue
                            trigger.click(timeout=MENU_EXPANSION_TIMEOUT)
                        page.wait_for_timeout(70)
                        opened = _wait_for_overlay_or_mutation(page, before)
                        if opened:
                            if event_callback:
                                event_callback("submenu_opened", {
                                    "label": label, "strategy": strategy, "url": page.url,
                                })
                            if _is_dropdown_trigger(trigger):
                                captured_dropdown_items.extend(_snapshot(page))
                                try:
                                    page.keyboard.press("Escape")
                                except Exception:
                                    pass
                            break
                    except Exception:
                        continue
                progressed = True
            except Exception:
                continue
        if not progressed:
            break
    return captured_dropdown_items


def detect_menus(page: Page, event_callback: Any = None) -> dict[str, list[dict[str, Any]]]:
    """Discover navigation and visible component menu entries deterministically."""
    captured_dropdown_items = _expand_triggers(page, event_callback)
    seen: set[tuple[str, str, str]] = set()
    sidebar: list[dict[str, str | None]] = []
    top: list[dict[str, str | None]] = []
    overlays: list[dict[str, str | None]] = []
    for item in [*captured_dropdown_items, *_snapshot(page)]:
        label = _clean_text(item.get("text", ""))
        if not label:
            continue
        key = (item.get("href", ""), item.get("selector", ""), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        entry = {"text": label, "href": item.get("href") or None,
                 "selector": item.get("selector", ""),
                 "container_selector": item.get("container", ""),
                 "hierarchy_level": int(item.get("hierarchyLevel", 0))}
        parent_role = item.get("parentRole", "")
        parent_class = item.get("parentClass", "").lower()
        container = item.get("container", "")
        if item.get("parentTag") == "select" or parent_role in {"menu", "menubar", "listbox", "combobox"} or any(
            token in parent_class for token in ("dropdown", "overlay", "select-panel", "tieredmenu")
        ):
            overlays.append(entry)
        elif container in {"aside", "mat-sidenav"} or "sidebar" in parent_class or "sidenav" in parent_class:
            sidebar.append(entry)
        else:
            top.append(entry)
    logger.info("Discovered navigation=%d sidebar=%d top=%d overlay=%d", len(sidebar) + len(top), len(sidebar), len(top), len(overlays))
    def build_tree(items: list[dict[str, str | None]]) -> list[dict[str, Any]]:
        roots: list[dict[str, Any]] = []
        stack: dict[int, dict[str, Any]] = {}
        for item in items:
            level = max(0, int(item.get("hierarchy_level", 0)))
            node = {**item, "children": []}
            parent = stack.get(level - 1) if level else None
            (parent["children"] if parent else roots).append(node)
            stack = {depth: value for depth, value in stack.items() if depth < level}
            stack[level] = node
        return roots

    result: dict[str, list[dict[str, Any]]] = {}
    if sidebar:
        result["sidebar_menus"] = [{"type": "sidebar", "selector": "navigation", "items": sidebar, "tree": build_tree(sidebar)}]
    if top:
        result["top_navigation_menus"] = [{"type": "top_navigation", "selector": "navigation", "items": top, "tree": build_tree(top)}]
    if overlays:
        result["overlay_menus"] = [{"type": "overlay", "selector": "overlay", "items": overlays, "tree": build_tree(overlays)}]
    return result
