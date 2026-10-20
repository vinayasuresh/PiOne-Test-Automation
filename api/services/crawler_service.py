"""Synchronous crawler service — wraps the Playwright framework for the API.

Designed to run inside ``asyncio.to_thread`` so the FastAPI event loop stays
responsive. The contract is:

* On success            → ``ScanResponse(status="success")``
* On crawl failure      → ``ScanResponse(status="partial_success")`` with
                          everything we collected before the error.
* The browser is closed in ``finally`` no matter what.
"""

from __future__ import annotations

from pathlib import Path
import time
from uuid import uuid4

from automation_framework.config.settings import REPORT_PATH, ROUTE_LOAD_TIMEOUT, STORAGE_STATE_PATH
from automation_framework.crawler.crawl_exporter import export_crawl_results
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.navigation_expander import ensure_navigation_expanded
from automation_framework.crawler.ui_wait_engine import wait_for_ui_stability
from automation_framework.crawler.url_filter import install_document_scope_guard, is_valid_url
from automation_framework.utils.browser_manager import save_storage_state, start_persistent_browser
from automation_framework.utils.logger import configure_run_logger, logger
from automation_framework.utils.metadata_collector import generate_run_id

from api.models.scan_models import ScanResponse
from api.services.auth_lifecycle_service import (
    AuthenticationExpiredError,
    check_auth_session_validity,
)
from api.services.run_context_service import (
    create_run_context,
    get_run_context,
    save_run_context,
)
from api.services.scan_event_bus import publish, register_alias
from api.utils.response_builder import build_scan_response


def run_scan(
    url: str,
    username: str | None,
    password: str | None,
    interactive_mode: bool = False,
    interactive_timeout: int = 300,
    resume_run_id: str | None = None,
    scan_id: str | None = None,
    replay_route: str | None = None,
    selected_module_ids: list[str] | None = None,
    auth_session_id: str | None = None,
    run_id: str | None = None,
) -> ScanResponse:
    """Run a full browser-based scan and ALWAYS return a ScanResponse.

    Even when the crawler raises mid-flight, we collect whatever has been
    stored on the engine so far and return a ``partial_success`` response.
    The only situation where this raises is when login itself fails before
    we have any results to return.
    """
    actual_run_id = run_id or scan_id or resume_run_id or generate_run_id()
    configure_run_logger(actual_run_id)
    register_alias(actual_run_id, scan_id or actual_run_id)
    if scan_id:
        register_alias(scan_id, actual_run_id)

    logger.info(f"[API] Scan started | run_id={actual_run_id} | url={url}")
    start_time = time.monotonic()

    playwright = None
    browser    = None
    context    = None
    engine: CrawlerEngine | None = None

    try:
        # ── Boot browser ────────────────────────────────────────────────
        state_path = _auth_state_path(auth_session_id) if auth_session_id else STORAGE_STATE_PATH
        if auth_session_id:
            if not state_path.exists():
                raise AuthenticationExpiredError("The module-selection session has expired. Sign in and discover modules again.")
            valid, reason = check_auth_session_validity(url, auth_session_id, actual_run_id)
            if not valid:
                raise AuthenticationExpiredError(f"Authentication expired ({reason}). Refresh the session to continue.")
        playwright, browser, context, page = start_persistent_browser(state_path)
        # Protect the initial navigation and any redirects during login too;
        # the crawler engine is created only after this part of the flow.
        install_document_scope_guard(
            page,
            url,
            (lambda event_type, data: publish(
                scan_id, {"type": event_type, "scan_id": scan_id, **data}
            )) if scan_id else None,
        )

        # ── Determine starting URL ──────────────────────────────────────
        target_nav_url = url
        start_module_url = None
        if not (username and password):
            if actual_run_id:
                try:
                    run_ctx = get_run_context(actual_run_id)
                    if run_ctx and run_ctx.discovered_modules:
                        if selected_module_ids:
                            for mod_id in selected_module_ids:
                                for mod in run_ctx.discovered_modules:
                                    if (mod.get("id") == mod_id or mod.get("module_id") == mod_id) and mod.get("route"):
                                        start_module_url = mod.get("route")
                                        break
                                if start_module_url:
                                    break
                        if not start_module_url:
                            for mod in run_ctx.discovered_modules:
                                r = mod.get("route")
                                if r and not r.endswith(("/", "/login", "/signin")):
                                    start_module_url = r
                                    break
                except Exception as e:
                    logger.debug(f"[API] Error resolving start module url: {e}")

            if not start_module_url and selected_module_ids:
                for mod_id in selected_module_ids:
                    if str(mod_id).startswith("route:"):
                        cand = str(mod_id)[6:]
                        if is_valid_url(cand, url):
                            start_module_url = cand
                            break

            from urllib.parse import urlparse, urljoin
            parsed_target = urlparse(url)
            if (not parsed_target.path or parsed_target.path == "/") and start_module_url:
                target_nav_url = urljoin(url, start_module_url)
            elif (not parsed_target.path or parsed_target.path == "/") and "saucedemo.com" in parsed_target.netloc:
                target_nav_url = "https://www.saucedemo.com/inventory.html"

        # ── Login (or direct nav) ───────────────────────────────────────
        if username and password:
            logger.info(f"[API] Logging in | user={username}")
            login(page, username, password, url)
            try:
                save_storage_state(context, STORAGE_STATE_PATH)
            except Exception:
                pass
            logger.info("[API] Login successful")
        else:
            logger.info(f"[API] No credentials provided — navigating to {target_nav_url}")
            page.goto(target_nav_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
            if auth_session_id:
                try:
                    if page.locator('input[type="password"]').first.is_visible(timeout=1000):
                        if start_module_url and target_nav_url != start_module_url:
                            logger.info(f"[API] Retrying on protected route: {start_module_url}")
                            page.goto(start_module_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
                        if page.locator('input[type="password"]').first.is_visible(timeout=1000):
                            raise AuthenticationExpiredError("Authentication expired. Refresh the session to continue.")
                except AuthenticationExpiredError:
                    raise
                except Exception:
                    pass

        # ── Optional nav expansion ──────────────────────────────────────
        try:
            ensure_navigation_expanded(page)
        except Exception:
            logger.warning("[API] Navigation expansion failed; continuing anyway")

        # ── Crawl ──────────────────────────────────────────────────────
        credentials = (username, password) if username and password else None
        engine = CrawlerEngine(
            page,
            base_url=url,
            credentials=credentials,
            interactive_mode=interactive_mode,
            interactive_timeout=interactive_timeout,
            checkpoint_path=REPORT_PATH / f"checkpoint_{resume_run_id or actual_run_id}.json",
            resume_from_checkpoint=bool(resume_run_id),
            run_id=actual_run_id,
            scan_id=scan_id or actual_run_id,
            event_callback=(
                lambda event_type, data: publish(
                    actual_run_id, {"type": event_type, "scan_id": scan_id or actual_run_id, "run_id": actual_run_id, **data}
                )
            ),
        )
        if resume_run_id and not engine.checkpoint_loaded:
            raise ValueError("The requested checkpoint is missing, unreadable, or belongs to another app path")

        if interactive_mode:
            logger.info(
                f"[API] Interactive mode ENABLED | per-route timeout={interactive_timeout}s"
            )

        logger.info("[API] Crawler started")
        if replay_route:
            if not engine.checkpoint_loaded:
                raise ValueError("Route replay requires a valid checkpoint for this application")
            from urllib.parse import urljoin
            replay_route = urljoin(url, replay_route)
            if not is_valid_url(replay_route, url):
                raise ValueError("Replay route is outside the requested application path")
        try:
            if replay_route:
                normalized = engine.route_tracker.normalize_url(replay_route)
                previous = engine.page_intelligence.get(normalized, {})
                replay_api_marker = len(engine._api_responses)
                engine._navigate(replay_route)
                engine._ensure_authenticated()
                wait_for_ui_stability(page)
                crawl_results = engine.replay_route(
                    page.url, menu_name=previous.get("menu_name", "route_replay"),
                    api_marker=replay_api_marker,
                )
                old_targets = previous.get("automation_targets", [])
                current_targets = crawl_results.get("page_intelligence", {}).get(normalized, {}).get("automation_targets", [])
                old_by_id = {_element_identity(normalized, target): target for target in old_targets}
                new_by_id = {_element_identity(normalized, target): target for target in current_targets}
                crawl_results["replay_comparison"] = {
                    "route": normalized,
                    "previous_element_count": len(old_by_id),
                    "current_element_count": len(new_by_id),
                    "added_elements": sorted(set(new_by_id) - set(old_by_id)),
                    "removed_elements": sorted(set(old_by_id) - set(new_by_id)),
                    "changed_elements": sorted(
                        identity for identity in set(old_by_id) & set(new_by_id)
                        if old_by_id[identity] != new_by_id[identity]
                    ),
                    "unchanged_element_count": len(set(old_by_id) & set(new_by_id)),
                }
            else:
                crawl_results = engine.crawl(
                    start_url=page.url,
                    selected_module_ids=selected_module_ids,
                )
        except AuthenticationExpiredError:
            raise
        except Exception as crawl_exc:
            # Partial-success path: return whatever the engine collected.
            duration = time.monotonic() - start_time
            partial = _collect_partial(engine)
            logger.warning(
                f"[API] Crawl raised after {duration:.2f}s "
                f"| collected_routes={len(partial.get('page_intelligence', {}))} "
                f"| error={crawl_exc}"
            )
            _safe_export(partial, actual_run_id)
            try:
                run_ctx = get_run_context(actual_run_id)
                if not run_ctx:
                    run_ctx = create_run_context(url=url, auth_session_id=auth_session_id, username=username, run_id=actual_run_id)
                run_ctx.scan_results = partial
                run_ctx.status = "partial_scanned"
                save_run_context(run_ctx)
            except Exception:
                pass
            if scan_id:
                publish(scan_id, {"type": "scan_failed", "scan_id": scan_id,
                                  "error": str(crawl_exc), "routes": len(partial.get("page_intelligence", {}))})
            return build_scan_response(
                partial,
                duration,
                status="partial_success",
                message=f"Crawl interrupted: {crawl_exc}",
                auth_session_id=auth_session_id,
                run_id=actual_run_id,
            )

        # ── Done ───────────────────────────────────────────────────────
        duration = time.monotonic() - start_time
        route_count = len(crawl_results.get("page_intelligence", {}))
        logger.info("[API] Scan completed successfully")
        logger.info(f"[API] Total routes scanned: {route_count}")
        logger.info(f"[API] Duration: {duration:.2f}s")

        export_paths = _safe_export(crawl_results, actual_run_id)
        try:
            run_ctx = get_run_context(actual_run_id)
            if not run_ctx:
                run_ctx = create_run_context(url=url, auth_session_id=auth_session_id, username=username, run_id=actual_run_id)
            run_ctx.selected_modules = selected_module_ids or run_ctx.selected_modules
            run_ctx.scan_results = crawl_results
            run_ctx.route_graph = crawl_results.get("route_graph", {})
            run_ctx.ui_state_graph = crawl_results.get("ui_state_graph", {})
            run_ctx.element_registry = crawl_results.get("element_registry", {})
            if crawl_results.get("checkpoint_id") and crawl_results["checkpoint_id"] not in run_ctx.checkpoints:
                run_ctx.checkpoints.append(crawl_results["checkpoint_id"])
            if export_paths:
                run_ctx.artifact_paths.update(export_paths)
            run_ctx.status = "scanned"
            save_run_context(run_ctx)
        except Exception:
            logger.exception("[API] Failed to update RunContext with crawl results")
        if scan_id:
            publish(scan_id, {"type": "scan_completed", "scan_id": scan_id,
                              "routes": route_count, "duration_seconds": round(duration, 2)})
        stopped_early = bool(crawl_results.get("scan_stopped_early"))
        return build_scan_response(
            crawl_results,
            duration,
            status="partial_success" if stopped_early else "success",
            message=("Scan ended by user; exported routes and elements discovered so far."
                     if stopped_early else None),
            auth_session_id=auth_session_id,
            run_id=actual_run_id,
        )

    except Exception as exc:
        if scan_id:
            publish(scan_id, {"type": "scan_failed", "scan_id": scan_id,
                              "error": f"{type(exc).__name__}: {exc}"})
        raise

    finally:
        if context:
            try:
                save_storage_state(context, state_path)
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_partial(engine: CrawlerEngine | None) -> dict:
    """Return the engine's in-progress data shaped like ``crawl()``'s output."""
    if engine is None:
        return {"page_intelligence": {}, "routes": []}

    page_intel = getattr(engine, "page_intelligence", {}) or {}
    return {
        "page_intelligence": page_intel,
        "routes": list(page_intel.keys()),
        "manual_interactions": getattr(engine, "manual_interactions", []) or [],
        "interaction_flow": getattr(engine, "interaction_flow", []) or [],
        "component_registry": {},
        "feature_routes": getattr(engine, "feature_routes", {}) or {},
        "route_validations": getattr(engine, "route_validations", {}) or {},
        "menu_hierarchy": getattr(engine, "menu_hierarchy", {}) or {},
        "checkpoint_id": (getattr(engine, "checkpoint_path", None).stem.removeprefix("checkpoint_")
                          if getattr(engine, "checkpoint_path", None) else None),
        "route_namespace": (engine._checkpoint_namespace() if engine else ""),
    }


def _element_identity(route: str, target: dict) -> str:
    return target.get("element_identity") or "|".join((
        route, target.get("element_xpath", ""), target.get("element_role", ""),
        target.get("label", ""), str(target.get("element_index", 0)),
    ))


def _safe_export(crawl_results: dict, run_id: str) -> dict[str, str]:
    try:
        export_paths = export_crawl_results(crawl_results, run_id=run_id)
        logger.info(f"[API] Test-ready artifacts exported: {export_paths}")
        return export_paths or {}
    except Exception:
        logger.exception("[API] Failed to export test-ready artifacts")
        return {}


def discover_modules(url: str, username: str | None, password: str | None) -> dict:
    """Log in once and return a passive, current-app-only module catalogue.

    The saved Playwright storage state is scoped by an opaque selection-session
    ID. It contains browser auth state only and is not included in artifacts.
    """
    if not username or not password:
        raise ValueError("Credentials are required to discover live application modules")

    auth_session_id = uuid4().hex
    state_path = _auth_state_path(auth_session_id)
    playwright = browser = context = None
    try:
        playwright, browser, context, page = start_persistent_browser()
        install_document_scope_guard(page, url)
        login(page, username, password, url)
        try:
            ensure_navigation_expanded(page)
        except Exception:
            # Menu discovery has its own framework-agnostic detectors. A
            # failed optional sidebar expansion must not discard a valid
            # authenticated session or turn into a 500-level UI failure.
            logger.warning("[API] Navigation expansion failed during module discovery; continuing")
        save_storage_state(context, state_path)

        engine = CrawlerEngine(page, base_url=url, credentials=None)
        modules = engine.discover_modules()
        safe_modules = [
            {
                "id": item["id"],
                "module_id": item.get("module_id") or item["id"],
                "label": item["label"],
                "route": item.get("url", ""),
                "source": item.get("source", ""),
                "hierarchy_level": int(item.get("hierarchy_level", 0)),
                "parent_module": item.get("parent_module"),
                "confidence": float(item.get("confidence", 1.0)),
            }
            for item in modules
        ]

        # Initialize and persist RunContext
        run_ctx = create_run_context(url=url, auth_session_id=auth_session_id, username=username)
        run_ctx.discovered_modules = safe_modules
        run_ctx.status = "discovered"
        save_run_context(run_ctx)

        return {
            "run_id": run_ctx.run_id,
            "auth_session_id": auth_session_id,
            "modules": safe_modules,
        }
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass


def _auth_state_path(auth_session_id: str) -> Path:
    if not auth_session_id or not auth_session_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid module-selection session")
    state_dir = REPORT_PATH / "auth_sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{auth_session_id}.json"
