import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from automation_framework.config.settings import STABILIZATION_TIMEOUT
from automation_framework.utils.logger import logger


VISIBLE_REGION_SELECTOR = (
    "main, [role='main'], [class*='content' i], [class*='dashboard' i], "
    "table, [role='table'], [role='grid'], canvas, svg, [class*='map' i], "
    "[class*='chart' i], button, a[href], input, select, textarea"
)

LOADER_SELECTOR = (
    "[aria-busy='true'], [role='progressbar'], [class*='spinner' i], "
    "[class*='loader' i], [class*='loading' i], [class*='skeleton' i]"
)

DATA_REGION_SELECTOR = (
    "table, [role='table'], [role='grid'], canvas, svg, [class*='mapbox' i], "
    "[class*='leaflet' i], [class*='chart' i], [class*='analytics' i]"
)


def wait_for_spa_readiness(page: Page, timeout: int = STABILIZATION_TIMEOUT) -> dict[str, Any]:
    """Wait for SPA readiness and collect page readiness metrics.

    Checks:
    1. DOM content loaded.
    2. Meaningful content in application root/body (DOM count >= 5).
    3. Visible controls or bounded timeout.
    4. Loaders disappear & DOM mutation settling.
    """
    logger.info(f"Waiting for SPA readiness on page: {page.url}")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
    except PlaywrightTimeoutError:
        pass

    deadline = time.monotonic() + (timeout / 1000.0)
    ready = False
    readiness_timeout_reached = False
    dom_count = 0
    visible_count = 0

    while time.monotonic() < deadline:
        try:
            stats = page.evaluate("""() => {
                const domCount = document.body ? document.body.querySelectorAll('*').length : 0;
                const controls = document.body ? document.body.querySelectorAll(
                    "a[href], button, input:not([type='hidden']), select, textarea, [role='button'], [role='link']"
                ) : [];
                let visibleControls = 0;
                for (let i = 0; i < controls.length; i++) {
                    const el = controls[i];
                    const box = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden' && (box.width > 0 || box.height > 0)) {
                        visibleControls++;
                    }
                }
                return { domCount, visibleControls };
            }""")
            dom_count = stats.get("domCount", 0)
            visible_count = stats.get("visibleControls", 0)

            if dom_count >= 5:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.15)

    if time.monotonic() >= deadline and not ready:
        readiness_timeout_reached = True

    _wait_for_loaders_to_disappear(page, min(timeout, 2000))
    _wait_for_dom_to_settle(page, min(timeout, 2000))

    return {
        "ready": ready and not readiness_timeout_reached,
        "readiness_timeout_reached": readiness_timeout_reached,
        "dom_element_count": dom_count,
        "visible_control_count": visible_count,
        "final_url": page.url,
    }


def wait_for_ui_stability(page: Page, timeout: int = STABILIZATION_TIMEOUT) -> None:
    """Wait for the page to be interactive and stable.

    Three targeted steps (in order):
    1. Network idle  — ensures XHR/fetch traffic has quieted down.
    2. Loaders gone  — spinners/skeletons hidden before we scan.
    3. DOM settle    — short MutationObserver window to catch post-load renders.

    _wait_for_visible_interactive_region and _wait_for_data_regions were
    removed: they add latency without improving scan accuracy because both
    conditions are already implied by networkidle + loaders disappearing.
    """
    logger.info(f"Waiting for UI stability on page: {page.url}")

    _wait_for_network_idle(page, min(timeout, 4000))
    _wait_for_loaders_to_disappear(page, 2000)
    _wait_for_dom_to_settle(page, min(timeout, 3000))


def _wait_for_network_idle(page: Page, timeout: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        logger.warning("Network idle timeout during UI stabilization")


def _wait_for_loaders_to_disappear(page: Page, timeout: int) -> None:
    loaders = page.locator(LOADER_SELECTOR)

    try:
        if loaders.count() > 0:
            loaders.first.wait_for(state="hidden", timeout=timeout)
    except PlaywrightTimeoutError:
        logger.warning("Loader timeout during UI stabilization")


def _wait_for_visible_interactive_region(page: Page, timeout: int) -> None:
    try:
        page.locator(VISIBLE_REGION_SELECTOR).first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        logger.warning("Visible interactive region timeout during UI stabilization")


def _wait_for_data_regions(page: Page, timeout: int) -> None:
    data_regions = page.locator(DATA_REGION_SELECTOR)

    try:
        if data_regions.count() > 0:
            data_regions.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        logger.warning("Data region visibility timeout during UI stabilization")


def _wait_for_dom_to_settle(page: Page, timeout: int) -> None:
    try:
        page.wait_for_function(
            """
            (settleWindow) => new Promise((resolve) => {
                let timer;
                const observer = new MutationObserver(() => {
                    clearTimeout(timer);
                    timer = setTimeout(done, settleWindow);
                });
                const done = () => {
                    observer.disconnect();
                    resolve(true);
                };
                observer.observe(document.body, {
                    childList: true,
                    subtree: true,
                    attributes: true
                });
                timer = setTimeout(done, settleWindow);
            })
            """,
            arg=150,  # 150 ms settle window (was 300 ms)
            timeout=timeout,
        )
    except PlaywrightTimeoutError:
        logger.warning("DOM settling timeout during UI stabilization")
