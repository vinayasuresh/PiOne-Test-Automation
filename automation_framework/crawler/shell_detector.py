from typing import Any, Callable

from playwright.sync_api import Page

from automation_framework.crawler.menu_detector import detect_menus


def detect_application_shell(
    page: Page,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    menus = detect_menus(page, event_callback=event_callback)

    return {
        "current_url": page.url,
        "global_navigation": menus,
    }
