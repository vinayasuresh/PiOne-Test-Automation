from typing import Any

from playwright.sync_api import Page

from automation_framework.utils.logger import logger


INTERACTION_SELECTOR = (
    "button, a[href], input:not([type='hidden']), textarea, select, "
    "[role='button'], [role='link'], [role='textbox'], [role='combobox'], [role='tab']"
)


def record_manual_interactions(page: Page) -> list[dict[str, Any]]:
    logger.info("Manual interaction recorder started")
    print("Manual exploration started. Interact with the browser, then press Enter here to stop.")

    page.evaluate(
        """
        (selector) => {
            window.__manualInteractions = [];

            const cleanText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const selectorFor = (element) => {
                if (element.getAttribute('data-testid')) {
                    return `[data-testid="${element.getAttribute('data-testid')}"]`;
                }
                if (element.getAttribute('aria-label')) {
                    return `${element.tagName.toLowerCase()}[aria-label="${element.getAttribute('aria-label')}"]`;
                }
                if (element.getAttribute('placeholder')) {
                    return `${element.tagName.toLowerCase()}[placeholder="${element.getAttribute('placeholder')}"]`;
                }
                if (element.id) {
                    return `#${CSS.escape(element.id)}`;
                }
                return element.tagName.toLowerCase();
            };

            const capture = (event) => {
                const element = event.target.closest(selector);
                if (!element) {
                    return;
                }

                window.__manualInteractions.push({
                    route: window.location.href,
                    intent: event.type,
                    selector: selectorFor(element),
                    role: element.getAttribute('role') || '',
                    label: element.getAttribute('aria-label') || cleanText(element.innerText) || element.getAttribute('placeholder') || '',
                    type: element.getAttribute('type') || element.tagName.toLowerCase()
                });
            };

            document.addEventListener('click', capture, true);
            document.addEventListener('change', capture, true);
        }
        """,
        INTERACTION_SELECTOR,
    )

    input()
    interactions = page.evaluate("() => window.__manualInteractions || []")
    logger.info(f"Manual interaction recorder captured {len(interactions)} interactions")

    return interactions
