import os
import sys
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from automation_framework.config.settings import HEADLESS


def _launch_headless() -> bool:
    """Use headed mode for local interactive work, headless mode on CI/servers."""
    if HEADLESS or os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
        return True
    # Linux workers without a display cannot launch Chromium in headed mode.
    return sys.platform.startswith("linux") and not os.getenv("DISPLAY")


def start_browser() -> tuple[Playwright, Browser, Page]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=_launch_headless())
    page = browser.new_page()

    return playwright, browser, page


def start_persistent_browser(
    storage_state_path: str | Path | None = None,
) -> tuple[Playwright, Browser, BrowserContext, Page]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=_launch_headless())

    context_options = {}
    session_storage = {}
    if storage_state_path and Path(storage_state_path).exists():
        state = json.loads(Path(storage_state_path).read_text(encoding="utf-8"))
        session_storage = state.pop("_omnitest_session_storage", {})
        context_options["storage_state"] = state

    context = browser.new_context(**context_options)
    if session_storage:
        # sessionStorage is tab scoped and is not included in Playwright's
        # storage_state. Restore only the exact captured origin, once per tab.
        payload = json.dumps(session_storage).replace("<", "\\u003c")
        context.add_init_script(script=f"""(() => {{
            const saved = {payload};
            const marker = '__omnitest_session_restored__';
            if (!Object.prototype.hasOwnProperty.call(saved, location.origin) || sessionStorage.getItem(marker)) return;
            for (const [key, value] of Object.entries(saved[location.origin])) sessionStorage.setItem(key, value);
            sessionStorage.setItem(marker, '1');
        }})()""")
    page = context.new_page()

    return playwright, browser, context, page


def save_storage_state(context: BrowserContext, storage_state_path: str | Path) -> None:
    """Persist all supported browser authentication state for later execution.

    Modern SPAs may store the session in IndexedDB rather than cookies alone.
    Playwright versions before IndexedDB export support still work via the
    compatibility fallback.
    """
    try:
        state = context.storage_state(indexed_db=True)
    except TypeError:
        state = context.storage_state()
    sessions = {}
    for page in context.pages:
        try:
            parsed = urlparse(page.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            origin = f"{parsed.scheme}://{parsed.netloc}"
            values = page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))")
            values.pop("__omnitest_session_restored__", None)
            if values:
                sessions[origin] = values
        except Exception:
            continue
    if sessions:
        state["_omnitest_session_storage"] = sessions
    Path(storage_state_path).write_text(json.dumps(state), encoding="utf-8")
