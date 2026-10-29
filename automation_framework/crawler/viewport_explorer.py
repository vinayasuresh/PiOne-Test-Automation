"""Deterministic viewport coverage for lazy and virtualized application UI."""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Page

from automation_framework.config.settings import (
    MAX_SCROLL_CONTAINERS,
    MAX_SCROLL_POSITIONS,
    VIEWPORT_EXPLORATION_TIMEOUT,
)


_SCROLLABLES_SCRIPT = """() => {
  const nodes = [document.scrollingElement, ...document.querySelectorAll('body *')];
  const result = [];
  const selectorFor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node !== document.body) {
      const siblings = Array.from(node.parentElement.children).filter(item => item.tagName === node.tagName);
      parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(node) + 1})`);
      node = node.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  };
  for (const el of nodes) {
    if (!el || result.length >= %d) continue;
    const style = getComputedStyle(el);
    const scrollable = el.scrollHeight - el.clientHeight > 80 &&
      (el === document.scrollingElement || /(auto|scroll|overlay)/.test(style.overflowY));
    if (!scrollable) continue;
    const selector = el === document.scrollingElement ? '' : selectorFor(el);
    result.push({id: el === document.scrollingElement ? '__document__' : selector, selector, isDocument: el === document.scrollingElement,
      top: el.scrollTop, max: Math.max(0, el.scrollHeight - el.clientHeight)});
  }
  return result;
}""" % MAX_SCROLL_CONTAINERS


def explore_viewport_states(page: Page, on_state_change: Callable[[str], None], deadline: float | None = None) -> None:
    """Visit fixed scroll checkpoints for document and nested scroll panes.

    This is intentionally click-free. It exposes lazy/virtualized DOM content
    and emits a scan callback only when the rendered UI fingerprint changes.
    """
    stop_at = min(
        deadline or float("inf"), time.monotonic() + VIEWPORT_EXPLORATION_TIMEOUT / 1000,
    )
    try:
        containers = page.evaluate(_SCROLLABLES_SCRIPT)
    except Exception:
        return

    for container in containers:
        if time.monotonic() >= stop_at:
            return
        maximum = int(container.get("max", 0))
        if maximum <= 0:
            continue
        original = int(container.get("top", 0))
        # Visit ordered viewport-sized increments rather than a few large
        # jumps. Lazy lists often append only after intermediate scroll events.
        viewport = max(1, int(page.evaluate("() => window.innerHeight || 800")))
        step = max(1, viewport - 80)
        positions = list(range(0, maximum + 1, step))
        if positions[-1] != maximum:
            positions.append(maximum)
        if len(positions) > MAX_SCROLL_POSITIONS:
            positions = sorted({round(maximum * index / (MAX_SCROLL_POSITIONS - 1)) for index in range(MAX_SCROLL_POSITIONS)})
        previous = _fingerprint(page)
        try:
            for position in positions:
                if time.monotonic() >= stop_at:
                    return
                _scroll_to(page, container, position)
                page.wait_for_timeout(350)
                current = _fingerprint(page)
                if current != previous:
                    on_state_change(f"scroll:{container.get('id', 'container')}:{position}")
                    previous = current
        finally:
            try:
                _scroll_to(page, container, original)
            except Exception:
                pass


def _scroll_to(page: Page, container: dict, position: int) -> None:
    page.evaluate(
        """({container, position}) => {
          const target = container.isDocument ? document.scrollingElement : document.querySelector(container.selector);
          if (target) target.scrollTop = position;
        }""",
        {"container": container, "position": position},
    )


def _fingerprint(page: Page) -> str:
    try:
        return str(page.evaluate("""() => {
          const visible = Array.from(document.querySelectorAll('button, a, input, select, textarea, [role]'))
            .filter(el => { const r=el.getBoundingClientRect(), s=getComputedStyle(el); return r.width && r.height && s.display !== 'none' && s.visibility !== 'hidden'; })
            .slice(0, 200)
            .map(el => `${el.tagName}|${el.getAttribute('aria-label') || ''}|${(el.innerText || el.getAttribute('placeholder') || '').trim().slice(0, 40)}`)
            .join('~');
          return `${document.body ? document.body.querySelectorAll('*').length : 0}:${visible}`;
        }"""))
    except Exception:
        return ""
