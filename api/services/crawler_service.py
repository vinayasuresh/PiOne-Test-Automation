"""Synchronous crawler service — wraps the Playwright framework for the API.

Designed to run inside ``asyncio.to_thread`` so the FastAPI event loop stays
responsive. The contract is:

* On success            → ``ScanResponse(status="success")``
* On crawl failure      → ``ScanResponse(status="partial_success")`` with
                          everything we collected before the error.
* The browser is closed in ``finally`` no matter what.
"""

import time

from automation_framework.config.settings import STORAGE_STATE_PATH
from automation_framework.crawler.crawl_exporter import export_crawl_results
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.navigation_expander import ensure_navigation_expanded
from automation_framework.utils.browser_manager import start_persistent_browser
from automation_framework.utils.logger import configure_run_logger, logger
from automation_framework.utils.metadata_collector import generate_run_id

from api.models.scan_models import ScanResponse
from api.utils.response_builder import build_scan_response


def run_scan(
    url: str,
    username: str | None,
    password: str | None,
    interactive_mode: bool = False,
    interactive_timeout: int = 300,
) -> ScanResponse:
    """Run a full browser-based scan and ALWAYS return a ScanResponse.

    Even when the crawler raises mid-flight, we collect whatever has been
    stored on the engine so far and return a ``partial_success`` response.
    The only situation where this raises is when login itself fails before
    we have any results to return.
    """
    run_id = generate_run_id()
    configure_run_logger(run_id)

    logger.info(f"[API] Scan started | run_id={run_id} | url={url}")
    start_time = time.monotonic()

    playwright = None
    browser    = None
    context    = None
    engine: CrawlerEngine | None = None

    try:
        # ── Boot browser ────────────────────────────────────────────────
        playwright, browser, context, page = start_persistent_browser(STORAGE_STATE_PATH)

        # ── Login (or direct nav) ───────────────────────────────────────
        if username and password:
            logger.info(f"[API] Logging in | user={username}")
            login(page, username, password, url)
            try:
                context.storage_state(path=str(STORAGE_STATE_PATH))
            except Exception:
                pass
            logger.info("[API] Login successful")
        else:
            logger.info("[API] No credentials provided — navigating directly")
            page.goto(url, wait_until="domcontentloaded")

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
        )

        if interactive_mode:
            logger.info(
                f"[API] Interactive mode ENABLED | per-route timeout={interactive_timeout}s"
            )

        logger.info("[API] Crawler started")
        try:
            crawl_results = engine.crawl(start_url=page.url)
        except Exception as crawl_exc:
            # Partial-success path: return whatever the engine collected.
            duration = time.monotonic() - start_time
            partial = _collect_partial(engine)
            logger.warning(
                f"[API] Crawl raised after {duration:.2f}s "
                f"| collected_routes={len(partial.get('page_intelligence', {}))} "
                f"| error={crawl_exc}"
            )
            _safe_export(partial, run_id)
            return build_scan_response(
                partial,
                duration,
                status="partial_success",
                message=f"Crawl interrupted: {crawl_exc}",
            )

        # ── Done ───────────────────────────────────────────────────────
        duration = time.monotonic() - start_time
        route_count = len(crawl_results.get("page_intelligence", {}))
        logger.info("[API] Scan completed successfully")
        logger.info(f"[API] Total routes scanned: {route_count}")
        logger.info(f"[API] Duration: {duration:.2f}s")

        _safe_export(crawl_results, run_id)
        return build_scan_response(crawl_results, duration, status="success")

    finally:
        if context:
            try:
                context.storage_state(path=str(STORAGE_STATE_PATH))
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
    }


def _safe_export(crawl_results: dict, run_id: str) -> None:
    try:
        export_paths = export_crawl_results(crawl_results, run_id=run_id)
        logger.info(f"[API] Test-ready artifacts exported: {export_paths}")
    except Exception:
        logger.exception("[API] Failed to export test-ready artifacts")
