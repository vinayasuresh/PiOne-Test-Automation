from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from automation_framework.config.settings import HEADLESS


def start_browser() -> tuple[Playwright, Browser, Page]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=HEADLESS)
    page = browser.new_page()

    return playwright, browser, page


def start_persistent_browser(
    storage_state_path: str | Path | None = None,
) -> tuple[Playwright, Browser, BrowserContext, Page]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=HEADLESS)

    context_options = {}
    if storage_state_path and Path(storage_state_path).exists():
        context_options["storage_state"] = str(storage_state_path)

    context = browser.new_context(**context_options)
    page = context.new_page()

    return playwright, browser, context, page


def save_storage_state(context: BrowserContext, storage_state_path: str | Path) -> None:
    """Persist all supported browser authentication state for later execution.

    Modern SPAs may store the session in IndexedDB rather than cookies alone.
    Playwright versions before IndexedDB export support still work via the
    compatibility fallback.
    """
    try:
        context.storage_state(path=str(storage_state_path), indexed_db=True)
    except TypeError:
        context.storage_state(path=str(storage_state_path))
