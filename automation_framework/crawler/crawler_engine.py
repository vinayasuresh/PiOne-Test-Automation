import time
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import ENABLE_DEEP_EXPLORATION
from automation_framework.crawler.hidden_navigation_explorer import explore_hidden_navigation
from automation_framework.crawler.interactive_capture import (
    events_to_interactions,
    extract_events,
    inject_capture,
    wait_for_user,
)
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.menu_detector import detect_menus
from automation_framework.crawler.route_tracker import RouteTracker
from automation_framework.crawler.shell_detector import detect_application_shell
from automation_framework.crawler.ui_wait_engine import wait_for_ui_stability
from automation_framework.crawler.url_filter import is_valid_url
from automation_framework.engine.ui_intelligence_engine import (
    CATEGORY_ORDER,
    build_component_registry,
    build_ui_intelligence,
)
from automation_framework.utils.logger import logger


def _recount_sections(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for target in targets:
        category = target.get("category", "")
        if category:
            counts[category] = counts.get(category, 0) + 1
    return [
        {"name": category, "count": counts[category]}
        for category in CATEGORY_ORDER
        if counts.get(category, 0) > 0
    ]


class CrawlerEngine:
    """Structured, menu-driven UI exploration engine.

    Flow:
        1. Detect application shell + menus
        2. Build deduplicated menu list (sidebar + top nav)
        3. For each menu item: navigate -> wait for stability -> scan -> store
        4. Each route is scanned exactly once (RouteTracker enforces this)
    """

    # Minimum on-page time per route (seconds). Ensures slow-rendering UI has
    # time to settle and prevents instant DOM snapshots.
    _MIN_SCAN_SECONDS: float = 2.5

    def __init__(
        self,
        page: Page,
        base_url: str,
        credentials: tuple[str, str] | None = None,
        interactive_mode: bool = False,
        interactive_timeout: int = 45,
    ) -> None:
        self.page = page
        self.base_url = base_url
        self.credentials = credentials
        self.interactive_mode = interactive_mode
        self.interactive_timeout = interactive_timeout
        self.route_tracker = RouteTracker()
        self.page_intelligence: dict[str, dict[str, Any]] = {}
        self.feature_routes: dict[str, dict[str, str]] = {}
        # Cross-route registry: (label_lower, primary_selector) -> dedup across pages
        self._global_seen: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl(self, start_url: str | None = None) -> dict[str, object]:
        self._ensure_authenticated()
        wait_for_ui_stability(self.page)

        application_shell = detect_application_shell(self.page)

        # Step 1: scan the landing page itself once. Use the page title (or URL
        # path tail) instead of a hardcoded "home" so the menu_name reflects
        # the actual landing screen.
        self._scan_route(start_url or self.page.url, menu_name=self._landing_label())

        # Step 2: build a flat, deduplicated menu list.
        menu_items = self._build_menu_list(application_shell)
        logger.info(f"Discovered {len(menu_items)} menu items to explore")

        # Step 3: visit each menu item exactly once.
        for menu_item in menu_items:
            self._visit_menu_item(menu_item)

        return {
            "page_intelligence": self.page_intelligence,
            "manual_interactions": [],
            "component_registry": build_component_registry(self.page_intelligence),
            "feature_routes": self.feature_routes,
            "routes": sorted(self.route_tracker.get_visited_routes()),
        }

    # ------------------------------------------------------------------
    # Menu discovery
    # ------------------------------------------------------------------

    def _build_menu_list(self, application_shell: dict[str, Any]) -> list[dict[str, str]]:
        """Flat list of {url, label, source, container_selector} with duplicates removed."""
        menu_items: list[dict[str, str]] = []
        seen_keys: set[tuple[str, str]] = set()
        menus = application_shell.get("global_navigation", {})

        for menu_type, menu_groups in menus.items():
            for menu_group in menu_groups:
                container_selector = menu_group.get("selector", "")
                for item in menu_group.get("items", []):
                    label = (item.get("text") or "").strip()
                    href = item.get("href")
                    if not label:
                        continue

                    resolved_url = self._normalize(href) if href else ""
                    if resolved_url and not is_valid_url(resolved_url, self.base_url):
                        continue

                    # Dedup key: prefer URL when known; otherwise (label, container).
                    dedup_key = (resolved_url, "") if resolved_url else (label.lower(), container_selector)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    menu_items.append(
                        {
                            "url": resolved_url,
                            "label": label,
                            "source": menu_type,
                            "container_selector": container_selector,
                        }
                    )
                    if resolved_url:
                        self.feature_routes[resolved_url] = {
                            "label": label,
                            "source": menu_type,
                        }

        return menu_items

    # ------------------------------------------------------------------
    # Per-menu visit
    # ------------------------------------------------------------------

    def _visit_menu_item(self, menu_item: dict[str, str]) -> None:
        url = menu_item.get("url", "")
        label = menu_item["label"]
        container_selector = menu_item.get("container_selector", "")

        if url and self.route_tracker.is_visited(url):
            logger.info(f"Skipping already-visited menu route: {label} ({url})")
            return

        logger.info(f"Visiting menu: {label} -> {url or '(click)'}")

        try:
            navigated = self._click_menu_item(label, container_selector)
            if not navigated and url:
                self._navigate(url)
            self._ensure_authenticated()
            wait_for_ui_stability(self.page)
        except Exception:
            logger.exception(f"Failed to navigate to menu: {label}")
            return

        # Capture URL AFTER navigation/click so SPAs that update history land correctly.
        self._scan_route(self.page.url, menu_name=label)

    def _click_menu_item(self, label: str, container_selector: str) -> bool:
        """Click the menu item by visible text within its container.

        Returns True if a click was performed and a navigation/state change
        was observed; False if the item could not be located/clicked.
        """
        if not container_selector:
            return False

        try:
            container = self.page.locator(container_selector).first
            item = container.get_by_text(label, exact=True).first
            if not item.is_visible():
                item = container.get_by_role("link", name=label).first
            if not item.is_visible():
                return False

            previous_url = self.page.url
            item.click()

            # Wait for either a URL change or network idle — 2 s is enough for most SPAs.
            try:
                self.page.wait_for_url(lambda new_url: new_url != previous_url, timeout=2000)
            except PlaywrightTimeoutError:
                pass
            try:
                self.page.wait_for_load_state("networkidle", timeout=4000)
            except PlaywrightTimeoutError:
                logger.warning(f"Network idle timeout after clicking menu: {label}")
            return True
        except Exception:
            logger.warning(f"Click failed for menu '{label}', will fall back to URL navigation")
            return False

    def _scan_route(self, url: str, menu_name: str) -> None:
        normalized = self.route_tracker.normalize_url(url)

        if normalized in self.page_intelligence:
            logger.info(f"Route already scanned, skipping: {normalized}")
            return

        if self.route_tracker.is_visited(normalized):
            return

        self.route_tracker.mark_visited(normalized)
        _scan_start = time.monotonic()
        logger.info(f"Scanning route: {normalized} (menu: {menu_name})")

        # Behave like a real user: wait, observe, scroll, then scan.
        self._stabilize_page(normalized)

        route_doc = build_ui_intelligence(
            self.page,
            feature_name=menu_name,
            menu_name=menu_name,
            global_seen=self._global_seen,
        )

        # Empty-page handling: if nothing was detected, give the page a short
        # extra wait and retry once. Replace fixed sleep with a load-state check.
        if not route_doc["automation_targets"]:
            logger.info(f"Empty scan on {normalized}, retrying after domcontentloaded")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            except PlaywrightTimeoutError:
                pass
            route_doc = build_ui_intelligence(
                self.page,
                feature_name=menu_name,
                menu_name=menu_name,
                global_seen=self._global_seen,
            )
            if not route_doc["automation_targets"]:
                logger.warning(
                    f"low confidence scan: no automation targets detected on {normalized}"
                )

        self.page_intelligence[normalized] = route_doc

        # Interactive learning: pause and let the user demonstrate flows on
        # the live page. Captured events are merged into the route doc so
        # the response includes both passive AND user-driven components.
        if self.interactive_mode:
            self._capture_interactions(normalized, route_doc)

        # Enforce minimum 2 s on-page time so the user perceives a real scan
        # and any late-arriving widgets had a chance to render.
        elapsed = time.monotonic() - _scan_start
        if elapsed < self._MIN_SCAN_SECONDS:
            self.page.wait_for_timeout(int((self._MIN_SCAN_SECONDS - elapsed) * 1000))

        logger.info(
            f"Scan complete: {normalized} | "
            f"{len(route_doc['automation_targets'])} targets | "
            f"{time.monotonic() - _scan_start:.2f}s"
        )

        # Deep exploration: try expanding hidden navigation (dropdowns, tabs,
        # aria-expanded triggers) and scan any new states discovered.
        if ENABLE_DEEP_EXPLORATION:
            self._explore_hidden_state(menu_name)

    # ------------------------------------------------------------------
    # Page stabilization
    # ------------------------------------------------------------------

    def _stabilize_page(self, route_label: str) -> None:
        """Behave like a real user: wait for network/render, scroll, observe.

        Order:
            1. networkidle (so XHR-driven content has arrived)
            2. 3 s settle window (covers fade-ins, async rerenders)
            3. body must be present
            4. opportunistic waits for table/button/input (best-effort)
            5. scroll wheel + 1 s wait (triggers lazy-load/virtualized lists)
            6. scroll back to top so extraction starts from a stable viewport
        """
        # 1. Network idle.
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            logger.debug(f"networkidle timeout: {route_label}")

        # 2. Real-user settle window (mandatory).
        self.page.wait_for_timeout(3000)

        # 3. Body must be visible — fail fast if the page never rendered.
        try:
            self.page.wait_for_selector("body", timeout=5000, state="visible")
        except PlaywrightTimeoutError:
            logger.warning(f"body never became visible: {route_label}")

        # 4. Opportunistic waits for interactive content. Each is best-effort:
        #    if a selector never appears the page legitimately may not have it.
        for selector in ("table", "button", "input"):
            try:
                self.page.wait_for_selector(selector, timeout=2500, state="visible")
            except PlaywrightTimeoutError:
                logger.debug(f"no '{selector}' visible on {route_label}")

        # 5. Simulate user scroll to surface lazy/virtualized rows.
        try:
            self.page.mouse.wheel(0, 2000)
            self.page.wait_for_timeout(1000)
            self.page.mouse.wheel(0, -2000)
            self.page.wait_for_timeout(400)
        except Exception:
            logger.debug(f"scroll simulation failed: {route_label}")

    # ------------------------------------------------------------------
    # Interactive learning
    # ------------------------------------------------------------------

    def _capture_interactions(self, normalized: str, route_doc: dict[str, Any]) -> None:
        """Inject the recorder, wait for the user, harvest interactions.

        Falls back gracefully: if injection fails or no events are captured
        the route keeps its passive-scan results untouched.
        """
        logger.info(f"Interactive mode: capturing on {normalized}")
        try:
            inject_capture(self.page)
        except Exception:
            logger.exception("Interactive mode: injection failed, falling back to passive scan")
            route_doc["interactions"] = []
            return

        wait_for_user(self.page, timeout_seconds=self.interactive_timeout)
        raw_events = extract_events(self.page)
        interactions = events_to_interactions(raw_events)

        if not interactions:
            logger.info(f"Interactive mode: no interactions captured on {normalized} (passive only)")
            route_doc["interactions"] = []
            return

        logger.info(
            f"Interactive mode: {len(interactions)} interactions captured on {normalized}"
        )
        route_doc["interactions"] = interactions

    def _explore_hidden_state(self, menu_name: str) -> None:
        def on_state_change(trigger_label: str) -> None:
            current_url = self.page.url
            normalized = self.route_tracker.normalize_url(current_url)
            sub_menu_name = f"{menu_name} > {trigger_label}" if trigger_label else menu_name

            if normalized in self.page_intelligence:
                # Same route URL but new visible state: merge new components in
                # using the global_seen registry so duplicates are skipped.
                logger.info(f"Deep exploration scanning sub-state of {normalized}: {sub_menu_name}")
                extra = build_ui_intelligence(
                    self.page,
                    feature_name=sub_menu_name,
                    menu_name=sub_menu_name,
                    global_seen=self._global_seen,
                )
                if extra["automation_targets"]:
                    existing = self.page_intelligence[normalized]
                    existing["automation_targets"].extend(extra["automation_targets"])
                    # Recompute sections from the merged target list.
                    existing["sections"] = _recount_sections(existing["automation_targets"])
                return

            # New URL surfaced via a hidden trigger: treat as a fresh route.
            self._scan_route(current_url, menu_name=sub_menu_name)

        try:
            explore_hidden_navigation(self.page, on_state_change)
        except Exception:
            logger.exception("Deep exploration failed; continuing without it")

    def _landing_label(self) -> str:
        """Best-effort label for the landing page (page title or URL path tail)."""
        try:
            title = self.page.title().strip()
        except Exception:
            title = ""

        if title:
            return title

        path_tail = self.page.url.rstrip("/").rsplit("/", 1)[-1]
        return path_tail or "landing"

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _navigate(self, url: str) -> None:
        # wait_until="domcontentloaded" is fast; the caller's wait_for_ui_stability
        # handles the remaining stabilization (networkidle + DOM settle).
        self.page.goto(url, wait_until="domcontentloaded")

    def _ensure_authenticated(self) -> None:
        if not self._is_login_page():
            return

        if not self.credentials:
            raise RuntimeError("Authentication session lost and no credentials are available")

        logger.warning("Authentication session appears to be lost. Attempting login recovery.")
        username, password = self.credentials
        login(self.page, username, password, self.base_url)

    def _is_login_page(self) -> bool:
        # URL-keyword check: most reliable signal.
        login_url_indicators = ("login", "sign-in", "signin", "auth", "authenticate", "sso")
        if any(indicator in self.page.url.lower() for indicator in login_url_indicators):
            return True

        # Fallback: a visible password input is a strong login-page signal.
        try:
            return self.page.locator('input[type="password"]').first.is_visible()
        except Exception:
            return False

    def _normalize(self, url: str) -> str:
        return self.route_tracker.normalize_url(urljoin(self.base_url, url))
