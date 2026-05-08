from playwright.sync_api import Locator, Page


SIDEBAR_SELECTORS = [
    "aside",
    "aside nav",
    "[class*='sidebar']",
    "[class*='side-nav']",
    "[aria-label*='sidebar']",
]

TOP_NAV_SELECTORS = [
    "header nav",
    "nav",
    "[role='navigation']",
    "[class*='navbar']",
    "[class*='top-nav']",
]

MENU_ITEM_SELECTOR = "a, button, [role='menuitem'], [role='link'], [role='button']"


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _extract_visible_items(menu: Locator, container_selector: str) -> list[dict[str, str | None]]:
    items = []
    menu_items = menu.locator(MENU_ITEM_SELECTOR)

    for index in range(menu_items.count()):
        item = menu_items.nth(index)

        if not item.is_visible():
            continue

        text = _clean_text(item.inner_text())
        if not text:
            continue

        items.append(
            {
                "text": text,
                "href": item.get_attribute("href"),
                # Container selector lets the crawler re-locate this item later
                # via get_by_role/text scoped to its menu container.
                "container_selector": container_selector,
            }
        )

    return items


def _detect_menu_group(page: Page, selectors: list[str], menu_type: str) -> list[dict[str, object]]:
    menus = []
    seen_signatures = set()

    for selector in selectors:
        containers = page.locator(selector)

        for index in range(containers.count()):
            container = containers.nth(index)

            if not container.is_visible():
                continue

            items = _extract_visible_items(container, selector)
            if not items:
                continue

            signature = tuple(item["text"] for item in items)
            if signature in seen_signatures:
                continue

            seen_signatures.add(signature)
            menus.append(
                {
                    "type": menu_type,
                    "selector": selector,
                    "items": items,
                }
            )

    return menus


def detect_menus(page: Page) -> dict[str, list[dict[str, object]]]:
    return {
        "sidebar_menus": _detect_menu_group(page, SIDEBAR_SELECTORS, "sidebar"),
        "top_navigation_menus": _detect_menu_group(page, TOP_NAV_SELECTORS, "top_navigation"),
    }
