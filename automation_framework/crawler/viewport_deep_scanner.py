"""ViewportDeepScanner: Bounded document and nested container deep scrolling.

Guarantees:
- Deterministic scrolling in viewport-sized increments.
- Detection of nested scrollable containers (overflow: auto/scroll, virtual tables).
- Detects overlays, popups, and lazy-loaded elements via DOM mutations.
- Safe restoration to initial scroll positions after exploration.
"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import Page

from automation_framework.config.settings import (
    MAX_SCROLL_CONTAINERS,
    MAX_SCROLL_POSITIONS,
    VIEWPORT_EXPLORATION_TIMEOUT,
)
from automation_framework.utils.logger import logger


_DETECT_SCROLLABLES_SCRIPT = """(maxContainers) => {
  const nodes = [document.scrollingElement, ...document.querySelectorAll('body *')];
  const results = [];
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
    if (!el || results.length >= maxContainers) continue;
    const style = getComputedStyle(el);
    const isDoc = (el === document.scrollingElement || el === document.body);
    const hasScroll = (el.scrollHeight - el.clientHeight > 60);
    const isScrollable = isDoc || /(auto|scroll|overlay)/.test(style.overflowY || style.overflow);
    if (!hasScroll || !isScrollable) continue;

    const selector = isDoc ? '' : selectorFor(el);
    results.push({
      id: isDoc ? '__document__' : selector,
      selector,
      isDocument: isDoc,
      top: el.scrollTop,
      max: Math.max(0, el.scrollHeight - el.clientHeight)
    });
  }
  return results;
}"""

_DOM_FINGERPRINT_SCRIPT = """() => {
  const count = document.body ? document.body.querySelectorAll('*').length : 0;
  const sample = Array.from(document.querySelectorAll('button, a, input, select, textarea, tr, [role]'))
    .filter(el => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    })
    .slice(0, 150)
    .map(el => `${el.tagName}:${el.getAttribute('aria-label') || el.innerText || ''}`)
    .join(';');
  return `${count}:${sample.slice(0, 500)}`;
}"""


class ViewportDeepScanner:
    """Explores document and nested scrollable regions deterministically."""

    def __init__(
        self,
        page: Page,
        timeout_ms: int = VIEWPORT_EXPLORATION_TIMEOUT,
        max_containers: int = MAX_SCROLL_CONTAINERS,
        max_positions: int = MAX_SCROLL_POSITIONS,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.max_containers = max_containers
        self.max_positions = max_positions

    def deep_scroll(
        self,
        on_state_change: Callable[[str], None],
        deadline: float | None = None,
    ) -> None:
        """Scan viewport and nested containers in increments."""
        stop_at = min(
            deadline or float("inf"),
            time.monotonic() + self.timeout_ms / 1000,
        )

        try:
            containers = self.page.evaluate(_DETECT_SCROLLABLES_SCRIPT, self.max_containers)
        except Exception:
            logger.debug("Failed to evaluate scrollable containers")
            return

        for container in containers:
            if time.monotonic() >= stop_at:
                break

            maximum = int(container.get("max", 0))
            if maximum <= 0:
                continue

            original = int(container.get("top", 0))
            viewport_height = max(1, int(self.page.evaluate("() => window.innerHeight || 800")))
            step = max(1, viewport_height - 100)

            positions = list(range(0, maximum + 1, step))
            if positions[-1] != maximum:
                positions.append(maximum)

            if len(positions) > self.max_positions:
                positions = sorted({
                    round(maximum * i / (self.max_positions - 1))
                    for i in range(self.max_positions)
                })

            prev_fp = self._get_fingerprint()
            try:
                for pos in positions:
                    if time.monotonic() >= stop_at:
                        break

                    self._scroll_container(container, pos)
                    self.page.wait_for_timeout(300)

                    curr_fp = self._get_fingerprint()
                    if curr_fp != prev_fp:
                        cid = container.get("id", "container")
                        on_state_change(f"scroll:{cid}:{pos}")
                        prev_fp = curr_fp
            finally:
                try:
                    self._scroll_container(container, original)
                except Exception:
                    pass

    def _scroll_container(self, container: dict, position: int) -> None:
        self.page.evaluate(
            """({container, position}) => {
              const target = container.isDocument
                ? (document.scrollingElement || document.documentElement || document.body)
                : document.querySelector(container.selector);
              if (target) {
                target.scrollTop = position;
                if (container.isDocument) {
                  window.scrollTo(0, position);
                }
                target.dispatchEvent(new Event('scroll', { bubbles: true }));
              }
            }""",
            {"container": container, "position": position},
        )

    def _get_fingerprint(self) -> str:
        try:
            return str(self.page.evaluate(_DOM_FINGERPRINT_SCRIPT))
        except Exception:
            return ""
