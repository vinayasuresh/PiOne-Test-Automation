import time
import json
from typing import Any, Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import (
    ENABLE_DEEP_EXPLORATION,
    MENU_EXPANSION_TIMEOUT,
    MAX_DISCOVERED_ROUTES,
    GLOBAL_SCAN_TIMEOUT,
    MAX_TOTAL_PROBES,
    ROUTE_NAVIGATION_TIMEOUT,
    ROUTE_LOAD_TIMEOUT,
    ROUTE_SCAN_TIMEOUT,
    STABILIZATION_TIMEOUT,
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
from automation_framework.crawler.ui_wait_engine import wait_for_spa_readiness, wait_for_ui_stability
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
    extract_fallback_body_elements,
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
        checkpoint_store: CheckpointStore | None = None,
        resume_from_checkpoint: bool = False,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        scan_id: str | None = None,
        session_state_trusted: bool = False,
    ) -> None:
        self.page = page
        self.page.set_default_timeout(ROUTE_SCAN_TIMEOUT)
        self.page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)
        self.base_url = base_url
        self.credentials = credentials
        self.interactive_mode = interactive_mode
        self.interactive_timeout = interactive_timeout
        if checkpoint_store:
            self.checkpoint_store = checkpoint_store
            self.checkpoint_path = checkpoint_store.checkpoint_path
        elif checkpoint_path:
            self.checkpoint_path = Path(checkpoint_path)
            self.checkpoint_store = CheckpointStore(self.checkpoint_path)
        else:
            self.checkpoint_path = None
            self.checkpoint_store = None
        self.checkpoint_loaded = False
        self.event_callback = event_callback
        self.run_id = run_id
        self.scan_id = scan_id
        self._selected_module_ids: list[str] | None = None
        self._retry_count = 0
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
        self._global_deadline = 0.0
        self._total_probes = 0
        self._deep_exploration_active = False
        self._discovered_route_queue: list[dict[str, str]] = []
        self._queued_routes: set[str] = set()
        self.scan_stop_requested = False
        self.parking_lot_actions: list[dict[str, Any]] = []
        self.scan_events: list[dict[str, Any]] = []
        self.session_manager = SessionManager(
            self.base_url,
            credentials=self.credentials,
            session_state_trusted=session_state_trusted,
        )

        # Core intelligence & exploration engines
        self.element_registry = ElementIdentityRegistry()
        self.viewport_scanner = ViewportDeepScanner(self.page)
        self.menu_overlay_engine = MenuOverlayEngine(self.page)
        self.probe_engine = InteractionProbeEngine(self.page)
        self._active_module_id: str = ""
        self.completed_modules: list[str] = []
        self.module_decisions: dict[str, str] = {}
        self._current_ui_state_id: str = "state_base"
        self._probed_action_identities: set[str] = set()
        self._probed_element_identities: set[tuple[str, str]] = set()

        self.page.on("response", self._record_api_response)
        self._current_console_errors: list[str] = []
        self._current_redirect_chain: list[str] = []
        try:
            self.page.on("console", self._record_console_message)
            self.page.on("pageerror", self._record_page_error)
        except Exception:
            pass
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
        self._selected_module_ids = selected_module_ids
        self._global_deadline = time.monotonic() + max(1, GLOBAL_SCAN_TIMEOUT)
        self._total_probes = 0
        self._ensure_authenticated()
        wait_for_ui_stability(self.page)

        application_shell = detect_application_shell(self.page, event_callback=self._emit_event)
        self.menu_hierarchy = application_shell.get("global_navigation", {})
        menu_items = self._build_menu_list(application_shell)
        selected_items = self._select_menu_items(menu_items, selected_module_ids)
        restored_count = self._restore_missing_saved_route_selections(
            menu_items, selected_items, selected_module_ids, self.base_url,
        )
        if restored_count:
            logger.info("Restored %s saved route selection(s) missing from current navigation", restored_count)
        logger.info(f"Discovered {len(menu_items)} modules; {len(selected_items)} selected for this run")

        # An application with no detectable navigation still has one usable
        # module: its authenticated landing screen. An explicit empty user
        # selection never falls back to this behavior.
        landing = start_url or self.page.url
        mid = f"route:{self._normalize(landing)}"
        is_fallback_module_selected = bool(
            selected_module_ids and (
                mid in selected_module_ids or any(str(m).startswith("route:") for m in selected_module_ids)
            )
        )
        if (selected_module_ids is None or is_fallback_module_selected) and not selected_items:
            label = self._landing_label()
            if mid not in self.completed_modules:
                while True:
                    self._active_module_id = mid
                    self._save_checkpoint()
                    self._scan_route(landing, menu_name=label, expected_url=landing, api_marker=0)
                    self._drain_discovered_routes()
                    decision = self._review_module(label, landing)
                    self.module_decisions[mid] = decision
                    if decision == "retry":
                        self._reset_module_state(mid, {"label": label, "url": landing})
                        continue
                    if decision not in {"continue", "retry", "end"}:
                        self.scan_stop_requested = True
                        break
                    self.completed_modules.append(mid)
                    self._save_checkpoint()
                    if decision == "end" or self.scan_stop_requested:
                        self.scan_stop_requested = True
                    break
            self._save_checkpoint()
            return self._result_snapshot()

        # Finish one selected module, including safe child routes, then pause
        # for an explicit continue/end decision before opening the next one.
        for menu_item in menu_items:
            self._check_pause_or_stop()
            if self.scan_stop_requested:
                break
            if menu_item not in selected_items:
                continue
            mid = menu_item.get("id") or menu_item.get("module_id") or f"route:{menu_item.get('url', '')}"
            if mid in self.completed_modules:
                logger.info(f"Skipping already-completed module: {mid}")
                continue

            while True:
                self._active_module_id = mid
                self._save_checkpoint()
                self._visit_menu_item(menu_item)
                self._drain_discovered_routes()
                decision = self._review_module(menu_item["label"], menu_item.get("url", ""))
                self.module_decisions[mid] = decision
                if decision == "retry":
                    logger.info(f"User requested retry for module '{menu_item['label']}' ({mid})")
                    self._reset_module_state(mid, menu_item)
                    continue
                if decision not in {"continue", "retry", "end"}:
                    self.scan_stop_requested = True
                    break
                self.completed_modules.append(mid)
                self._save_checkpoint()
                if decision == "end" or self.scan_stop_requested:
                    self.scan_stop_requested = True
                break

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
            self._check_pause_or_stop()
            if self.scan_stop_requested:
                break
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

    @staticmethod
    def _restore_missing_saved_route_selections(
        menu_items: list[dict[str, str]],
        selected_items: list[dict[str, str]],
        selected_module_ids: list[str] | None,
        base_url: str,
    ) -> int:
        """Append durable selected route IDs that are not in current navigation.

        Checkpoint resume must honour the user's selected scope even when a
        route disappears from the navigation due to authentication state,
        collapsed menus, or a SPA re-render.  Both lists are mutated because
        the crawl loop traverses ``menu_items`` while selection is calculated
        from ``selected_items``.
        """
        selected_item_ids = {
            str(item.get("id") or item.get("module_id") or "")
            for item in selected_items
        }
        restored_count = 0
        for raw_selected_id in selected_module_ids or []:
            selected_id = str(raw_selected_id or "")
            if not selected_id.startswith("route:") or selected_id in selected_item_ids:
                continue
            selected_url = selected_id.removeprefix("route:").strip()
            if not selected_url or not is_valid_url(selected_url, base_url):
                continue
            recovered_item = {
                "id": selected_id,
                "module_id": selected_id,
                "url": selected_url,
                "label": f"Saved route: {urlparse(selected_url).path or '/'}",
                "source": "checkpoint_route_selection",
                # A route already present in a checkpoint must be scanned
                # again here; merely marking its module complete would make
                # the recovered selection a cosmetic 3/3.
                "force_rescan": True,
                "hierarchy_level": 0,
                "parent_module": None,
                "dom_order": 10**9,
            }
            menu_items.append(recovered_item)
            selected_items.append(recovered_item)
            selected_item_ids.add(selected_id)
            restored_count += 1
        return restored_count

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
            logger.warning(f"Discovered route navigation failed: {url} ({exc})")
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
            "current_module_id": self._active_module_id,
            "completed_modules": list(self.completed_modules),
            "module_decisions": dict(self.module_decisions),
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

                    # Keep distinct menu controls even when they resolve to
                    # the same route (common in SPAs: a sidebar item and a
                    # breadcrumb/action can both open the current page).
                    # Only collapse the same labelled control repeated by a
                    # responsive desktop/mobile shell.
                    dedup_key = (resolved_url, label.casefold()) if resolved_url else (label.casefold(), container_selector)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    mid = self._module_id(resolved_url, label, item.get("selector", ""))
                    if any(existing.get("id") == mid for existing in menu_items):
                        mid = f"control:{item.get('selector', '').strip()}|{label.casefold()}"
                    menu_items.append(
                        {
                            "id": mid,
                            "module_id": mid,
                            "url": resolved_url,
                            "label": label,
                            "source": menu_type,
                            "container_selector": container_selector,
                            "selector": item.get("selector", ""),
                            "dom_order": int(item.get("dom_order", 10**9)),
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

        # The detector records rendered DOM order.  Preserve it across the
        # sidebar/top/overlay buckets instead of relying on dictionary bucket
        # order, which can differ from what the user sees.
        menu_items.sort(key=lambda item: int(item.get("dom_order", 10**9)))

        # Reconstruct parent links from the rendered hierarchy after the
        # global ordering is known.  The detector supplies a stable depth but
        # not every framework exposes an explicit parent id; keeping the
        # nearest preceding shallower item makes nested sidebar entries
        # selectable and auditable without changing their visible order.
        parent_stack: dict[int, dict[str, Any]] = {}
        for item in menu_items:
            level = max(0, int(item.get("hierarchy_level", 0) or 0))
            parent = parent_stack.get(level - 1) if level else None
            if not item.get("parent_module") and parent:
                item["parent_module"] = parent.get("id")
            parent_stack = {
                depth: value for depth, value in parent_stack.items() if depth < level
            }
            parent_stack[level] = item
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

        if url and self.route_tracker.is_visited(url) and not menu_item.get("force_rescan"):
            logger.info(f"Skipping already-visited menu route: {label} ({url})")
            return

        if url and menu_item.get("force_rescan"):
            # A saved route selection was missing from the fresh navigation.
            # Revisit it as a first-class module rather than completing it
            # solely because an earlier module happened to discover the URL.
            self.route_tracker.unmark_visited(url)

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
        prev_deadline = self._route_deadline
        route_budget = ROUTE_SCAN_TIMEOUT / 1000
        if self._global_deadline:
            route_budget = min(route_budget, max(0.1, self._global_deadline - _scan_start))
        self._route_deadline = _scan_start + route_budget
        self.page.set_default_timeout(ROUTE_SCAN_TIMEOUT)
        api_marker = len(self._api_responses) if api_marker is None else api_marker
        self._current_console_errors = []
        self._current_redirect_chain = [normalized]
        logger.info(f"Scanning route: {normalized} (menu: {menu_name})")

        # 1. Page stabilization & SPA readiness
        readiness = self._stabilize_page(normalized)
        stable = readiness.get("ready", False)
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

        # Fallback extraction from document.body if standard scan returned 0 elements
        if not route_doc["automation_targets"]:
            logger.info(f"Empty scan on {normalized}, running deterministic fallback extraction from document.body")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
                remaining = self._remaining_route_timeout()
                if time.monotonic() < self._route_deadline:
                    self.page.set_default_timeout(remaining)
                    fallback_targets = extract_fallback_body_elements(
                        self.page,
                        base_url=self.base_url,
                        menu_name=menu_name,
                        deadline=self._route_deadline,
                    )
                    for target in fallback_targets:
                        target.setdefault("ui_state_id", self._current_ui_state_id)
                        target.setdefault("module_id", active_mid)
                        target.setdefault("discovery_mechanism", "fallback_body_dom")
                        ident, is_new = self.element_registry.register(normalized, target)
                        if is_new:
                            self._emit_event("element_found", {
                                "route": normalized, "id": ident,
                                "element_type": target.get("type", ""), "label": target.get("label", ""),
                            })
                    route_doc["automation_targets"] = self.element_registry.get_elements_for_route(normalized)
                    route_doc["auto_detected_elements"] = list(route_doc["automation_targets"])
                    route_doc["sections"] = _recount_sections(route_doc["automation_targets"])
            except Exception:
                logger.exception(f"Fallback scan failed: {normalized}")

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

        # 6. Health scoring via RouteHealthScorer.score_route() with diagnostic signals
        page_load_success, document_status = self._page_load_health()
        actual_norm = self.route_tracker.normalize_url(self.page.url)
        if actual_norm not in self._current_redirect_chain:
            self._current_redirect_chain.append(actual_norm)

        validation = score_route(
            expected_url=self.route_tracker.normalize_url(expected_url or normalized),
            actual_url=actual_norm,
            stable=stable,
            target_count=len(route_doc["automation_targets"]),
            api_responses=self._api_responses[api_marker:],
            interaction_stats=self._route_interactions.get(normalized, {"attempted": 0, "succeeded": 0}),
            page_load_success=page_load_success,
            document_status=document_status,
            scan_error=bool(route_doc.get("scan_error")),
            is_login_page=self._is_login_page(),
            visible_control_count=readiness.get("visible_control_count", 0),
            dom_element_count=readiness.get("dom_element_count", 0),
            readiness_timeout_reached=readiness.get("readiness_timeout_reached", False),
            console_errors=list(self._current_console_errors),
            redirect_chain=list(self._current_redirect_chain),
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
        self._route_deadline = prev_deadline

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
        and safely probe navigation/expand/filter/sort/pagination buttons and child routes."""
        candidate_selector = (
            "a[href], [role='link'], [routerlink], [ng-reflect-router-link], [data-route], [data-href], "
            "button, [role='button'], input[type='button'], input[type='submit'], "
            "[role='tab'], [role='switch'], [role='search'], "
            "[aria-controls], details > summary, summary, .accordion-button, .accordion-header, mat-expansion-panel-header, "
            ".pagination a, .pagination button, .page-link, .p-paginator button, .p-paginator a, "
            "[role='menuitem'], .dropdown-item, mat-menu-item, "
            "[role='option'], mat-option, .p-dropdown-item"
        )
        try:
            candidates = self.page.locator(candidate_selector).evaluate_all("""els => els.map((el, index) => {
                const style = window.getComputedStyle(el);
                const box = el.getBoundingClientRect();
                const isGlobalNav = !!el.closest('header nav, nav[role="navigation"], aside[role="navigation"], [role="menubar"]');
                if (isGlobalNav && (el.tagName.toLowerCase() === 'a' || el.getAttribute('role') === 'link')) {
                    return { visible: false };
                }
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && (box.width > 0 || box.height > 0);
                const isPagination = !!el.closest('.pagination, .p-paginator, [aria-label*="pagination" i]');
                const isAccordion = !!el.closest('.accordion, [role="region"], details, mat-accordion, mat-expansion-panel');
                let label = (
                    el.getAttribute('aria-label') ||
                    el.getAttribute('title') ||
                    el.innerText ||
                    el.value ||
                    ''
                ).trim();
                if (!label) {
                    const iconChild = el.querySelector('svg title, img[alt]');
                    if (iconChild) {
                        label = (iconChild.textContent || iconChild.getAttribute('alt') || '').trim();
                    }
                }
                const href = el.getAttribute('href') || '';
                const routerlink = el.getAttribute('routerlink') || el.getAttribute('ng-reflect-router-link') || el.getAttribute('data-route') || el.getAttribute('data-href') || '';
                if (!label && (href || routerlink)) {
                    label = (href || routerlink).split('/').filter(Boolean).pop() || 'link';
                }
                return {
                    index,
                    label: label.slice(0, 80),
                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                    tagName: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    aria_expanded: el.getAttribute('aria-expanded') || '',
                    aria_haspopup: el.getAttribute('aria-haspopup') || '',
                    aria_controls: el.getAttribute('aria-controls') || '',
                    href: href,
                    routerlink: routerlink,
                    classes: el.className || '',
                    is_pagination: isPagination,
                    is_accordion: isAccordion,
                    visible
                };
            })""")
        except Exception:
            return

        known_parked = {p.get("label", "").lower() for p in self.parking_lot_actions if p.get("route") == normalized}
        probed_count = 0
        max_probes = 25

        for item in candidates:
            if not item.get("visible") or not item.get("label"):
                continue
            if (self._route_deadline > 0 and time.monotonic() >= self._route_deadline) or self.scan_stop_requested:
                break
            if self._global_deadline and time.monotonic() >= self._global_deadline:
                self.scan_stop_requested = True
                self._emit_event("scan_budget_exceeded", {"reason": "global_timeout"})
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
                        "selector": item.get("routerlink") or item.get("href") or item.get("tagName"),
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

            # Out-of-scope link guard: reject external domains or non-navigational links
            target_link = (
                item.get("routerlink")
                or item.get("data-route")
                or item.get("data-href")
                or item.get("href")
                or ""
            ).strip()
            if target_link:
                if target_link.lower().startswith(("mailto:", "tel:")):
                    self._emit_event("route_rejected", {
                        "parent_route": normalized,
                        "target_url": target_link,
                        "action": item.get("label", ""),
                        "reason": "outside_app_scope_or_browser_error",
                    })
                    continue
                if not target_link.lower().startswith(("javascript:", "#")):
                    norm_target = self._normalize(target_link)
                    if (
                        norm_target.lower().startswith(("chrome-error:", "chrome-error://", "about:blank", "data:"))
                        or not is_valid_url(norm_target, self.base_url)
                    ):
                        self._emit_event("route_rejected", {
                            "parent_route": normalized,
                            "target_url": norm_target,
                            "action": item.get("label", ""),
                            "reason": "outside_app_scope_or_browser_error",
                        })
                        continue

            # Safe action: check duplicate action and element identities
            action_identity = f"{normalized}|{item.get('role', '')}|{item.get('label', '')}|{item.get('href', '')}|{item.get('index', 0)}"
            if action_identity in self._probed_action_identities:
                continue

            elem_ident = compute_element_identity(
                normalized,
                item.get("xpath", "") or f"//body//*[{item.get('index', 0)}]",
                item.get("role", ""),
                item.get("label", ""),
                int(item.get("index", 0)) + 1,
            )
            if (normalized, elem_ident) in self._probed_element_identities:
                continue

            self._probed_action_identities.add(action_identity)
            self._check_pause_or_stop()
            if self.scan_stop_requested:
                break

            self._probed_element_identities.add((normalized, elem_ident))

            if probed_count >= max_probes:
                break
            probed_count += 1
            self._total_probes += 1
            if self._total_probes > MAX_TOTAL_PROBES:
                self.scan_stop_requested = True
                self._emit_event("scan_budget_exceeded", {"reason": "probe_limit", "limit": MAX_TOTAL_PROBES})
                break

            interaction = self._route_interactions.setdefault(normalized, {"attempted": 0, "succeeded": 0})
            interaction["attempted"] += 1

            scroll_pos = {"x": 0, "y": 0}
            try:
                try:
                    locator = self.page.locator(candidate_selector).nth(int(item["index"]))
                    if not locator.is_visible():
                        continue
                except Exception:
                    continue

                state_before = compute_ui_state_fingerprint(self.page, normalized)
                if hasattr(self.route_tracker, "graph"):
                    self.route_tracker.graph.record_ui_state(normalized, state_before)

                try:
                    scroll_pos = self.page.evaluate(
                        "() => ({ x: window.scrollX || window.pageXOffset || 0, y: window.scrollY || window.pageYOffset || 0 })"
                    )
                except Exception:
                    scroll_pos = {"x": 0, "y": 0}

                # Save parent route checkpoint before executing safe action
                self._save_checkpoint()

                # Execute action once with bounded timeout
                try:
                    probe_res = self.probe_engine.probe_action(
                        item, locator, current_route=normalized,
                        parking_lot=self.parking_lot_actions, auto_restore=False,
                    )
                except (PlaywrightTimeoutError, TimeoutError):
                    logger.debug(f"Timeout probing action '{item['label']}'; continuing scan queue")
                    self._emit_event("action_failed", {"route": normalized, "action": item["label"], "reason": "timeout"})
                    self._restore_parent_state(normalized, scroll_pos)
                    continue
                except Exception as click_err:
                    reason = "timeout" if "timeout" in str(click_err).lower() else str(click_err)
                    logger.debug(f"Error probing action '{item['label']}': {click_err}")
                    self._emit_event("action_failed", {"route": normalized, "action": item["label"], "reason": reason})
                    self._restore_parent_state(normalized, scroll_pos)
                    continue

                # Check for Authentication Expiry
                if self._is_login_page():
                    logger.warning(f"Action '{item['label']}' redirected to login page. Attempting session recovery.")
                    try:
                        self._ensure_authenticated()
                        self._restore_parent_state(normalized, scroll_pos)
                        retry_loc = self.page.locator(candidate_selector).nth(int(item["index"]))
                        if retry_loc.is_visible():
                            probe_res = self.probe_engine.probe_action(
                                item, retry_loc, current_route=normalized,
                                parking_lot=self.parking_lot_actions, auto_restore=False,
                            )
                            if self._is_login_page():
                                raise AuthenticationExpiredError("Still on login page after re-authentication retry.")
                    except Exception as auth_err:
                        logger.error(f"Authentication recovery or retry failed for '{item['label']}': {auth_err}")
                        self._emit_event("action_failed", {
                            "route": normalized,
                            "action": item["label"],
                            "reason": f"authentication_expired: {auth_err}",
                        })
                        self._restore_parent_state(normalized, scroll_pos)
                        continue

                if probe_res.get("success"):
                    interaction["succeeded"] += 1

                if hasattr(self.route_tracker, "graph"):
                    self.route_tracker.graph.record_action(normalized, {
                        "label": item.get("label"),
                        "role": item.get("role"),
                        "classification": classification,
                        "success": probe_res.get("success", False),
                    })

                after_url = probe_res.get("after_url", "") or self.page.url
                # Reject external or out-of-scope redirects and browser error pages
                if (
                    after_url.lower().startswith(("chrome-error:", "chrome-error://", "about:blank", "data:", "file:", "javascript:"))
                    or not is_valid_url(after_url, self.base_url)
                ):
                    if after_url.rstrip("/") != normalized.rstrip("/"):
                        logger.warning(f"Action '{item['label']}' caused out-of-scope or browser-error navigation: {after_url}")
                        self._emit_event("route_rejected", {
                            "parent_route": normalized,
                            "target_url": after_url,
                            "action": item["label"],
                            "reason": "outside_app_scope_or_browser_error",
                        })
                        self._restore_parent_state(normalized, scroll_pos)
                        continue

                child_route = self.route_tracker.normalize_url(after_url)
                if probe_res.get("url_changed") and child_route != normalized:
                    if is_valid_url(child_route, self.base_url):
                        parent_node = (
                            self.route_tracker.graph.nodes.get(normalized)
                            if hasattr(self.route_tracker, "graph")
                            else None
                        )
                        current_depth = parent_node.depth if parent_node else 0

                        # Record the parent-child route edge in RouteGraph
                        if hasattr(self.route_tracker, "graph"):
                            nav_cause = "safe_button_navigation" if (item.get("tagName") == "button" or item.get("role") == "button") else "content_link"
                            self.route_tracker.graph.add_route(
                                route=child_route,
                                parent_route=normalized,
                                navigation_cause=nav_cause,
                                return_strategy="navigate_url",
                                menu_name=item["label"],
                            )

                        # Prevent duplicate traversal using normalized route, route tracker, and page intelligence
                        is_duplicate = (
                            self.route_tracker.is_visited(child_route)
                            or child_route in self.page_intelligence
                        )

                        if is_duplicate:
                            logger.info(f"Duplicate child route skipped: {normalized} -> {child_route}")
                            self._emit_event("duplicate_route_skipped", {
                                "parent_route": normalized,
                                "child_route": child_route,
                                "action": item["label"],
                                "element_identity": elem_ident,
                            })
                        elif current_depth < 5:
                            logger.info(
                                "Safe action revealed child route: %s -> %s (depth=%d)",
                                normalized, child_route, current_depth + 1,
                            )
                            # Save parent checkpoint before scanning child route
                            self._save_checkpoint()
                            # Scan child route
                            self._scan_route(
                                child_route,
                                menu_name=f"{menu_name} > {item['label']}",
                                expected_url=child_route,
                            )
                            # Save child checkpoint after scan
                            self._save_checkpoint()

                    # Return to parent route and restore scroll position
                    self._restore_parent_state(normalized, scroll_pos)

                else:
                    # In-page safe action (tabs, accordions, pagination, dropdown options, filters, sorts)
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

                    if probe_res.get("dom_delta", 0) > 0 or state_after["ui_state_id"] != state_before["ui_state_id"]:
                        extra = build_ui_intelligence(
                            self.page,
                            feature_name=f"{menu_name} > {item['label']}",
                            menu_name=menu_name,
                            global_seen=self._global_seen,
                            deadline=self._route_deadline if self._route_deadline > 0 else (time.monotonic() + 10.0),
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

                    try:
                        self.page.evaluate("(pos) => window.scrollTo(pos.x || 0, pos.y || 0)", scroll_pos)
                    except Exception:
                        pass

            except Exception as exc:
                logger.debug(f"Failed probing safe action '{item.get('label')}': {exc}")
                self._restore_parent_state(normalized, scroll_pos)

    def _build_telemetry_snapshot(self, event_type: str) -> dict[str, Any]:
        validations = self.route_validations or {}
        working_count = sum(1 for v in validations.values() if v.get("status") == "WORKING")
        partial_count = sum(1 for v in validations.values() if v.get("status") == "PARTIAL")
        non_working_count = sum(1 for v in validations.values() if v.get("status") in {"NON-WORKING", "NON_WORKING_ROUTE"})

        status = "running"
        if event_type == "scan_completed":
            status = "scanned"
        elif event_type in {"scan_stopped", "scan_budget_exceeded"}:
            status = "partial_scanned"
        elif event_type in {"scan_failed", "route_failed"}:
            status = "failed"
        elif event_type == "module_review_started":
            status = "paused"
        elif event_type == "scan_paused":
            status = "paused"
        elif self.run_id:
            try:
                from api.services.scan_control_service import is_scan_paused
                if is_scan_paused(self.run_id):
                    status = "paused"
            except Exception:
                pass

        try:
            current_route = getattr(self.page, "url", "") or ""
        except Exception:
            current_route = ""
        current_module = getattr(self, "_active_module_id", "") or ""

        total_elements = len(self.element_registry) if hasattr(self, "element_registry") else 0
        if total_elements == 0:
            total_elements = sum(len(d.get("automation_targets", [])) for d in self.page_intelligence.values())
        manual_elements = sum(
            1 for item in self.element_registry.get_all_elements()
            if item.get("is_user_confirmed")
        ) if hasattr(self, "element_registry") else 0

        safe_actions_explored = len(getattr(self, "_probed_action_identities", set()))
        parking_lot_actions = len(getattr(self, "parking_lot_actions", []))

        mod_total = len(self._selected_module_ids or []) if self._selected_module_ids else 1
        mod_curr = min(mod_total, len(getattr(self, "completed_modules", [])) + 1)

        routes_scanned = len(self.page_intelligence)
        routes_pending = len(getattr(self, "_discovered_route_queue", []))

        errors_count = len(getattr(self, "_current_console_errors", [])) + non_working_count

        route_total = routes_scanned + routes_pending
        return {
            "run_id": self.run_id or "",
            "scan_id": self.scan_id or self.run_id or "",
            "target_origin": self.base_url,
            "allowed_path_namespace": self._checkpoint_namespace(),
            "auth_status": "expired" if self._is_login_page() else "authenticated",
            "selected_modules": list(self._selected_module_ids or []),
            "timestamp": time.time(),
            "status": status,
            "current_module": current_module,
            "current_route": current_route,
            "module_progress": {"current": mod_curr, "total": mod_total},
            "route_progress": {"scanned": routes_scanned, "pending": routes_pending, "total": route_total},
            "elements_found": total_elements,
            "manual_interaction_count": len(getattr(self, "manual_interactions", [])),
            "manual_element_count": manual_elements,
            "safe_actions_explored": safe_actions_explored,
            "parking_lot_actions": parking_lot_actions,
            "route_health": {
                "working_count": working_count,
                "partial_count": partial_count,
                "non_working_count": non_working_count,
            },
            "retry_count": getattr(self, "_retry_count", 0),
            "errors": errors_count,
            "last_event_sequence": len(self.scan_events) + 1,
        }

    def _check_pause_or_stop(self) -> None:
        if self._global_deadline and time.monotonic() >= self._global_deadline:
            self.scan_stop_requested = True
            self._emit_event("scan_budget_exceeded", {"reason": "global_timeout"})
            return
        if not self.run_id:
            return
        try:
            from api.services.scan_control_service import is_scan_paused, is_scan_stop_requested
            if is_scan_stop_requested(self.run_id):
                self.scan_stop_requested = True
                return
            while is_scan_paused(self.run_id) and not self.scan_stop_requested:
                self._emit_event("scan_paused", {"route": getattr(self.page, "url", ""), "message": "Scan paused at route boundary."})
                time.sleep(0.5)
                if is_scan_stop_requested(self.run_id):
                    self.scan_stop_requested = True
                    break
        except Exception:
            pass

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        telemetry = self._build_telemetry_snapshot(event_type)
        merged_data = {**telemetry, **data}
        self.scan_events.append({
            "event": event_type,
            "event_type": event_type,
            "timestamp": time.time(),
            "data": merged_data,
        })
        if not self.event_callback:
            return
        try:
            self.event_callback(event_type, merged_data)
        except Exception:
            logger.debug(f"Scan event callback failed for {event_type}")

    # ------------------------------------------------------------------
    # Page stabilization
    # ------------------------------------------------------------------

    def _stabilize_page(self, route_label: str) -> dict[str, Any]:
        """Perform SPA readiness check and page stabilization."""
        try:
            return wait_for_spa_readiness(
                self.page,
                timeout=min(STABILIZATION_TIMEOUT, self._remaining_route_timeout(STABILIZATION_TIMEOUT)),
            )
        except Exception as exc:
            logger.debug(f"wait_for_spa_readiness encountered error on {route_label}: {exc}")
            return {
                "ready": True,
                "readiness_timeout_reached": False,
                "dom_element_count": 0,
                "visible_control_count": 0,
                "final_url": self.page.url,
            }

    # ------------------------------------------------------------------
    # Interactive learning
    # ------------------------------------------------------------------

    def _review_module(self, module_label: str, module_url: str) -> str:
        """Pause only after a selected module is complete, never per child route.

        Returns user decision: 'continue', 'retry', or 'end'.
        """
        if not self.interactive_mode or self.scan_stop_requested:
            return "continue"

        # Content-link traversal may leave the browser on a child screen.
        # Return to the selected module before showing the user's review UI.
        if module_url:
            try:
                expected = self.route_tracker.normalize_url(module_url)
                if self.route_tracker.normalize_url(self.page.url) != expected:
                    self._navigate(expected)
                    wait_for_ui_stability(self.page)
            except Exception:
                logger.warning(f"Could not restore selected module before review: {module_label}")

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
                return "continue"
            normalized = self.route_tracker.normalize_url(route_doc.get("route") or self.page.url)

        # Compute accurate module summary metrics
        mod_id = self._active_module_id or f"route:{normalized}"
        mod_routes = [
            r for r, d in self.page_intelligence.items()
            if d.get("menu_name") == module_label
            or any(t.get("module_id") == mod_id for t in d.get("automation_targets", []))
            or r == normalized
        ]
        elements_count = sum(
            len(self.page_intelligence[r].get("automation_targets", []))
            for r in mod_routes if r in self.page_intelligence
        )
        safe_actions_count = sum(
            len([
                a for a in getattr(self.route_tracker.graph.get_node(r), "actions", [])
                if is_safe_action(str(a.get("classification") or "").lower())
            ])
            for r in mod_routes if hasattr(self.route_tracker, "graph") and self.route_tracker.graph.get_node(r)
        )
        parking_count = len([
            p for p in self.parking_lot_actions
            if p.get("route") in mod_routes or p.get("module_id") == mod_id
        ])
        route_val = self.route_validations.get(normalized, {})
        route_health = route_val.get("status", "HEALTHY")
        error_count = len([
            r for r in mod_routes
            if self.page_intelligence.get(r, {}).get("scan_error")
            or self.route_validations.get(r, {}).get("status") in {"NON-WORKING", "NON_WORKING_ROUTE"}
        ])

        summary = {
            "module_name": module_label,
            "module_id": mod_id,
            "routes_scanned": len(mod_routes),
            "route_count": len(mod_routes),
            "elements_found": elements_count,
            "element_count": elements_count,
            "safe_actions_explored": safe_actions_count,
            "parking_lot_count": parking_count,
            "route_health": route_health,
            "errors_count": error_count,
            "total_routes_in_run": len(self.page_intelligence),
        }

        self._emit_event("module_review_started", {
            "route": normalized,
            "module_name": module_label,
            "summary": summary,
            "message": "Selected module is complete; waiting for explicit continue, retry, or end decision.",
        })

        choice = self._capture_interactions(normalized, route_doc, summary=summary)

        if choice == "timeout":
            # A review timeout is not a crawler failure. Preserve automatic
            # evidence and advance; Retry/End remain explicit user choices.
            self._emit_event("module_review_timeout", {
                "route": normalized,
                "module_name": module_label,
                "message": "No manual decision arrived before the review window; automatic evidence was preserved and the next module will start.",
            })
            choice = "continue"

        self._emit_event("module_review_completed", {
            "route": normalized,
            "module_name": module_label,
            "decision": choice,
            "interaction_count": len(route_doc.get("interactions", [])),
            "manual_interaction_count": len(self.manual_interactions),
            "manual_element_count": sum(
                1 for item in self.element_registry.get_all_elements()
                if item.get("is_user_confirmed")
            ),
            "elements_found": len(self.element_registry),
            "interactions": route_doc.get("interactions", [])[-20:],
            "ended": self.scan_stop_requested,
        })
        return choice

    def _capture_interactions(
        self, normalized: str, route_doc: dict[str, Any], summary: dict[str, Any] | None = None,
    ) -> str:
        logger.info(f"Interactive mode: capturing on {normalized}")
        try:
            if summary:
                inject_capture(self.page, summary=summary)
            else:
                inject_capture(self.page)
        except Exception:
            logger.exception("Interactive mode: injection failed, falling back to passive scan")
            route_doc["interactions"] = []
            return "continue"

        run_id = getattr(self, "run_id", None)
        try:
            choice = wait_for_user(self.page, timeout_seconds=self.interactive_timeout, run_id=run_id)
        except TypeError:
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
            # Do not erase evidence from an earlier retry of this module.
            route_doc.setdefault("interactions", [])
            route_doc.setdefault("interaction_flow", [])
            return choice

        logger.info(
            f"Interactive mode: {len(interactions)} interactions captured on {normalized}"
        )
        existing_interactions = list(route_doc.get("interactions", []))
        seen_interactions = {
            (item.get("type"), item.get("selector"), item.get("page_url"), item.get("xpath"))
            for item in existing_interactions
        }
        for interaction in interactions:
            key = (
                interaction.get("type"), interaction.get("selector"),
                interaction.get("page_url"), interaction.get("xpath"),
            )
            if key not in seen_interactions:
                existing_interactions.append(interaction)
                seen_interactions.add(key)
        route_doc["interactions"] = existing_interactions

        existing_flow = list(route_doc.get("interaction_flow", []))
        existing_flow_keys = {
            (item.get("step"), item.get("selector"), item.get("page"), item.get("xpath"))
            for item in existing_flow
        }
        for step in flow:
            key = (step.get("step"), step.get("selector"), step.get("page"), step.get("xpath"))
            if key not in existing_flow_keys:
                existing_flow.append(step)
                existing_flow_keys.add(key)
        route_doc["interaction_flow"] = existing_flow
        user_targets = interactions_to_targets(
            interactions,
            framework=route_doc.get("framework", "unknown"),
        )
        for index, target in enumerate(user_targets, start=1):
            # The capture script records the XPath at the instant of the user
            # action.  Prefer that durable evidence over recalculating it from
            # the current page, which may now be a different route.
            target["element_xpath"] = interactions[index - 1].get("xpath") or self._xpath_for_selector(target, index)
            target["source"] = interactions[index - 1].get("source", "interactive_learning")
            target["source_page"] = interactions[index - 1].get("page_url", normalized)
            target["source_html"] = interactions[index - 1].get("element_html", "")
            target["element_role"] = target.get("element_role") or self._role_for_target(target)
            target["element_index"] = int(target.get("element_index") or index)
            target["is_user_confirmed"] = True
            target["discovery_mechanism"] = "interactive_learning"
            target["interaction_probability"] = 1.0
            target["interaction_probability_method"] = "user_confirmed_interaction"
            ident, is_new = self.element_registry.register(normalized, target)
            target["id"] = ident
            target["hierarchy_level"] = 2
            if is_new:
                route_doc["automation_targets"].append(target)
                self._emit_event("element_found", {
                    "route": normalized, "id": ident,
                    "element_type": target.get("type", ""), "label": target.get("label", ""),
                })
            else:
                # The registry may already contain the same element from the
                # passive scan. Update the route document too, because some
                # callers hold a copied target rather than the registry dict.
                for existing_target in route_doc.get("automation_targets", []):
                    existing_identity = existing_target.get("element_identity") or existing_target.get("id")
                    if existing_identity == ident:
                        existing_target.update({
                            "is_user_confirmed": True,
                            "discovery_mechanism": "interactive_learning",
                            "source": target.get("source", "interactive_learning"),
                            "source_page": target.get("source_page", normalized),
                            "source_html": target.get("source_html", ""),
                            "element_xpath": target.get("element_xpath", ""),
                            "xpath": target.get("element_xpath", ""),
                            "interaction_probability": 1.0,
                            "interaction_probability_method": "user_confirmed_interaction",
                            "interaction_type": target.get("interaction_type", "interact"),
                        })
                        break

        existing_user_targets = list(route_doc.get("user_confirmed_elements", []))
        known_user_targets = {
            (item.get("selector", {}).get("primary", {}).get("value"), item.get("element_xpath"))
            for item in existing_user_targets
        }
        for target in user_targets:
            key = (target.get("selector", {}).get("primary", {}).get("value"), target.get("element_xpath"))
            if key not in known_user_targets:
                existing_user_targets.append(target)
                known_user_targets.add(key)
        route_doc["user_confirmed_elements"] = existing_user_targets
        route_doc["sections"] = _recount_sections(route_doc["automation_targets"])
        self.manual_interactions.extend(
            {**interaction, "route": normalized} for interaction in interactions
        )
        self.interaction_flow.extend({**step, "route": normalized} for step in flow)
        # Persist immediately at the human checkpoint. This makes manual
        # evidence durable even if the browser closes before Continue/End.
        self._save_checkpoint()
        return choice


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

    def _restore_parent_state(self, parent_url: str, scroll_pos: dict[str, Any] | None = None) -> bool:
        """Safely return to parent route and restore scroll position."""
        parent_norm = self.route_tracker.normalize_url(parent_url)
        try:
            curr_norm = self.route_tracker.normalize_url(self.page.url)
            if curr_norm != parent_norm:
                self._navigate(parent_norm)
                self._ensure_authenticated()
                wait_for_ui_stability(self.page)
            if scroll_pos:
                try:
                    self.page.evaluate("(pos) => window.scrollTo(pos.x || 0, pos.y || 0)", scroll_pos)
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.warning(f"Could not restore parent route '{parent_norm}': {exc}")
            try:
                self.page.goto(parent_norm, wait_until="domcontentloaded", timeout=min(ROUTE_NAVIGATION_TIMEOUT, 6000))
                self._ensure_authenticated()
                wait_for_ui_stability(self.page)
                if scroll_pos:
                    self.page.evaluate("(pos) => window.scrollTo(pos.x || 0, pos.y || 0)", scroll_pos)
                return True
            except Exception:
                logger.error(f"Failed secondary fallback to restore parent route '{parent_norm}'")
                return False

    def _remaining_route_timeout(self, preferred_ms: int = ROUTE_SCAN_TIMEOUT) -> int:
        deadlines = [deadline for deadline in (self._route_deadline, self._global_deadline) if deadline]
        if not deadlines:
            return preferred_ms
        return max(1, min(preferred_ms, int((min(deadlines) - time.monotonic()) * 1000)))

    def _record_api_response(self, response: Any) -> None:
        try:
            if response.request.resource_type not in {"fetch", "xhr"}:
                return
            if not is_internal_url(response.url, self.base_url):
                return
            self._api_responses.append({"url": response.url, "status": response.status})
        except Exception:
            return

    def _record_console_message(self, msg: Any) -> None:
        try:
            mtype = getattr(msg, "type", "")
            if mtype in {"error", "warning"}:
                text = str(getattr(msg, "text", ""))
                if len(self._current_console_errors) < 25:
                    self._current_console_errors.append(f"{mtype}: {text[:150]}")
        except Exception:
            pass

    def _record_page_error(self, err: Any) -> None:
        try:
            if len(self._current_console_errors) < 25:
                self._current_console_errors.append(f"pageerror: {str(err)[:150]}")
        except Exception:
            pass

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
            self.manual_interactions = list(data.get("manual_interactions", []))
            self.interaction_flow = list(data.get("interaction_flow", []))
            # The checkpoint keeps manual evidence separately from page docs
            # so a restart cannot discard a human-taught locator.
            for interaction in self.manual_interactions:
                route = str(interaction.get("route") or interaction.get("page_url") or "")
                if route in self.page_intelligence:
                    self.page_intelligence[route].setdefault("interactions", []).append(interaction)
            for step in self.interaction_flow:
                route = str(step.get("route") or step.get("page") or "")
                if route in self.page_intelligence:
                    self.page_intelligence[route].setdefault("interaction_flow", []).append(step)
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

            self.completed_modules = list(data.get("completed_modules", []))
            self.module_decisions = dict(data.get("module_decisions", {}))
            self._active_module_id = data.get("current_module_id", "")
            if not self._selected_module_ids and data.get("selected_modules"):
                self._selected_module_ids = list(data["selected_modules"])

            logger.info(f"Resumed scan checkpoint with {len(self.page_intelligence)} routes")
        except Exception:
            logger.exception("Unable to process checkpoint; starting fresh scan")

    def _reset_module_state(self, module_id: str, menu_item: dict[str, Any]) -> None:
        """Clear cached intelligence, visited state, and registered elements for a module so it can be re-scanned."""
        label = menu_item.get("label", "")
        url = self._normalize(menu_item.get("url", "")) if menu_item.get("url") else ""

        routes_to_reset = set()
        if url:
            routes_to_reset.add(url)
        for r, doc in list(self.page_intelligence.items()):
            if doc.get("menu_name") == label or any(t.get("module_id") == module_id for t in doc.get("automation_targets", [])):
                routes_to_reset.add(r)

        for r in routes_to_reset:
            self.page_intelligence.pop(r, None)
            self.route_validations.pop(r, None)
            self.route_tracker.unmark_visited(r)
            if hasattr(self.route_tracker, "graph"):
                node = self.route_tracker.graph.get_node(r)
                if node:
                    node.visited = False
            self._queued_routes.discard(r)
            if hasattr(self.element_registry, "unregister_route"):
                self.element_registry.unregister_route(r)

        self.parking_lot_actions = [
            p for p in self.parking_lot_actions
            if p.get("route") not in routes_to_reset and p.get("module_id") != module_id
        ]

        self._discovered_route_queue = [
            q for q in self._discovered_route_queue if q.get("url") not in routes_to_reset
        ]

    def _save_checkpoint(self) -> None:
        if not self.checkpoint_store:
            return
        try:
            session_id = (self.checkpoint_path.stem.removeprefix("checkpoint_")
                          if self.checkpoint_path else "")
            curr_url = ""
            scroll_pos = {"x": 0, "y": 0}
            try:
                if self.page and not self.page.is_closed():
                    curr_url = self.page.url
                    scroll_pos = self.page.evaluate("() => ({ x: window.scrollX || 0, y: window.scrollY || 0 })")
            except Exception:
                pass

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            visited = sorted(self.route_tracker.get_visited_routes())
            completed_routes = sorted(r for r in visited if r in self.route_validations)

            data = {
                "schema_version": "2.0",
                "timestamp": now_iso,
                "session_id": session_id,
                "run_id": self.run_id or session_id,
                "base_url": self.base_url,
                "namespace": self._checkpoint_namespace(),
                "allowed_scope": self._checkpoint_namespace(),
                "auth_status": "authenticated" if (self.credentials or not self._is_login_page()) else "expired",
                "current_route": curr_url,
                "scroll_position": scroll_pos,
                "selected_modules": list(self._selected_module_ids or []),
                "visited_routes": visited,
                "completed_routes": completed_routes,
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
                "manual_interactions": self.manual_interactions,
                "interaction_flow": self.interaction_flow,
                "manual_checkpoint_state": {
                    "scan_stop_requested": self.scan_stop_requested,
                    "interactive_mode": self.interactive_mode,
                },
                "current_module_id": self._active_module_id,
                "completed_modules": list(self.completed_modules),
                "module_decisions": dict(self.module_decisions),
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
            return (not self.session_manager.session_state_trusted) and self.page.locator('input[type="password"]').first.is_visible()
        except Exception:
            return False

    def _normalize(self, url: str) -> str:
        return self.route_tracker.normalize_url(urljoin(self.page.url or self.base_url, url))
