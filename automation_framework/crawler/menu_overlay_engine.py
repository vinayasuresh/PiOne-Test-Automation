"""MenuOverlayEngine: Safe opening, discovery, and restoration of UI overlays.

Supports:
- Angular Material (mat-menu, mat-select, .cdk-overlay-container)
- PrimeNG (.p-menu, .p-dropdown-panel, .p-overlaypanel)
- ng-select (.ng-dropdown-panel)
- React / Tailwind / Radix UI ([data-radix-popper-content-wrapper], [role="menu"])
- Native dropdowns and custom expandable panels
- Detects overlays rendered outside the trigger's DOM hierarchy (attached to <body>)
- Clean state restoration (Escape or outside click) after probing
"""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.utils.logger import logger


OVERLAY_CONTAINER_SELECTORS = (
    ".cdk-overlay-container",
    ".p-dropdown-panel",
    ".p-menu",
    ".p-overlaypanel",
    ".ng-dropdown-panel",
    "[role='menu']",
    "[role='listbox']",
    "[data-radix-popper-content-wrapper]",
    ".dropdown-menu.show",
)


class MenuOverlayEngine:
    """Safely opens menu and dropdown overlays across UI frameworks."""

    def __init__(self, page: Page, timeout_ms: int = 2000) -> None:
        self.page = page
        self.timeout_ms = timeout_ms

    def safely_open_and_capture(
        self,
        trigger: Locator,
        interaction_method: str = "click",
    ) -> list[dict[str, Any]]:
        """Open an overlay, capture visible menu/dropdown items, and close it cleanly."""
        discovered_items: list[dict[str, Any]] = []

        try:
            if not trigger.is_visible():
                return []

            # 1. Trigger the overlay
            if interaction_method == "hover":
                trigger.hover(timeout=self.timeout_ms)
            elif interaction_method == "keyboard":
                trigger.focus(timeout=self.timeout_ms)
                self.page.keyboard.press("ArrowDown")
            else:
                trigger.click(timeout=self.timeout_ms)

            self.page.wait_for_timeout(250)

            # 2. Inspect active overlays anywhere in document (including <body> portals)
            for sel in OVERLAY_CONTAINER_SELECTORS:
                container = self.page.locator(sel).first
                if container.is_visible():
                    items = container.locator(
                        "a[href], button, [role='menuitem'], [role='option'], .p-menuitem-link"
                    ).evaluate_all("""els => els.map(el => ({
                        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                        href: el.getAttribute('href') || '',
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        visible: el.offsetWidth > 0 && el.offsetHeight > 0
                    }))""")

                    for item in items:
                        if item.get("visible") and item.get("text"):
                            discovered_items.append(item)
                    break

        except Exception as exc:
            logger.debug("Overlay capture encountered non-fatal error: %s", exc)
        finally:
            # 3. Clean restoration: close overlay with Escape so page isn't obscured
            self._close_overlay()

        return discovered_items

    def _close_overlay(self) -> None:
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(100)
        except Exception:
            pass
