from typing import Any

from playwright.sync_api import Page

from automation_framework.crawler.menu_detector import detect_menus


def detect_application_shell(page: Page) -> dict[str, Any]:
    menus = detect_menus(page)

    return {
        "current_url": page.url,
        "global_navigation": menus,
    }
