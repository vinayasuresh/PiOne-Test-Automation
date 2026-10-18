import time
import json
from typing import Any, Callable
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import (
    ENABLE_DEEP_EXPLORATION,
    MENU_EXPANSION_TIMEOUT,
    MAX_DISCOVERED_ROUTES,
    ROUTE_NAVIGATION_TIMEOUT,
    ROUTE_LOAD_TIMEOUT,
    ROUTE_SCAN_TIMEOUT,
)
from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_safe_action,
    is_parking_lot_action,
)
from automation_framework.crawler.interactive_capture import (
    events_to_interactions,
    extract_events,
    inject_capture,
    interactions_to_flow,
    interactions_to_targets,
    wait_for_user,
)
from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError
from automation_framework.crawler.interaction_probe_engine import InteractionProbeEngine
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.menu_detector import detect_menus
from automation_framework.crawler.menu_overlay_engine import MenuOverlayEngine
from automation_framework.crawler.route_graph import RouteGraph
from automation_framework.crawler.route_health_scorer import RouteStatus, score_route
from automation_framework.crawler.route_tracker import RouteTracker
from automation_framework.crawler.session_manager import SessionManager
from automation_framework.crawler.shell_detector import detect_application_shell
from automation_framework.crawler.ui_wait_engine import wait_for_ui_stability
from automation_framework.crawler.url_filter import (
    install_document_scope_guard,
    is_internal_url,
    is_valid_url,
)
from automation_framework.crawler.ui_state_fingerprint import (
    compute_selector_quality_score,
    compute_ui_state_fingerprint,
)
from automation_framework.crawler.viewport_deep_scanner import ViewportDeepScanner
from automation_framework.engine.element_identity_registry import (
    ElementIdentityRegistry,
    compute_element_identity,
)
from automation_framework.engine.ui_intelligence_engine import (
    CATEGORY_ORDER,
    build_component_registry,
    build_ui_intelligence,
)
from automation_framework.storage.checkpoint_store import CheckpointStore, compute_namespace
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
    """Structured, menu-driven UI exploration and intelligence engine.

    Consolidated production flow per route:
        1. Validate application scope
        2. Scan the current route and register element identities
        3. Deep-scroll document and nested scroll containers using ViewportDeepScanner
        4. Re-scan newly revealed DOM states into ElementIdentityRegistry
        5. Detect and safely open overlays using MenuOverlayEngine
        6. Gather candidate actions and classify with ActionClassifier
        7. Send unsafe and unknown actions to parking_lot_actions
        8. Probe safe actions with InteractionProbeEngine
        9. On route change: add edge to RouteGraph, scan child route, save checkpoint,
           restore parent route/state, and continue probing remaining candidates
        10. Score route health using RouteHealthScorer.score_route()
        11. Handle manual-assist checkpoints (Confirm & Continue vs End Scan & Save)
        12. Export comprehensive artifacts (JSON, YAML, Excel, Markdown)
    """

    _MIN_SCAN_SECONDS: float = 2.0

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
        self.checkpoint_store = CheckpointStore(self.checkpoint_path) if self.checkpoint_path else None
        self.checkpoint_loaded = False
        self.event_callback = event_callback
        self.route_tracker = RouteTracker()
        self.page_intelligence: dict[str, dict[str, Any]] = {}
        self.feature_routes: dict[str, dict[str, Any]] = {}
        self.menu_hierarchy: dict[str, Any] = {}
        self.manual_interactions: list[dict[str, Any]] = []
        self.interaction_flow: list[dict[str, Any]] = []
        self._global_seen: set[tuple[str, str]] = set()
        self.route_validations: dict[str, dict[str, Any]] = {}
        self._api_responses: list[dict[str, Any]] = []
        self._route_interactions: dict[str, dict[str, int]] = {}
        self._route_deadline = 0.0
        self._deep_exploration_active = False
        self._discovered_route_queue: list[dict[str, str]] = []
        self._queued_routes: set[str] = set()
        self.scan_stop_requested = False
        self.parking_lot_actions: list[dict[str, Any]] = []
        self.scan_events: list[dict[str, Any]] = []
        self.session_manager = SessionManager(self.base_url, credentials=self.credentials)

        # Core intelligence & exploration engines
        self.element_registry = ElementIdentityRegistry()
        self.viewport_scanner = ViewportDeepScanner(self.page)
        self.menu_overlay_engine = MenuOverlayEngine(self.page)
        self.probe_engine = InteractionProbeEngine(self.page)
        self._active_module_id: str = ""
        self._current_ui_state_id: str = "state_base"

        self.page.on("response", self._record_api_response)
        install_document_scope_guard(self.page, self.base_url, self._emit_event)
        self._seed_loaded_api_responses()
        if resume_from_checkpoint:
            self._load_checkpoint()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def crawl(
        self,
        start_url: str | None = None,
        selected_module_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Crawl selected top-level modules and their safe in-module routes.

        ``selected_module_ids`` comes from authenticated menu discovery. This
        prevents an all-menu traversal when a user asked for only one module.
        """
        self._ensure_authenticated()
        wait_for_ui_stability(self.page)

        application_shell = detect_application_shell(self.page, event_callback=self._emit_event)
        self.menu_hierarchy = application_shell.get("global_navigation", {})
        menu_items = self._build_menu_list(application_shell)
        selected_items = self._select_menu_items(menu_items, selected_module_ids)
        logger.info("Discovered %d modules; %d selected for this run", len(menu_items), len(selected_items))

        # An application with no detectable navigation still has one usable
        # module: its authenticated landing screen. An explicit empty user
        # selection never falls back to this behavior.
        if selected_module_ids is None and not selected_items:
            landing = start_url or self.page.url
            self._active_module_id = f"route:{self._normalize(landing)}"
            self._scan_route(landing, menu_name=self._landing_label(), expected_url=landing, api_marker=0)
            self._review_module(self._landing_label(), landing)
            self._save_checkpoint()
            return self._result_snapshot()

        # Finish one selected module, including safe child routes, then pause
        # for an explicit continue/end decision before opening the next one.
        for menu_item in menu_items:
            if self.scan_stop_requested:
                break
            if menu_item not in selected_items:
                continue
            self._active_module_id = menu_item.get("id") or menu_item.get("module_id") or f"route:{menu_item.get('url', '')}"
            self._visit_menu_item(menu_item)
            self._drain_discovered_routes()
            self._review_module(menu_item["label"], menu_item.get("url", ""))

        self._save_checkpoint()
        return self._result_snapshot()

    def discover_modules(self) -> list[dict[str, Any]]:
        """Passively return the current application's authenticated modules."""
        self._ensure_authenticated()
        wait_for_ui_stability(self.page)
        application_shell = detect_application_shell(self.page, event_callback=self._emit_event)
        self.menu_hierarchy = application_shell.get("global_navigation", {})
        modules = self._build_menu_list(application_shell)

        # Supplement with direct link inspection in header/nav/aside (especially for icon-only or collapsed navigation)
        try:
            fallback_links = self.page.locator(
                "header a[href], nav a[href], aside a[href], [role='navigation'] a[href], "
                "[role='menubar'] a[href], mat-sidenav a[href]"
            ).evaluate_all("""els => els.map(el => {
                let text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                if (!text) {
                    const child = el.querySelector('[aria-label], [title], svg title, img[alt]');
                    if (child) {
                        text = (child.getAttribute('aria-label') || child.getAttribute('title') || child.getAttribute('alt') || child.textContent || '').trim();
                    }
                }
                return { href: el.getAttribute('href') || '', text: text || '' };
            })""")
            for fl in fallback_links:
                href = fl.get("href")
                resolved = self._normalize(href) if href else ""
                if resolved and is_valid_url(resolved, self.base_url):
                    mid = f"route:{resolved}"
                    if not any(m.get("id") == mid for m in modules):
                        label = fl.get("text") or resolved.rsplit("/", 1)[-1] or "Home"
                        modules.append({
                            "id": mid,
                            "module_id": mid,
                            "url": resolved,
                            "label": label,
                            "source": "fallback_navigation",
                            "hierarchy_level": 0,
                            "parent_module": None,
                            "confidence": 0.85,
                        })
        except Exception:
            pass

        # Fallback 2: Single-page or no visible navigation shell -> default to authenticated landing module
        if not modules:
            landing_url = self._normalize(self.page.url)
            landing_label = self._landing_label()
            landing_id = f"route:{landing_url}"
            modules.append({
                "id": landing_id,
                "module_id": landing_id,
                "url": landing_url,
                "label": landing_label,
                "source": "landing_page",
                "hierarchy_level": 0,
                "parent_module": None,
                "confidence": 0.8,
            })

        return modules

    def _drain_discovered_routes(self) -> None:
        while self._discovered_route_queue and not self.scan_stop_requested:
            candidate = self._discovered_route_queue.pop(0)
            self._visit_discovered_route(candidate)

    @staticmethod
    def _select_menu_items(
        menu_items: list[dict[str, str]], selected_module_ids: list[str] | None,
    ) -> list[dict[str, str]]:
        if selected_module_ids is None:
            return menu_items
        selected = set(selected_module_ids)
        return [item for item in menu_items if item.get("id") in selected]

    def _queue_page_routes(self, source_route: str, source_label: str) -> None:
        """Queue visible in-scope navigation targets from explicit links and router bindings."""
        if len(self._queued_routes) >= MAX_DISCOVERED_ROUTES:
            return
        try:
            candidates = self.page.locator(
                "a[href], [role='link'][href], [routerlink], [data-route], [data-href], [ng-reflect-router-link]"
            ).evaluate_all("""elements => elements.map(el => {
                const style = getComputedStyle(el), box = el.getBoundingClientRect();
                const value = el.getAttribute('href') || el.getAttribute('routerlink') ||
                    el.getAttribute('data-route') || el.getAttribute('data-href') ||
                    el.getAttribute('ng-reflect-router-link') || '';
                return { value, label: (el.getAttribute('aria-label') || el.getAttribute('title') ||
                    el.innerText || el.textContent || '').trim().slice(0, 80),
                    visible: style.display !== 'none' && style.visibility !== 'hidden' && !!(box.width || box.height),
                    inShell: !!el.closest('nav, aside, header, [role="navigation"], .sidebar, .sidenav, .side-nav') };
            })""")
        except Exception:
            return

        for candidate in candidates:
            # Global navigation is selected explicitly before scanning.  It is
            # not a child route of the active module and must never re-enqueue
            # unrelated modules through ordinary link discovery.
            if (not candidate.get("visible") or not candidate.get("value")
                    or candidate.get("inShell")):
                continue
            destination = self._normalize(str(candidate["value"]))
            if (not is_valid_url(destination, self.base_url)
                    or destination == source_route
                    or destination in self._queued_routes
                    or self.route_tracker.is_visited(destination)):
                continue
            self._queued_routes.add(destination)
            self._discovered_route_queue.append({
                "url": destination,
                "label": str(candidate.get("label") or destination),
                "source": source_label,
            })
            if hasattr(self.route_tracker, "graph"):
                self.route_tracker.graph.add_route(
                    route=destination,
                    parent_route=source_route,
                    navigation_cause="content_link",
                    return_strategy="navigate_url",
                    menu_name=str(candidate.get("label") or destination),
                )
            self._emit_event("route_discovered", {
                "route": destination, "source_route": source_route,
                "label": candidate.get("label") or destination,
            })

    def _visit_discovered_route(self, candidate: dict[str, str]) -> None:
        url = candidate["url"]
        if self.route_tracker.is_visited(url):
            return
        try:
            marker = len(self._api_responses)
            self._navigate(url)
            self._ensure_authenticated()
            wait_for_ui_stability(self.page)
            self._scan_route(self.page.url, candidate["label"], expected_url=url, api_marker=marker)
        except Exception as exc:
            logger.warning("Discovered route navigation failed: %s (%s)", url, exc)
            self._emit_event("route_failed", {"route": url, "reason": "discovered_route_navigation_failed"})

    def replay_route(
        self, url: str, menu_name: str = "route_replay", api_marker: int | None = None,
    ) -> dict[str, object]:
        """Force a single in-scope route through the consolidated scanner and checkpoint it."""
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
            "scan_stopped_early": self.scan_stop_requested,
            "parking_lot_actions": self.parking_lot_actions,
            "scan_events": self.scan_events,
            "route_graph": self.route_tracker.graph.to_dict(),
        }

    # ------------------------------------------------------------------
    # Menu discovery & navigation
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
                        continue

                    resolved_url = self._normalize(href) if href else ""
                    if resolved_url and not is_valid_url(resolved_url, self.base_url):
                        continue

                    dedup_key = (resolved_url, "") if resolved_url else (label.lower(), container_selector)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    mid = self._module_id(resolved_url, label, item.get("selector", ""))
                    menu_items.append(
                        {
                            "id": mid,
                            "module_id": mid,
                            "url": resolved_url,
                            "label": label,
                            "source": menu_type,
                            "container_selector": container_selector,
                            "selector": item.get("selector", ""),
                            "hierarchy_level": item.get("hierarchy_level", 0),
                            "parent_module": item.get("parent_module"),
                            "confidence": 1.0 if resolved_url else 0.9,
                        }
                    )
                    if resolved_url:
                        self.feature_routes[resolved_url] = {
                            "label": label,
                            "source": menu_type,
                            "hierarchy_level": item.get("hierarchy_level", 0),
                        }

        return menu_items

    @staticmethod
    def _module_id(url: str, label: str, selector: str) -> str:
        """Stable selection ID: route when available, otherwise control+label."""
        if url:
            return f"route:{url}"
        return f"control:{selector.strip()}|{label.strip().lower()}"

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

        self._scan_route(self.page.url, menu_name=label, expected_url=url or self.page.url,
                         api_marker=api_marker)

    def _click_menu_item(self, label: str, container_selector: str, selector: str = "") -> bool:
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

            try:
                self.page.wait_for_url(lambda new_url: new_url != previous_url, timeout=2000)
            except PlaywrightTimeoutError:
                pass
            try:
                self.page.wait_for_load_state("networkidle", timeout=4000)
            except PlaywrightTimeoutError:
                pass
            current_count = int(self.page.evaluate("() => document.body ? document.body.querySelectorAll('*').length : 0"))
            return self.page.url != previous_url or current_count != previous_count
        except Exception:
            logger.warning(f"Click failed for menu '{label}', falling back to URL navigation")
            return False

    # ------------------------------------------------------------------
    # Consolidated Route Scanner
    # ------------------------------------------------------------------

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

        # 1. Page stabilization
        stable = self._stabilize_page(normalized)
        base_state = compute_ui_state_fingerprint(self.page, normalized)
        self._current_ui_state_id = base_state.get("ui_state_id", "state_base")
        if hasattr(self.route_tracker, "graph"):
            self.route_tracker.graph.record_ui_state(normalized, base_state)

        # 2. Extract initial passive UI intelligence
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

        active_mid = self._active_module_id or f"route:{normalized}"
        # Register initial targets with ElementIdentityRegistry
        initial_targets = list(route_doc.get("automation_targets", []))
        for target in initial_targets:
            target.setdefault("ui_state_id", self._current_ui_state_id)
            target.setdefault("module_id", active_mid)
            target.setdefault("discovery_mechanism", "passive_dom")
            ident, is_new = self.element_registry.register(normalized, target)
            if is_new:
                self._emit_event("element_found", {
                    "route": normalized, "id": ident,
                    "element_type": target.get("type", ""), "label": target.get("label", ""),
                })
        route_doc["auto_detected_elements"] = list(route_doc.get("automation_targets", []))

        # Retry if empty
        if not route_doc["automation_targets"]:
            logger.info(f"Empty scan on {normalized}, retrying after domcontentloaded")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
                remaining = self._remaining_route_timeout()
                if time.monotonic() < self._route_deadline:
                    self.page.set_default_timeout(remaining)
                    route_doc = build_ui_intelligence(
                        self.page,
                        feature_name=menu_name,
                        menu_name=menu_name,
                        global_seen=self._global_seen,
                        deadline=self._route_deadline,
                    )
                    for target in route_doc.get("automation_targets", []):
                        target.setdefault("ui_state_id", self._current_ui_state_id)
                        target.setdefault("module_id", active_mid)
                        target.setdefault("discovery_mechanism", "passive_dom")
                        ident, is_new = self.element_registry.register(normalized, target)
                        if is_new:
                            self._emit_event("element_found", {
                                "route": normalized, "id": ident,
                                "element_type": target.get("type", ""), "label": target.get("label", ""),
                            })
                    route_doc["auto_detected_elements"] = list(route_doc.get("automation_targets", []))
            except Exception:
                logger.exception(f"Retry scan failed: {normalized}")

        self.page_intelligence[normalized] = route_doc

        # 3. Deep scroll document and nested containers using ViewportDeepScanner
        self._deep_scroll_viewport(normalized, menu_name, route_doc)

        # 4. Safely detect and open menu/dropdown overlays using MenuOverlayEngine
        self._detect_and_open_overlays(normalized, menu_name, route_doc)

        # 5. Gather candidate actions, classify with ActionClassifier, divert to parking lot or probe safely
        self._probe_candidate_actions(normalized, menu_name, route_doc)

        # Re-sync automation targets and sections from the registry
        route_doc["automation_targets"] = self.element_registry.get_elements_for_route(normalized)
        route_doc["sections"] = _recount_sections(route_doc["automation_targets"])

        # 6. Health scoring via RouteHealthScorer.score_route()
        page_load_success, document_status = self._page_load_health()
        validation = score_route(
            expected_url=self.route_tracker.normalize_url(expected_url or normalized),
            actual_url=self.route_tracker.normalize_url(self.page.url),
            stable=stable,
            target_count=len(route_doc["automation_targets"]),
            api_responses=self._api_responses[api_marker:],
            interaction_stats=self._route_interactions.get(normalized, {"attempted": 0, "succeeded": 0}),
            page_load_success=page_load_success,
            document_status=document_status,
            scan_error=bool(route_doc.get("scan_error")),
            is_login_page=self._is_login_page(),
        )
        self.route_validations[normalized] = validation
        route_doc["route_validation"] = validation
        # Keep the persisted graph truthful.  A route is marked as visited
        # before scanning to prevent traversal loops, but its final health and
        # score are only known after the route pipeline has completed.
        self.route_tracker.graph.mark_visited(
            normalized,
            status=validation["status"],
            score=validation["score"],
        )

        # Enforce minimum on-page time for user perception and late widgets
        elapsed = time.monotonic() - _scan_start
        if elapsed < self._MIN_SCAN_SECONDS:
            self.page.wait_for_timeout(int((self._MIN_SCAN_SECONDS - elapsed) * 1000))

        logger.info(
            f"Scan complete: {normalized} | "
            f"{len(route_doc['automation_targets'])} targets | "
            f"{time.monotonic() - _scan_start:.2f}s"
        )
        self._save_checkpoint()

        # Queue visible explicit links from this page
        self._queue_page_routes(normalized, menu_name)
        # Route discovery is part of the checkpointed state.  Saving only
        # before this call loses the resume queue after an interruption.
        self._save_checkpoint()

        if not route_doc.get("scan_error"):
            self._emit_event("route_completed", {
                "route": normalized,
                "status": self.route_validations[normalized]["status"],
                "score": self.route_validations[normalized]["score"],
                "element_count": len(route_doc.get("automation_targets", [])),
            })

    def _deep_scroll_viewport(self, normalized: str, menu_name: str, route_doc: dict[str, Any]) -> None:
        """Deep-scroll document and nested containers using ViewportDeepScanner."""
        deadline = self._route_deadline if self._route_deadline > 0 else (time.monotonic() + ROUTE_SCAN_TIMEOUT / 1000)
        active_mid = self._active_module_id or f"route:{normalized}"

        def on_viewport_change(scroll_label: str) -> None:
            if self._route_deadline > 0 and time.monotonic() >= self._route_deadline:
                return
            extra = build_ui_intelligence(
                self.page,
                feature_name=f"{menu_name} > {scroll_label}",
                menu_name=menu_name,
                global_seen=self._global_seen,
                deadline=deadline,
            )
            for target in extra.get("automation_targets", []):
                target["discovery_mechanism"] = "viewport_scroll"
                target["module_id"] = active_mid
                target["ui_state_id"] = self._current_ui_state_id
                ident, is_new = self.element_registry.register(normalized, target)
                if is_new:
                    route_doc["automation_targets"].append(target)
                    self._emit_event("element_found", {
                        "route": normalized, "id": ident,
                        "element_type": target.get("type", ""), "label": target.get("label", ""),
                    })

        try:
            self.viewport_scanner.deep_scroll(on_viewport_change, deadline=deadline)
        except Exception:
            logger.exception("Viewport deep scan failed; continuing with route scan")

    def _detect_and_open_overlays(self, normalized: str, menu_name: str, route_doc: dict[str, Any]) -> None:
        """Detect and safely open menus and dropdowns using MenuOverlayEngine."""
        active_mid = self._active_module_id or f"route:{normalized}"
        try:
            triggers = self.page.locator(
                "[aria-haspopup], [aria-expanded], [role='combobox'], select, "
                "mat-select, .p-dropdown, .dropdown-toggle, [data-radix-popper-content-wrapper]"
            )
            count = min(triggers.count(), 10)
            for i in range(count):
                if self._route_deadline > 0 and time.monotonic() >= self._route_deadline:
                    break
                trigger = triggers.nth(i)
                if not trigger.is_visible():
                    continue
                items = self.menu_overlay_engine.safely_open_and_capture(trigger)
                for item in items:
                    href = item.get("href")
                    if href:
                        dest = self._normalize(href)
                        if is_valid_url(dest, self.base_url) and not self.route_tracker.is_visited(dest) and dest not in self._queued_routes:
                            self._queued_routes.add(dest)
                            self._discovered_route_queue.append({
                                "url": dest,
                                "label": item.get("text") or dest,
                                "source": f"{menu_name} > overlay",
                            })
                            if hasattr(self.route_tracker, "graph"):
                                self.route_tracker.graph.add_route(
                                    route=dest,
                                    parent_route=normalized,
                                    navigation_cause="overlay_menu",
                                    return_strategy="navigate_url",
                                    menu_name=item.get("text") or dest,
                                )
                    overlay_target = {
                        "label": item.get("text", ""),
                        "type": "menu_item" if item.get("role") in {"menuitem", "option"} else item.get("role", "button"),
                        "category": "dropdowns" if item.get("role") == "option" else "navigation",
                        "element_xpath": f"//overlay//{item.get('role', 'item')}[text()='{item.get('text', '')}']",
                        "element_role": item.get("role", "menuitem"),
                        "element_index": i + 1,
                        "selector": {"primary": {"strategy": "text", "value": item.get("text", "")}, "fallback": []},
                        "is_dynamic": True,
                        "hierarchy_level": 2,
                        "discovery_mechanism": "menu_overlay",
                        "module_id": active_mid,
                        "ui_state_id": self._current_ui_state_id,
                    }
                    ident, is_new = self.element_registry.register(normalized, overlay_target)
                    if is_new:
                        route_doc["automation_targets"].append(overlay_target)
                        self._emit_event("element_found", {
                            "route": normalized, "id": ident,
                            "element_type": overlay_target["type"],
                            "label": overlay_target["label"],
                        })
        except Exception:
            logger.debug("Overlay detection encountered non-fatal error; continuing")

    def _probe_candidate_actions(self, normalized: str, menu_name: str, route_doc: dict[str, Any]) -> None:
        """Gather candidate controls, classify them, divert unsafe actions to the parking lot,
        and safely probe navigation/expand/filter/sort/pagination buttons."""
        candidate_selector = (
            "button, [role='button'], input[type='button'], input[type='submit'], "
            "[role='tab'], [role='switch'], [role='search'], "
            "[data-route], [data-href]"
        )
        try:
            candidates = self.page.locator(candidate_selector).evaluate_all("""els => els.map((el, index) => {
                const style = window.getComputedStyle(el);
                const box = el.getBoundingClientRect();
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && (box.width > 0 || box.height > 0);
                return {
                    index,
                    label: (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '').trim().slice(0, 80),
                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                    tagName: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    aria_expanded: el.getAttribute('aria-expanded') || '',
                    aria_haspopup: el.getAttribute('aria-haspopup') || '',
                    href: el.getAttribute('href') || '',
                    routerlink: el.getAttribute('routerlink') || el.getAttribute('ng-reflect-router-link') || el.getAttribute('data-route') || el.getAttribute('data-href') || '',
                    visible
                };
            })""")
        except Exception:
            return

        known_parked = {p.get("label", "").lower() for p in self.parking_lot_actions if p.get("route") == normalized}
        probed_count = 0
        max_probes = 10

        for item in candidates:
            if not item.get("visible") or not item.get("label"):
                continue
            if (self._route_deadline > 0 and time.monotonic() >= self._route_deadline) or self.scan_stop_requested:
                break

            classification = classify_action(item)

            # Divert unsafe / unconfirmed / destructive actions to the parking lot
            if is_parking_lot_action(classification):
                lbl_lower = item["label"].lower()
                if lbl_lower not in known_parked:
                    known_parked.add(lbl_lower)
                    entry = {
                        "route": normalized,
                        "label": item["label"],
                        "classification": classification,
                        "selector": item.get("routerlink") or item.get("tagName"),
                        "reason": (
                            "Destructive action blocked by safety policy"
                            if classification == ActionType.BLOCKED_DESTRUCTIVE
                            else "Action requires manual user confirmation"
                        ),
                        "timestamp": time.time(),
                    }
                    self.parking_lot_actions.append(entry)
                    self._emit_event("parking_lot_action_added", entry)
                continue

            # Safe action: probe via InteractionProbeEngine
            if is_safe_action(classification) and probed_count < max_probes:
                probed_count += 1
                interaction = self._route_interactions.setdefault(normalized, {"attempted": 0, "succeeded": 0})
                interaction["attempted"] += 1

                try:
                    # The candidate index is captured from this exact selector.
                    # Reusing a label-based locator causes repeated labels to
                    # probe the first matching control and silently skip the
                    # others, which breaks full-page coverage.
                    locator = self.page.locator(candidate_selector).nth(int(item["index"]))
                    if not locator.is_visible():
                        continue

                    state_before = compute_ui_state_fingerprint(self.page, normalized)
                    if hasattr(self.route_tracker, "graph"):
                        self.route_tracker.graph.record_ui_state(normalized, state_before)

                    probe_res = self.probe_engine.probe_action(
                        item, locator, current_route=normalized,
                        parking_lot=self.parking_lot_actions, auto_restore=False,
                    )
                    if probe_res.get("success"):
                        interaction["succeeded"] += 1

                    if hasattr(self.route_tracker, "graph"):
                        self.route_tracker.graph.record_action(normalized, {
                            "label": item.get("label"),
                            "role": item.get("role"),
                            "classification": classification,
                            "success": probe_res.get("success", False),
                        })

                    if probe_res.get("url_changed"):
                        after_url = probe_res.get("after_url", "")
                        child_route = self.route_tracker.normalize_url(after_url)
                        if is_valid_url(child_route, self.base_url):
                            parent_node = (
                                self.route_tracker.graph.nodes.get(normalized)
                                if hasattr(self.route_tracker, "graph")
                                else None
                            )
                            current_depth = parent_node.depth if parent_node else 0
                            if current_depth < 5:
                                if hasattr(self.route_tracker, "graph"):
                                    self.route_tracker.graph.add_route(
                                        route=child_route,
                                        parent_route=normalized,
                                        navigation_cause="safe_button_navigation",
                                        return_strategy="navigate_url",
                                        menu_name=item["label"],
                                    )
                                if (
                                    not self.route_tracker.is_visited(child_route)
                                    and child_route not in self.page_intelligence
                                ):
                                    logger.info(
                                        "Safe button navigation revealed child route: %s -> %s (depth=%d)",
                                        normalized, child_route, current_depth + 1,
                                    )
                                    self._scan_route(
                                        child_route,
                                        menu_name=f"{menu_name} > {item['label']}",
                                        expected_url=child_route,
                                    )
                                    self._save_checkpoint()

                        # Restore parent route state
                        try:
                            self._navigate(normalized)
                            wait_for_ui_stability(self.page)
                        except Exception:
                            logger.warning("Could not restore parent route after safe probe: %s", normalized)

                    else:
                        state_after = compute_ui_state_fingerprint(self.page, normalized)
                        if state_after["ui_state_id"] != state_before["ui_state_id"]:
                            if hasattr(self.route_tracker, "graph"):
                                self.route_tracker.graph.record_ui_state(normalized, state_after)
                                self.route_tracker.graph.record_state_transition(
                                    normalized,
                                    state_before["ui_state_id"],
                                    state_after["ui_state_id"],
                                    item,
                                )
                            self._current_ui_state_id = state_after["ui_state_id"]

                        if probe_res.get("dom_delta", 0) > 0:
                            extra = build_ui_intelligence(
                                self.page,
                                feature_name=f"{menu_name} > {item['label']}",
                                menu_name=menu_name,
                                global_seen=self._global_seen,
                                deadline=self._route_deadline,
                            )
                            active_mid = self._active_module_id or f"route:{normalized}"
                            for target in extra.get("automation_targets", []):
                                target["discovery_mechanism"] = "interaction_probe"
                                target["module_id"] = active_mid
                                target["ui_state_id"] = self._current_ui_state_id
                                ident, is_new = self.element_registry.register(normalized, target)
                                if is_new:
                                    route_doc["automation_targets"].append(target)
                                    self._emit_event("element_found", {
                                        "route": normalized, "id": ident,
                                        "element_type": target.get("type", ""), "label": target.get("label", ""),
                                    })
                except Exception as exc:
                    logger.debug("Failed probing safe action '%s': %s", item["label"], exc)

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        self.scan_events.append({
            "event_type": event_type,
            "timestamp": time.time(),
            "data": data,
        })
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
        try:
            self.page.wait_for_load_state("networkidle", timeout=self._remaining_route_timeout(8000))
        except PlaywrightTimeoutError:
            logger.debug(f"networkidle timeout: {route_label}")

        self.page.wait_for_timeout(min(2000, self._remaining_route_timeout(2000)))

        body_visible = False
        try:
            self.page.wait_for_selector("body", timeout=self._remaining_route_timeout(5000), state="visible")
            body_visible = True
        except PlaywrightTimeoutError:
            logger.warning(f"body never became visible: {route_label}")

        for selector in (
            "table, [role='table'], mat-table",
            "button, [role='button'], mat-button, [mat-button], [mat-raised-button]",
            "input, textarea, [contenteditable='true'], [role='textbox'], mat-form-field",
            "[role='combobox'], mat-select, [aria-haspopup='listbox']",
        ):
            try:
                self.page.wait_for_selector(selector, timeout=self._remaining_route_timeout(2000), state="visible")
            except PlaywrightTimeoutError:
                pass

        dom_stable = False
        try:
            self.page.wait_for_function(
                """() => new Promise(resolve => {
                  let timer;
                  const observer = new MutationObserver(() => {
                    clearTimeout(timer);
                    timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 200);
                  });
                  observer.observe(document.body, {childList:true, subtree:true, attributes:true});
                  timer = setTimeout(() => { observer.disconnect(); resolve(true); }, 200);
                })""",
                timeout=self._remaining_route_timeout(1500),
            )
            dom_stable = True
        except PlaywrightTimeoutError:
            logger.debug(f"DOM did not settle within window: {route_label}")
        return body_visible and dom_stable

    # ------------------------------------------------------------------
    # Interactive learning
    # ------------------------------------------------------------------

    def _review_module(self, module_label: str, module_url: str) -> None:
        """Pause only after a selected module is complete, never per child route."""
        if not self.interactive_mode or self.scan_stop_requested:
            return

        # Content-link traversal may leave the browser on a child screen.
        # Return to the selected module before showing the user's review UI.
        if module_url:
            try:
                expected = self.route_tracker.normalize_url(module_url)
                if self.route_tracker.normalize_url(self.page.url) != expected:
                    self._navigate(expected)
                    wait_for_ui_stability(self.page)
            except Exception:
                logger.warning("Could not restore selected module before review: %s", module_label)

        normalized = self.route_tracker.normalize_url(module_url or self.page.url)
        route_doc = self.page_intelligence.get(normalized)
        if route_doc is None:
            # Click-only menu entries may not expose a URL. Use the latest
            # document carrying this menu label rather than blocking the run.
            route_doc = next(
                (doc for doc in self.page_intelligence.values()
                 if doc.get("menu_name") == module_label),
                None,
            )
            if route_doc is None:
                return
            normalized = self.route_tracker.normalize_url(route_doc.get("route") or self.page.url)

        self._emit_event("module_review_started", {
            "route": normalized,
            "module_name": module_label,
            "message": "Selected module is complete; waiting for continue or end.",
        })
        summary = {
            "module_name": module_label,
            "element_count": len(route_doc.get("automation_targets", [])),
            "route_count": len(self.page_intelligence),
        }
        self._capture_interactions(normalized, route_doc, summary=summary)
        self._emit_event("module_review_completed", {
            "route": normalized,
            "module_name": module_label,
            "interaction_count": len(route_doc.get("interactions", [])),
            "ended": self.scan_stop_requested,
        })

    def _capture_interactions(
        self, normalized: str, route_doc: dict[str, Any], summary: dict[str, Any] | None = None,
    ) -> None:
        logger.info(f"Interactive mode: capturing on {normalized}")
        try:
            if summary:
                inject_capture(self.page, summary=summary)
            else:
                inject_capture(self.page)
        except Exception:
            logger.exception("Interactive mode: injection failed, falling back to passive scan")
            route_doc["interactions"] = []
            return

        choice = wait_for_user(self.page, timeout_seconds=self.interactive_timeout)
        raw_events = extract_events(self.page)
        interactions = events_to_interactions(raw_events)
        flow = interactions_to_flow(interactions)

        if choice == "end":
            self.scan_stop_requested = True
            self._emit_event("scan_stopped", {
                "route": normalized,
                "message": "User ended the scan; exporting collected results.",
            })

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
            ident, is_new = self.element_registry.register(normalized, target)
            target["id"] = ident
            target["hierarchy_level"] = 2
            target["interaction_probability"] = 1.0
            target["interaction_probability_method"] = "user_confirmed_interaction"
            if is_new:
                route_doc["automation_targets"].append(target)
                self._emit_event("element_found", {
                    "route": normalized, "id": ident,
                    "element_type": target.get("type", ""), "label": target.get("label", ""),
                })

        route_doc["user_confirmed_elements"] = user_targets
        route_doc["sections"] = _recount_sections(route_doc["automation_targets"])
        self.manual_interactions.extend(
            {**interaction, "route": normalized} for interaction in interactions
        )
        self.interaction_flow.extend({**step, "route": normalized} for step in flow)

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
        return ""

    @staticmethod
    def _role_for_target(target: dict[str, Any]) -> str:
        element_type = target.get("type", "")
        return {"button": "button", "input": "textbox", "dropdown": "combobox",
                "table": "table"}.get(element_type, "")

    def _landing_label(self) -> str:
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
        if not is_valid_url(url, self.base_url):
            raise ValueError(f"Refusing to navigate outside requested app scope: {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)

    def _remaining_route_timeout(self, preferred_ms: int = ROUTE_SCAN_TIMEOUT) -> int:
        if not self._route_deadline:
            return preferred_ms
        return max(1, min(preferred_ms, int((self._route_deadline - time.monotonic()) * 1000)))

    def _record_api_response(self, response: Any) -> None:
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
        return compute_namespace(self.base_url)

    def _load_checkpoint(self) -> None:
        if not self.checkpoint_store:
            return
        data = self.checkpoint_store.load(expected_base_url=self.base_url)
        if not data:
            logger.info("No compatible checkpoint found for namespace; starting fresh scan")
            return
        try:
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
            self.parking_lot_actions = data.get("parking_lot_actions", [])
            self.scan_events = data.get("scan_events", [])
            if "route_graph" in data:
                self.route_tracker.graph = RouteGraph.from_dict(data["route_graph"])
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
                    # Preserve the health score and status loaded from the
                    # route graph; do not reset them to the tracker defaults.
                    node = self.route_tracker.graph.get_node(route)
                    if node:
                        node.visited = True
                    else:
                        self.route_tracker.mark_visited(route)

            # Resume the bounded traversal queue rather than only restoring
            # completed routes.  Labels are rebuilt from durable graph/menu
            # metadata when available; the URL remains the source of truth.
            self._discovered_route_queue = []
            self._queued_routes = set()
            for route in data.get("pending_routes", []):
                if not is_valid_url(route, self.base_url) or self.route_tracker.is_visited(route):
                    continue
                node = self.route_tracker.graph.get_node(route)
                self._discovered_route_queue.append({
                    "url": route,
                    "label": (node.menu_name if node and node.menu_name else route),
                    "source": "checkpoint_resume",
                })
                self._queued_routes.add(route)

            # Re-register targets into ElementIdentityRegistry
            for route, doc in self.page_intelligence.items():
                for target in doc.get("automation_targets", []):
                    self.element_registry.register(route, target)

            logger.info("Resumed scan checkpoint with %d routes", len(self.page_intelligence))
        except Exception:
            logger.exception("Unable to process checkpoint; starting fresh scan")

    def _save_checkpoint(self) -> None:
        if not self.checkpoint_store:
            return
        try:
            session_id = (self.checkpoint_path.stem.removeprefix("checkpoint_")
                          if self.checkpoint_path else "")
            data = {
                "session_id": session_id,
                "namespace": self._checkpoint_namespace(),
                "visited_routes": sorted(self.route_tracker.get_visited_routes()),
                "pending_routes": [item["url"] for item in self._discovered_route_queue],
                "failed_routes": sorted(
                    route for route, item in self.route_validations.items()
                    if item.get("status") in {"NON-WORKING", "NON_WORKING_ROUTE"}
                ),
                "partial_routes": sorted(
                    route for route, item in self.route_validations.items()
                    if item.get("status") in {"PARTIAL", "PARTIAL_ROUTE"}
                ),
                "route_validations": self.route_validations,
                "page_intelligence": self.page_intelligence,
                "element_map_snapshot": [
                    {"route": route, **target}
                    for route, doc in self.page_intelligence.items()
                    for target in doc.get("automation_targets", [])
                ],
                "parking_lot_actions": self.parking_lot_actions,
                "scan_events": self.scan_events,
                "manual_checkpoint_state": {
                    "scan_stop_requested": self.scan_stop_requested,
                    "interactive_mode": self.interactive_mode,
                },
                "feature_routes": self.feature_routes,
                "menu_hierarchy": self.menu_hierarchy,
                "route_graph": self.route_tracker.graph.to_dict(),
            }
            self.checkpoint_store.save(data)
        except Exception:
            logger.exception("Could not persist route checkpoint")

    def _ensure_authenticated(self) -> None:
        if not self._is_login_page():
            return

        if not self.credentials:
            raise AuthenticationExpiredError("Authentication expired. Refresh the session to continue.")

        logger.warning("Authentication session appears to be lost. Attempting login recovery.")
        username, password = self.credentials
        login(self.page, username, password, self.base_url)

    def _is_login_page(self) -> bool:
        from urllib.parse import unquote, urlparse
        login_url_indicators = {"login", "sign-in", "signin", "auth", "authenticate", "sso"}
        path_parts = {part.casefold() for part in unquote(urlparse(self.page.url).path).split("/") if part}
        if path_parts & login_url_indicators:
            return True

        try:
            return self.page.locator('input[type="password"]').first.is_visible()
        except Exception:
            return False

    def _normalize(self, url: str) -> str:
        return self.route_tracker.normalize_url(urljoin(self.page.url or self.base_url, url))
