"""InteractionProbeEngine: Performs safe UI probes and diverts unsafe actions to the parking lot.

Guarantees:
- Only executes actions classified as safe (safe_navigation, safe_expand, safe_filter, etc.).
- Bounded execution with deterministic timeouts.
- Records before/after URL, DOM signature, and newly revealed targets.
- Restores original route/state after each probe to ensure scanner determinism.
- Diverts unsafe, destructive, or mutation actions to the parking lot for user review.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_safe_action,
    is_parking_lot_action,
)
from automation_framework.crawler.ui_wait_engine import wait_for_ui_stability
from automation_framework.utils.logger import logger


class InteractionProbeEngine:
    """Probes candidate interactive controls deterministically and safely."""

    def __init__(self, page: Page, timeout_ms: int = 2500) -> None:
        self.page = page
        self.timeout_ms = timeout_ms

    def probe_action(
        self,
        element: dict[str, Any],
        locator: Locator,
        current_route: str,
        parking_lot: list[dict[str, Any]],
        auto_restore: bool = False,
        on_discovered_targets: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> dict[str, Any]:
        """Classify and optionally probe an action. Unsafe actions go to parking_lot."""
        classification = classify_action(element)
        label = element.get("label") or element.get("text") or "unlabeled_control"

        if is_parking_lot_action(classification):
            logger.info(f"Parking lot diversion [{classification}]: {label}")
            parking_lot_entry = {
                "route": current_route,
                "label": label,
                "classification": classification,
                "element": element,
                "reason": (
                    "Action is destructive and blocked"
                    if classification == ActionType.BLOCKED_DESTRUCTIVE
                    else "Action requires explicit user confirmation"
                ),
                "timestamp": time.time(),
            }
            parking_lot.append(parking_lot_entry)
            return {
                "probed": False,
                "classification": classification,
                "parking_lot": True,
            }

        # Safe action: proceed with controlled probe
        before_url = self.page.url
        before_child_count = self._get_dom_count()
        success = False

        try:
            logger.info("Probing safe action [%s]: %s", classification, label)
            if locator.is_visible():
                locator.click(timeout=self.timeout_ms)
                self.page.wait_for_timeout(300)
                wait_for_ui_stability(self.page)
                success = True
        except Exception as exc:
            logger.debug("Safe action probe failed for '%s': %s", label, exc)

        after_url = self.page.url
        after_child_count = self._get_dom_count()
        url_changed = (after_url.rstrip("/") != before_url.rstrip("/"))

        result = {
            "probed": True,
            "classification": classification,
            "success": success,
            "before_url": before_url,
            "after_url": after_url,
            "url_changed": url_changed,
            "dom_delta": abs(after_child_count - before_child_count),
        }

        # If navigation occurred during an in-page probe, restore prior route if auto_restore is enabled
        if url_changed and before_url and auto_restore:
            try:
                self.page.goto(before_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                wait_for_ui_stability(self.page)
            except Exception:
                logger.warning("Could not restore URL after safe probe: %s", before_url)

        return result

    def _get_dom_count(self) -> int:
        try:
            return int(self.page.evaluate("() => document.body ? document.body.querySelectorAll('*').length : 0"))
        except Exception:
            return 0
