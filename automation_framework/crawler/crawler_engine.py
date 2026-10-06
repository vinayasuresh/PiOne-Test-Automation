import time
import json
from typing import Any, Callable
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import (
    ENABLE_DEEP_EXPLORATION,
    MENU_EXPANSION_TIMEOUT,
    ROUTE_NAVIGATION_TIMEOUT,
    ROUTE_LOAD_TIMEOUT,
    ROUTE_SCAN_TIMEOUT,
)
from automation_framework.crawler.hidden_navigation_explorer import explore_hidden_navigation
from automation_framework.crawler.interactive_capture import (
    events_to_interactions,
    extract_events,
    inject_capture,
    interactions_to_flow,
    interactions_to_targets,
    wait_for_user,
)
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.menu_detector import detect_menus
from automation_framework.crawler.route_tracker import RouteTracker
from automation_framework.crawler.shell_detector import detect_application_shell
from automation_framework.crawler.ui_wait_engine import wait_for_ui_stability
from automation_framework.crawler.url_filter import is_internal_url, is_valid_url
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
        checkpoint_path: str | Path | None = None,
        resume_from_checkpoint: bool = False,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.page = page
        self.page.set_default_timeout(ROUTE_SCAN_TIMEOUT)
        self.page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)
        self.base_url = base_url
        self.credentials = credentials
        self.interactive_mode = interactive_mode
        self.interactive_timeout = interactive_timeout
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.checkpoint_loaded = False
        self.event_callback = event_callback
        self.route_tracker = RouteTracker()
        self.page_intelligence: dict[str, dict[str, Any]] = {}
        self.feature_routes: dict[str, dict[str, Any]] = {}
        self.menu_hierarchy: dict[str, Any] = {}
        self.manual_interactions: list[dict[str, Any]] = []
        self.interaction_flow: list[dict[str, Any]] = []
        # Kept for compatibility with the intelligence engine. It is not used
        # to suppress targets across routes: identity includes the route.
        self._global_seen: set[tuple[str, str]] = set()
        self.route_validations: dict[str, dict[str, Any]] = {}
        self._api_responses: list[dict[str, Any]] = []
        self._route_interactions: dict[str, dict[str, int]] = {}
        self._route_deadline = 0.0
        self._deep_exploration_active = False
        self.page.on("response", self._record_api_response)
        self.page.route("**/*", self._enforce_document_scope)
        self._seed_loaded_api_responses()
        if resume_from_checkpoint:
            self._load_checkpoint()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl(self, start_url: str | None = None) -> dict[str, object]:
        self._ensure_authenticated()
        # Include same-app API activity that occurred during the service's
        # initial navigation before this engine attached its response listener.
        landing_api_marker = 0
        wait_for_ui_stability(self.page)

        application_shell = detect_application_shell(self.page, event_callback=self._emit_event)
        self.menu_hierarchy = application_shell.get("global_navigation", {})

        # Step 1: scan the landing page itself once. Use the page title (or URL
        # path tail) instead of a hardcoded "home" so the menu_name reflects
        # the actual landing screen.
        self._scan_route(start_url or self.page.url, menu_name=self._landing_label(),
                         expected_url=start_url or self.page.url, api_marker=landing_api_marker)

        # Step 2: build a flat, deduplicated menu list.
        menu_items = self._build_menu_list(application_shell)
        logger.info(f"Discovered {len(menu_items)} menu items to explore")

        # Step 3: visit each menu item exactly once.
        for menu_item in menu_items:
            self._visit_menu_item(menu_item)

        self._save_checkpoint()

        return self._result_snapshot()

    def replay_route(
        self, url: str, menu_name: str = "route_replay", api_marker: int | None = None,
    ) -> dict[str, object]:
        """Force a single in-scope route through the current scanner and checkpoint it."""
        if not is_valid_url(url, self.base_url):
            raise ValueError(f"Refusing to replay route outside requested app scope: {url}")
        normalized = self.route_tracker.normalize_url(url)
        self.page_intelligence.pop(normalized, None)
        self.route_validations.pop(normalized, None)
        self.route_tracker.unmark_visited(normalized)
        marker = len(self._api_responses) if api_marker is None else api_marker
        self._scan_route(url, menu_name=menu_name, expected_url=url, api_marker=marker)
        self._save_checkpoint()
        return self._result_snapshot()

    def _result_snapshot(self) -> dict[str, object]:
        return {
            "page_intelligence": self.page_intelligence,
            "manual_interactions": self.manual_interactions,
            "interaction_flow": self.interaction_flow,
            "component_registry": build_component_registry(self.page_intelligence),
            "feature_routes": self.feature_routes,
            "routes": sorted(self.route_tracker.get_visited_routes()),
            "route_validations": self.route_validations,
            "checkpoint_id": (self.checkpoint_path.stem.removeprefix("checkpoint_")
                              if self.checkpoint_path else None),
            "route_namespace": self._checkpoint_namespace(),
            "menu_hierarchy": self.menu_hierarchy,
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
                    if menu_type == "overlay_menus" and not href:
                        # Dropdown options are UI data, not route candidates.
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
                            "selector": item.get("selector", ""),
                            "hierarchy_level": item.get("hierarchy_level", 0),
                        }
                    )
                    if resolved_url:
                        self.feature_routes[resolved_url] = {
                            "label": label,
                            "source": menu_type,
                            "hierarchy_level": item.get("hierarchy_level", 0),
                        }

        return menu_items

    # ------------------------------------------------------------------
    # Per-menu visit
    # ------------------------------------------------------------------

    def _visit_menu_item(self, menu_item: dict[str, str]) -> None:
        url = menu_item.get("url", "")
        label = menu_item["label"]
        container_selector = menu_item.get("container_selector", "")
        selector = menu_item.get("selector", "")

        if url and self.route_tracker.is_visited(url):
            logger.info(f"Skipping already-visited menu route: {label} ({url})")
            return

        logger.info(f"Visiting menu: {label} -> {url or '(click)'}")

        for attempt in range(2):
            try:
                api_marker = len(self._api_responses)
                navigated = self._click_menu_item(label, container_selector, selector)
                if not navigated and url:
                    self._navigate(url)
                self._ensure_authenticated()
                wait_for_ui_stability(self.page)
                route_key = self.route_tracker.normalize_url(url or self.page.url)
                if selector:
                    interaction = self._route_interactions.setdefault(route_key, {"attempted": 0, "succeeded": 0})
                    interaction["attempted"] += 1
                    interaction["succeeded"] += int(navigated)
                break
            except Exception:
                if attempt == 1:
                    logger.exception(f"Failed to navigate to menu after retry: {label}")
                    if url and is_valid_url(url, self.base_url):
                        route = self.route_tracker.normalize_url(url)
                        self.route_tracker.mark_visited(route)
                        self.route_validations[route] = {
                            "status": "NON-WORKING", "score": 0,
                            "expected_url": route, "actual_url": self.route_tracker.normalize_url(self.page.url),
                            "redirected": True, "dom_stable": False,
                            "api_response_present": False, "failed_api_responses": 0,
                            "target_count": 0, "interaction_success_rate": 0.0,
                        }
                        self.page_intelligence[route] = {
                            "route": route, "menu_name": label, "purpose": "",
                            "framework": "unknown", "sections": [],
                            "automation_targets": [], "scan_error": "Navigation failed after retry",
                            "route_validation": self.route_validations[route],
                        }
                        self._emit_event("route_failed", {
                            "route": route, "reason": "navigation_failed", "menu_name": label,
                        })
                        self._save_checkpoint()
                    return
                logger.warning(f"Navigation failed for '{label}', retrying once")

        # Capture URL AFTER navigation/click so SPAs that update history land correctly.
        self._scan_route(self.page.url, menu_name=label, expected_url=url or self.page.url,
                         api_marker=api_marker)

    def _click_menu_item(self, label: str, container_selector: str, selector: str = "") -> bool:
        """Click the menu item by visible text within its container.

        Returns True if a click was performed and a navigation/state change
        was observed; False if the item could not be located/clicked.
        """
        if not container_selector and not selector:
            return False

        try:
            container = self.page.locator(container_selector).first if container_selector else self.page.locator("body")
            item = self.page.locator(selector).first if selector else container.get_by_text(label, exact=True).first
            if not item.is_visible():
                item = container.get_by_role("link", name=label).first
            if not item.is_visible():
                return False

            previous_url = self.page.url
            previous_count = int(self.page.evaluate("() => document.body ? document.body.querySelectorAll('*').length : 0"))
            item.click(timeout=ROUTE_NAVIGATION_TIMEOUT)

            # Wait for either a URL change or network idle — 2 s is enough for most SPAs.
            try:
                self.page.wait_for_url(lambda new_url: new_url != previous_url, timeout=2000)
            except PlaywrightTimeoutError:
                pass
            try:
                self.page.wait_for_load_state("networkidle", timeout=4000)
            except PlaywrightTimeoutError:
                logger.warning(f"Network idle timeout after clicking menu: {label}")
            current_count = int(self.page.evaluate("() => document.body ? document.body.querySelectorAll('*').length : 0"))
            return self.page.url != previous_url or current_count != previous_count
        except Exception:
            logger.warning(f"Click failed for menu '{label}', will fall back to URL navigation")
            return False

    def _scan_route(
        self, url: str, menu_name: str, expected_url: str | None = None,
        api_marker: int | None = None,
    ) -> None:
        normalized = self.route_tracker.normalize_url(url)

        if not is_valid_url(normalized, self.base_url):
            logger.warning(f"Skipping route outside requested application scope: {normalized}")
            self._emit_event("route_failed", {"route": normalized, "reason": "outside_app_scope"})
            return

        if normalized in self.page_intelligence:
            logger.info(f"Route already scanned, skipping: {normalized}")
            return

        if self.route_tracker.is_visited(normalized):
            return

        self.route_tracker.mark_visited(normalized)
        self._emit_event("route_started", {"route": normalized, "menu_name": menu_name})
        _scan_start = time.monotonic()
        self._route_deadline = _scan_start + ROUTE_SCAN_TIMEOUT / 1000
        self.page.set_default_timeout(ROUTE_SCAN_TIMEOUT)
        api_marker = len(self._api_responses) if api_marker is None else api_marker
        logger.info(f"Scanning route: {normalized} (menu: {menu_name})")

        # Behave like a real user: wait, observe, scroll, then scan.
        stable = self._stabilize_page(normalized)

        try:
            remaining = self._remaining_route_timeout()
            if time.monotonic() >= self._route_deadline:
                raise TimeoutError(f"Route scan budget exceeded before extraction: {normalized}")
            self.page.set_default_timeout(remaining)
            route_doc = build_ui_intelligence(
                self.page,
                feature_name=menu_name,
                menu_name=menu_name,
                global_seen=self._global_seen,
                deadline=self._route_deadline,
            )
        except Exception as exc:
            # A broken page must not erase previous checkpoints or abort the
            # route queue. Store a deterministic failed node and continue.
            logger.exception(f"Route scan failed: {normalized}")
            route_doc = {
                "route": self.page.url,
                "menu_name": menu_name,
                "purpose": "",
                "framework": "unknown",
                "sections": [],
                "automation_targets": [],
                "scan_error": f"{type(exc).__name__}: {exc}",
            }
            self._emit_event("route_failed", {
                "route": normalized, "reason": route_doc["scan_error"],
                "menu_name": menu_name,
            })
        route_doc["auto_detected_elements"] = list(route_doc.get("automation_targets", []))

        # Empty-page handling: if nothing was detected, give the page a short
        # extra wait and retry once. Replace fixed sleep with a load-state check.
        if not route_doc["automation_targets"]:
            logger.info(f"Empty scan on {normalized}, retrying after domcontentloaded")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            except PlaywrightTimeoutError:
                pass
            try:
                remaining = self._remaining_route_timeout()
                if time.monotonic() >= self._route_deadline:
                    raise TimeoutError(f"Route scan budget exceeded before retry: {normalized}")
                self.page.set_default_timeout(remaining)
                route_doc = build_ui_intelligence(
                    self.page,
                    feature_name=menu_name,
                    menu_name=menu_name,
                    global_seen=self._global_seen,
                    deadline=self._route_deadline,
                )
                route_doc["auto_detected_elements"] = list(route_doc.get("automation_targets", []))
            except Exception:
                logger.exception(f"Retry scan failed: {normalized}")
            if not route_doc["automation_targets"]:
                logger.warning(
                    f"low confidence scan: no automation targets detected on {normalized}"
                )

        self.page_intelligence[normalized] = route_doc
        self.route_validations[normalized] = self._validate_route(
            expected_url=self.route_tracker.normalize_url(expected_url or normalized),
            stable=stable,
            target_count=len(route_doc["automation_targets"]),
            api_marker=api_marker,
            scan_error=bool(route_doc.get("scan_error")),
        )
        route_doc["route_validation"] = self.route_validations[normalized]
        for target in route_doc.get("automation_targets", []):
            self._emit_event("element_found", {
                "route": normalized, "id": target.get("id", ""),
                "element_type": target.get("type", ""), "label": target.get("label", ""),
            })
        self._save_checkpoint()

        # Interactive learning: pause and let the user demonstrate flows on
        # the live page. Captured events are merged into the route doc so
        # the response includes both passive AND user-driven components.
        if self.interactive_mode:
            self._capture_interactions(normalized, route_doc)
            validation = self.route_validations[normalized]
            self.route_validations[normalized] = self._rescore_route(
                validation, len(route_doc["automation_targets"])
            )
            route_doc["route_validation"] = self.route_validations[normalized]

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
        self._save_checkpoint()
        if not route_doc.get("scan_error"):
            self._emit_event("route_completed", {
                "route": normalized,
                "status": self.route_validations[normalized]["status"],
                "score": self.route_validations[normalized]["score"],
                "element_count": len(route_doc.get("automation_targets", [])),
            })

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if not self.event_callback:
            return
        try:
            self.event_callback(event_type, data)
        except Exception:
            logger.debug("Scan event callback failed for %s", event_type)

    # ------------------------------------------------------------------
    # Page stabilization
    # ------------------------------------------------------------------

    def _stabilize_page(self, route_label: str) -> bool:
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
            self.page.wait_for_load_state("networkidle", timeout=self._remaining_route_timeout(8000))
        except PlaywrightTimeoutError:
            logger.debug(f"networkidle timeout: {route_label}")

        # 2. Real-user settle window (mandatory).
        self.page.wait_for_timeout(min(3000, self._remaining_route_timeout(3000)))

        # 3. Body must be visible — fail fast if the page never rendered.
        body_visible = False
        try:
            self.page.wait_for_selector("body", timeout=self._remaining_route_timeout(5000), state="visible")
            body_visible = True
        except PlaywrightTimeoutError:
            logger.warning(f"body never became visible: {route_label}")

        # 4. Opportunistic waits for interactive content. Each is best-effort:
        #    if a selector never appears the page legitimately may not have it.
        for selector in (
            "table, [role='table'], mat-table",
            "button, [role='button'], mat-button, [mat-button], [mat-raised-button]",
            "input, textarea, [contenteditable='true'], [role='textbox'], mat-form-field",
            "[role='combobox'], mat-select, [aria-haspopup='listbox']",
        ):
            try:
                self.page.wait_for_selector(selector, timeout=self._remaining_route_timeout(2500), state="visible")
            except PlaywrightTimeoutError:
                logger.debug(f"no '{selector}' visible on {route_label}")

        # 5. Simulate user scroll to surface lazy/virtualized rows.
        try:
            self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(min(1500, self._remaining_route_timeout(1500)))
            self.page.mouse.wheel(0, 2000)
            self.page.wait_for_timeout(min(500, self._remaining_route_timeout(500)))
            self.page.mouse.wheel(0, -2000)
            self.page.evaluate("() => window.scrollTo(0, 0)")
            self.page.wait_for_timeout(min(400, self._remaining_route_timeout(400)))
        except Exception:
            logger.debug(f"scroll simulation failed: {route_label}")
        dom_stable = False
        try:
            self.page.wait_for_function(
                """() => new Promise(resolve => {
                  let timer;
                  const observer = new MutationObserver(() => {
                    clearTimeout(timer);
                    timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 250);
                  });
                  observer.observe(document.body, {childList:true, subtree:true, attributes:true});
                  timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 250);
                })""",
                timeout=self._remaining_route_timeout(1500),
            )
            dom_stable = True
        except PlaywrightTimeoutError:
            logger.warning(f"DOM did not settle within scan window: {route_label}")
        return body_visible and dom_stable

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
        flow = interactions_to_flow(interactions)

        if not interactions:
            logger.info(f"Interactive mode: no interactions captured on {normalized} (passive only)")
            route_doc["interactions"] = []
            route_doc["interaction_flow"] = []
            return

        logger.info(
            f"Interactive mode: {len(interactions)} interactions captured on {normalized}"
        )
        route_doc["interactions"] = interactions
        route_doc["interaction_flow"] = flow
        user_targets = interactions_to_targets(
            interactions,
            framework=route_doc.get("framework", "unknown"),
        )
        for index, target in enumerate(user_targets, start=1):
            target["element_xpath"] = self._xpath_for_selector(target, index)
            target["element_role"] = target.get("element_role") or self._role_for_target(target)
            target["element_index"] = int(target.get("element_index") or index)
            target["element_identity"] = "|".join((
                normalized, target["element_xpath"], target["element_role"],
                target.get("label", ""), str(target["element_index"]),
            ))
            target["id"] = target["element_identity"]
            target["hierarchy_level"] = 2
            target["interaction_probability"] = 1.0
            target["interaction_probability_method"] = "user_confirmed_interaction"
        route_doc["user_confirmed_elements"] = user_targets
        existing = {target.get("element_identity") for target in route_doc["automation_targets"]}
        for target in user_targets:
            identity = target.get("element_identity")
            if identity not in existing:
                route_doc["automation_targets"].append(target)
                existing.add(identity)
                self._emit_event("element_found", {
                    "route": normalized, "id": identity,
                    "element_type": target.get("type", ""), "label": target.get("label", ""),
                })
        route_doc["sections"] = _recount_sections(route_doc["automation_targets"])
        self.manual_interactions.extend(
            {**interaction, "route": normalized} for interaction in interactions
        )
        self.interaction_flow.extend({**step, "route": normalized} for step in flow)

    def _explore_hidden_state(self, menu_name: str) -> None:
        if self._deep_exploration_active:
            return
        starting_url = self.page.url

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
                    deadline=self._route_deadline,
                )
                if extra["automation_targets"]:
                    existing = self.page_intelligence[normalized]
                    known = {target.get("element_identity") for target in existing["automation_targets"]}
                    for target in extra["automation_targets"]:
                        identity = target.get("element_identity")
                        if identity not in known:
                            existing["automation_targets"].append(target)
                            known.add(identity)
                            self._emit_event("element_found", {
                                "route": normalized, "id": target.get("id", ""),
                                "element_type": target.get("type", ""), "label": target.get("label", ""),
                            })
                    # Recompute sections from the merged target list.
                    existing["sections"] = _recount_sections(existing["automation_targets"])
                    existing["route_validation"] = self._rescore_route(
                        self.route_validations[normalized], len(existing["automation_targets"])
                    )
                    self.route_validations[normalized] = existing["route_validation"]
                    self._save_checkpoint()
                return

            # New URL surfaced via a hidden trigger: treat as a fresh route.
            self._scan_route(current_url, menu_name=sub_menu_name)

        try:
            self._deep_exploration_active = True
            explore_hidden_navigation(self.page, on_state_change)
        except Exception:
            logger.exception("Deep exploration failed; continuing without it")
        finally:
            self._deep_exploration_active = False
            if self.page.url != starting_url and is_valid_url(starting_url, self.base_url):
                try:
                    self._navigate(starting_url)
                    wait_for_ui_stability(self.page)
                except Exception:
                    logger.warning("Could not restore the route after hidden-state exploration")

    def _xpath_for_selector(self, target: dict[str, Any], fallback_index: int) -> str:
        selector = (target.get("selector", {}).get("primary", {}) or {}).get("value", "")
        if selector:
            try:
                return str(self.page.locator(selector).first.evaluate(
                    """el => {
                      const parts=[]; let node=el;
                      while(node && node.nodeType===1 && node!==document.body) {
                        const siblings=Array.from(node.parentElement.children).filter(x=>x.tagName===node.tagName);
                        parts.unshift(`${node.tagName.toLowerCase()}[${siblings.indexOf(node)+1}]`);
                        node=node.parentElement;
                      }
                      return `/html/body/${parts.join('/')}`;
                    }"""
                ))
            except Exception:
                pass
        # Preserve the unresolved state instead of presenting a synthetic path
        # as a browser XPath; the per-route event index still distinguishes it.
        return ""

    @staticmethod
    def _role_for_target(target: dict[str, Any]) -> str:
        element_type = target.get("type", "")
        return {"button": "button", "input": "textbox", "dropdown": "combobox",
                "table": "table"}.get(element_type, "")

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
        if not is_valid_url(url, self.base_url):
            raise ValueError(f"Refusing to navigate outside requested app scope: {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)

    def _enforce_document_scope(self, route: Any) -> None:
        request = route.request
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            if not is_valid_url(request.url, self.base_url):
                self._emit_event("route_failed", {
                    "route": request.url, "reason": "navigation_blocked_outside_app_scope",
                })
                route.abort()
                return
        route.continue_()

    def _remaining_route_timeout(self, preferred_ms: int = ROUTE_SCAN_TIMEOUT) -> int:
        if not self._route_deadline:
            return preferred_ms
        return max(1, min(preferred_ms, int((self._route_deadline - time.monotonic()) * 1000)))

    def _record_api_response(self, response: Any) -> None:
        """Record only same-app XHR/fetch responses for route health."""
        try:
            if response.request.resource_type not in {"fetch", "xhr"}:
                return
            if not is_internal_url(response.url, self.base_url):
                return
            self._api_responses.append({"url": response.url, "status": response.status})
        except Exception:
            return

    def _seed_loaded_api_responses(self) -> None:
        try:
            entries = self.page.evaluate(
                """() => performance.getEntriesByType('resource')
                  .filter(e => ['fetch', 'xmlhttprequest'].includes(e.initiatorType))
                  .map(e => ({url:e.name, status:e.responseStatus || 0}))"""
            )
            self._api_responses.extend(
                {"url": item["url"], "status": int(item.get("status", 0))}
                for item in entries if is_internal_url(item.get("url", ""), self.base_url)
            )
        except Exception:
            return

    def _validate_route(
        self, expected_url: str, stable: bool, target_count: int,
        api_marker: int, scan_error: bool = False,
    ) -> dict[str, Any]:
        """Classify the currently displayed route without re-navigating it."""
        actual_url = self.route_tracker.normalize_url(self.page.url)
        responses = self._api_responses[api_marker:]
        failed_api = [item for item in responses if int(item["status"]) >= 400]
        api_present = bool(responses)
        redirected = actual_url != expected_url
        interaction = self._route_interactions.get(expected_url, {"attempted": 0, "succeeded": 0})
        interaction_rate = (
            interaction["succeeded"] / interaction["attempted"]
            if interaction["attempted"] else 0.0
        )
        page_load_success, document_status = self._page_load_health()
        components = {
            "dom_stability": 25 if stable else 0,
            "api_response": 20 if api_present and not failed_api else (8 if api_present else 0),
            "redirect_consistency": 20 if not redirected else 0,
            "interaction_success": round(15 * interaction_rate),
            "element_detection": 20 if target_count >= 5 else (12 if target_count else 0),
        }
        score = sum(components.values())
        if scan_error or not stable or not page_load_success or self._is_login_page() or score < 40:
            status = "NON-WORKING"
        elif score < 70 or failed_api or redirected or target_count == 0:
            status = "PARTIAL"
        else:
            status = "WORKING"
        return {
            "status": status,
            "score": score,
            "score_components": components,
            "expected_url": expected_url,
            "actual_url": actual_url,
            "redirected": redirected,
            "dom_stable": stable,
            "page_load_success": page_load_success,
            "document_status": document_status,
            "api_response_present": api_present,
            "failed_api_responses": len(failed_api),
            "target_count": target_count,
            "interaction_success_rate": round(interaction_rate, 3),
        }

    def _rescore_route(self, validation: dict[str, Any], target_count: int) -> dict[str, Any]:
        updated = dict(validation)
        components = dict(updated.get("score_components", {}))
        components["element_detection"] = 20 if target_count >= 5 else (12 if target_count else 0)
        updated["target_count"] = target_count
        updated["score_components"] = components
        updated["score"] = sum(components.values())
        if (not updated.get("dom_stable") or not updated.get("page_load_success")
                or self._is_login_page() or updated["score"] < 40):
            updated["status"] = "NON-WORKING"
        elif (updated["score"] < 70 or updated.get("redirected")
              or updated.get("failed_api_responses") or target_count == 0):
            updated["status"] = "PARTIAL"
        else:
            updated["status"] = "WORKING"
        return updated

    def _page_load_health(self) -> tuple[bool, int]:
        try:
            status = int(self.page.evaluate(
                "() => { const n=performance.getEntriesByType('navigation')[0]; "
                "return n && n.responseStatus ? n.responseStatus : 0; }"
            ) or 0)
            ready = self.page.evaluate("() => document.readyState") in {"interactive", "complete"}
            body = self.page.locator("body").is_visible()
            return ready and body and (status == 0 or 200 <= status < 400), status
        except Exception:
            return False, 0

    def _checkpoint_namespace(self) -> str:
        """Bind a checkpoint to this exact requested origin and path prefix."""
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        path = "/" + "/".join(part for part in parsed.path.split("/") if part)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path.rstrip('/') or '/'}"

    def _load_checkpoint(self) -> None:
        if not self.checkpoint_path or not self.checkpoint_path.exists():
            return
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if data.get("namespace") != self._checkpoint_namespace():
                raise ValueError("Checkpoint app namespace does not match requested URL")
            self.checkpoint_loaded = True
            self.page_intelligence = {
                route: doc for route, doc in data.get("page_intelligence", {}).items()
                if is_valid_url(route, self.base_url)
            }
            self.route_validations = {
                route: item for route, item in data.get("route_validations", {}).items()
                if is_valid_url(route, self.base_url)
            }
            self.feature_routes = {
                route: item for route, item in data.get("feature_routes", {}).items()
                if is_valid_url(route, self.base_url)
            }
            # Rebuilt from the live app shell on each resumed scan; never reuse
            # menu routes from a historical scan snapshot.
            self.menu_hierarchy = {}
            failed_routes = {
                route for route in data.get("failed_routes", [])
                if is_valid_url(route, self.base_url)
            }
            for route in failed_routes:
                self.page_intelligence.pop(route, None)
                self.route_validations.pop(route, None)
            for route in data.get("visited_routes", []):
                if is_valid_url(route, self.base_url) and route not in failed_routes:
                    self.route_tracker.mark_visited(route)
            logger.info("Resumed scan checkpoint with %d routes", len(self.page_intelligence))
        except Exception:
            logger.exception("Unable to load scan checkpoint; starting a fresh scan")
            self.page_intelligence = {}
            self.route_validations = {}
            self.feature_routes = {}
            self.menu_hierarchy = {}
            self.route_tracker = RouteTracker()

    def _save_checkpoint(self) -> None:
        if not self.checkpoint_path:
            return
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            # Do not persist interactive event values, which may contain secrets.
            page_snapshot = {}
            for route, doc in self.page_intelligence.items():
                safe_doc = dict(doc)
                safe_doc.pop("interactions", None)
                safe_doc.pop("interaction_flow", None)
                page_snapshot[route] = safe_doc
            payload = {
                "namespace": self._checkpoint_namespace(),
                "visited_routes": sorted(self.route_tracker.get_visited_routes()),
                "failed_routes": sorted(
                    route for route, item in self.route_validations.items()
                    if item.get("status") == "NON-WORKING"
                ),
                "partial_routes": sorted(
                    route for route, item in self.route_validations.items()
                    if item.get("status") == "PARTIAL"
                ),
                "route_validations": self.route_validations,
                "page_intelligence": page_snapshot,
                "element_map_snapshot": [
                    {"route": route, **target}
                    for route, doc in page_snapshot.items()
                    for target in doc.get("automation_targets", [])
                ],
                "feature_routes": self.feature_routes,
                "menu_hierarchy": self.menu_hierarchy,
            }
            temporary = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.checkpoint_path)
        except Exception:
            logger.exception("Could not persist route checkpoint")

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
        from urllib.parse import unquote, urlparse
        login_url_indicators = {"login", "sign-in", "signin", "auth", "authenticate", "sso"}
        path_parts = {part.casefold() for part in unquote(urlparse(self.page.url).path).split("/") if part}
        if path_parts & login_url_indicators:
            return True

        # Fallback: a visible password input is a strong login-page signal.
        try:
            return self.page.locator('input[type="password"]').first.is_visible()
        except Exception:
            return False

    def _normalize(self, url: str) -> str:
        # Resolve a relative menu href exactly as the browser does from the
        # currently loaded route; resolving it against a deep base URL can
        # incorrectly climb out of the requested application path.
        return self.route_tracker.normalize_url(urljoin(self.page.url or self.base_url, url))
