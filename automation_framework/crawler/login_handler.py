"""Deterministic login handler.

Flow (in order):
    1. Navigate to base_url and wait for any <input> + an <input type="password">.
    2. Pick username field by strict priority list.
    3. Pick password field (must be input[type="password"]).
    4. Pick submit button by strict priority list, excluding SSO/social buttons.
    5. Fill, small delay, click.
    6. Wait for networkidle + 3 s settle.
    7. Validate: if "login" still in URL → fail.

Only this module was changed; the public function signature
    login(page, username, password, base_url)
is unchanged so existing callers (main.py, crawler_engine.py,
api/services/crawler_service.py) continue to work.
"""

from playwright.sync_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from automation_framework.config.settings import SCREENSHOT_PATH
from automation_framework.utils.logger import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Username field selectors, tried in this exact order.
USERNAME_PRIORITY: tuple[str, ...] = (
    'input[type="email"]',
    'input[name*="email" i]',
    'input[name*="user" i]',
    'input[id*="email" i]',
    'input[id*="user" i]',
    'input[type="text"]:visible',
)

# SSO / social-login buttons we must never click.
SSO_KEYWORDS: tuple[str, ...] = (
    "google",
    "sso",
    "microsoft",
    "oauth",
    "facebook",
    "github",
    "apple",
    "azure",
    "okta",
    "linkedin",
    "twitter",
    "saml",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def login(page: Page, username: str, password: str, base_url: str) -> None:
    """Perform a deterministic email/username + password login.

    Raises RuntimeError on any failure. Captures a screenshot to
    screenshots/login_failure.png before raising.
    """
    try:
        logger.info(f"[login] Navigating to {base_url}")
        page.goto(base_url, wait_until="domcontentloaded")

        _wait_for_login_form(page)

        username_field, username_selector = _find_username_field(page)
        logger.info(f"[login] Username field selector: {username_selector}")

        password_field = _find_password_field(page)
        logger.info('[login] Password field selector: input[type="password"]')

        submit_button, submit_selector = _find_submit_button(page)
        logger.info(f"[login] Submit button selector: {submit_selector}")

        logger.info("[login] Filling credentials")
        username_field.fill(username)
        password_field.fill(password)
        page.wait_for_timeout(500)

        logger.info("[login] Submitting form")
        submit_button.click()

        _wait_for_login_completion(page)
        _validate_login_success(page)

        logger.info(f"[login] Login successful — current URL: {page.url}")

    except Exception as exc:
        _screenshot_failure(page)
        logger.error(f"[login] Login failed: {exc}")
        # Re-raise as RuntimeError so callers always see a uniform exception type.
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Login failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Step 1 — wait for the login form to be ready
# ---------------------------------------------------------------------------


def _wait_for_login_form(page: Page) -> None:
    try:
        page.wait_for_selector("input", timeout=8000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("no input fields found on the login page") from exc

    try:
        page.wait_for_selector('input[type="password"]', timeout=8000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("password field never appeared on the login page") from exc


# ---------------------------------------------------------------------------
# Step 2 — username field
# ---------------------------------------------------------------------------


def _find_username_field(page: Page) -> tuple[Locator, str]:
    for selector in USERNAME_PRIORITY:
        candidate = _first_interactable(page.locator(selector))
        if candidate is not None:
            return candidate, selector

    raise RuntimeError("could not locate a visible username/email field")


# ---------------------------------------------------------------------------
# Step 3 — password field
# ---------------------------------------------------------------------------


def _find_password_field(page: Page) -> Locator:
    candidate = _first_interactable(page.locator('input[type="password"]'))
    if candidate is None:
        raise RuntimeError("password field is not visible/interactable")
    return candidate


# ---------------------------------------------------------------------------
# Step 4 — submit button (strict priority + SSO exclusion)
# ---------------------------------------------------------------------------


def _find_submit_button(page: Page) -> tuple[Locator, str]:
    """Return the real login submit button (never an SSO/social button)."""
    # Priority 1: a submit button that lives inside a <form>.
    candidate = _first_safe_button(page.locator("form button[type='submit']"))
    if candidate is not None:
        return candidate, "form button[type='submit']"

    # Priority 2: any submit button on the page.
    candidate = _first_safe_button(page.locator("button[type='submit']"))
    if candidate is not None:
        return candidate, "button[type='submit']"

    # Priority 3: text-based fallback. Each variant is tried separately.
    text_selectors = (
        'button:has-text("Sign in")',
        'button:has-text("Login")',
        'button:has-text("Log in")',
        'button:has-text("Sign In")',
    )
    for selector in text_selectors:
        candidate = _first_safe_button(page.locator(selector))
        if candidate is not None:
            return candidate, selector

    raise RuntimeError("could not locate a non-SSO login submit button")


def _first_safe_button(locator: Locator) -> Locator | None:
    """Return the first visible button that is NOT an SSO/social button."""
    try:
        count = locator.count()
    except Exception:
        return None

    for index in range(count):
        button = locator.nth(index)
        if not _is_visible(button):
            continue
        if _is_sso_button(button):
            logger.info("[login] Skipping SSO/social button candidate")
            continue
        return button
    return None


def _is_sso_button(button: Locator) -> bool:
    """True if the button (or any descendant) references an SSO provider.

    We pull the entire subtree's text, attribute values, and child img alt
    text into one lowercased string so buttons whose label lives inside a
    child <img alt="Google"> or icon <svg> are still detected.
    """
    try:
        haystack = button.evaluate(
            """
            (el) => {
                const parts = [];
                /* Attributes on the button itself */
                ['aria-label', 'title', 'name', 'id', 'class', 'data-provider', 'data-testid']
                    .forEach(a => { const v = el.getAttribute(a); if (v) parts.push(v); });
                /* Visible text */
                parts.push(el.innerText || '');
                /* Attributes on every descendant — catches <img alt="Google"> and <svg aria-label> */
                el.querySelectorAll('*').forEach(child => {
                    ['alt', 'aria-label', 'title', 'data-provider', 'class']
                        .forEach(a => { const v = child.getAttribute(a); if (v) parts.push(v); });
                });
                return parts.join(' ').toLowerCase();
            }
            """
        )
    except Exception:
        haystack = ""

    return any(keyword in (haystack or "") for keyword in SSO_KEYWORDS)


# ---------------------------------------------------------------------------
# Step 6 — wait for login completion
# ---------------------------------------------------------------------------


def _wait_for_login_completion(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        logger.warning("[login] networkidle timeout after submit — continuing")
    page.wait_for_timeout(3000)


# ---------------------------------------------------------------------------
# Step 7 — validate
# ---------------------------------------------------------------------------


def _validate_login_success(page: Page) -> None:
    if "login" in page.url.lower():
        raise RuntimeError(f"still on login page after submit (url={page.url})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_interactable(locator: Locator) -> Locator | None:
    """Return the first locator match that is both visible and enabled."""
    try:
        count = locator.count()
    except Exception:
        return None

    for index in range(count):
        candidate = locator.nth(index)
        if not _is_visible(candidate):
            continue
        if not _is_enabled(candidate):
            continue
        return candidate
    return None


def _is_visible(locator: Locator) -> bool:
    try:
        return locator.is_visible()
    except Exception:
        return False


def _is_enabled(locator: Locator) -> bool:
    try:
        return locator.is_enabled()
    except Exception:
        return False


def _screenshot_failure(page: Page) -> None:
    try:
        SCREENSHOT_PATH.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_PATH / "login_failure.png"
        page.screenshot(path=str(path))
        logger.warning(f"[login] Failure screenshot saved to {path}")
    except Exception:
        logger.warning("[login] Could not save failure screenshot")
