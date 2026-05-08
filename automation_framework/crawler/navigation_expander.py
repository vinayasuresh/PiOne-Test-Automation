import re

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, expect

from automation_framework.utils.logger import logger


SIDEBAR_SELECTORS = [
    "aside",
    "aside nav",
    "[role='navigation'][aria-label*='side' i]",
    "[class*='sidebar' i]",
    "[class*='sidenav' i]",
    "[class*='side-nav' i]",
]

NAVIGATION_ITEM_SELECTOR = "a, button, [role='menuitem'], [role='link'], [role='button']"
TOGGLE_BUTTON_NAME = re.compile(r"menu|navigation|sidebar|expand|collapse", re.IGNORECASE)


def ensure_navigation_expanded(page: Page, timeout: int = 5000) -> bool:
    sidebar = _find_visible_sidebar(page)

    if not sidebar:
        logger.info("Sidebar navigation not detected")
        return False

    logger.info("Sidebar navigation detected")

    if _has_visible_navigation_text(sidebar):
        logger.info("Sidebar navigation already expanded")
        return True

    toggle_button = _find_sidebar_toggle(page, sidebar)
    if not toggle_button:
        logger.warning("Sidebar toggle button not detected")
        return False

    toggle_button.click()
    if not _wait_for_navigation_items(sidebar, timeout):
        return False

    logger.info("Sidebar navigation expanded")
    return True


def _find_visible_sidebar(page: Page) -> Locator | None:
    for selector in SIDEBAR_SELECTORS:
        sidebars = page.locator(selector)

        for index in range(sidebars.count()):
            sidebar = sidebars.nth(index)
            if sidebar.is_visible():
                return sidebar

    return None


def _find_sidebar_toggle(page: Page, sidebar: Locator) -> Locator | None:
    semantic_toggle = page.get_by_role("button", name=TOGGLE_BUTTON_NAME)

    if semantic_toggle.count() > 0 and semantic_toggle.first.is_visible():
        return semantic_toggle.first

    fallback_toggles = page.locator(
        "button[aria-label*='menu' i], "
        "button[aria-label*='sidebar' i], "
        "button[aria-expanded], "
        "[role='button'][aria-label*='menu' i], "
        "[role='button'][aria-expanded]"
    )

    for index in range(fallback_toggles.count()):
        toggle = fallback_toggles.nth(index)
        if toggle.is_visible():
            return toggle

    sidebar_toggle = sidebar.locator("button, [role='button']")

    for index in range(sidebar_toggle.count()):
        toggle = sidebar_toggle.nth(index)
        if toggle.is_visible():
            return toggle

    return None


def _wait_for_navigation_items(sidebar: Locator, timeout: int) -> bool:
    try:
        visible_text_items = sidebar.locator(NAVIGATION_ITEM_SELECTOR).filter(
            has_text=re.compile(r"\S")
        )
        expect(visible_text_items.first).to_be_visible(timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        logger.warning("Timed out waiting for sidebar navigation items")
        return False


def _has_visible_navigation_text(sidebar: Locator) -> bool:
    navigation_items = sidebar.locator(NAVIGATION_ITEM_SELECTOR)

    for index in range(navigation_items.count()):
        item = navigation_items.nth(index)

        if not item.is_visible():
            continue

        if _clean_text(item.inner_text()):
            return True

    return False


def _clean_text(text: str) -> str:
    return " ".join(text.split())
