"""Deterministic login handler.

Flow (in order):
    1. Navigate to base_url and wait for login form (supporting SPA hydration and frames).
    2. Pick username field by strict priority list (name, id, placeholder, label, autocomplete).
    3. Pick password field (type=password, name, id, placeholder, autocomplete).
    4. Pick submit button by strict priority list, excluding SSO/social buttons.
    5. Fill credentials safely, small delay, submit.
    6. Wait for load state + settle.
    7. Validate login success; capture redacted diagnostic snapshot on failure.

Public API:
    login(page, username, password, base_url)
"""

from __future__ import annotations

import json
import time
from typing import Any

from playwright.sync_api import (
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from automation_framework.config.settings import LOGIN_TIMEOUT, ROUTE_LOAD_TIMEOUT, SCREENSHOT_PATH
from automation_framework.utils.logger import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Username field selectors, tried in this exact order.
USERNAME_PRIORITY: tuple[str, ...] = (
    'input[type="email"]',
    'input[name="username" i]',
    'input[name="user" i]',
    'input[name="email" i]',
    'input[name*="user" i]',
    'input[name*="email" i]',
    'input[name*="login" i]',
    'input[name*="account" i]',
    'input[name*="identity" i]',
    'input[id="username" i]',
    'input[id="user" i]',
    'input[id="email" i]',
    'input[id*="username" i]',
    'input[id*="user" i]',
    'input[id*="email" i]',
    'input[id*="login" i]',
    'input[id*="account" i]',
    'input[placeholder="username" i]',
    'input[placeholder*="user" i]',
    'input[placeholder*="email" i]',
    'input[placeholder*="login" i]',
    'input[placeholder*="account" i]',
    'input[aria-label="username" i]',
    'input[aria-label*="user" i]',
    'input[aria-label*="email" i]',
    'input[aria-label*="login" i]',
    'input[aria-label*="account" i]',
    'input[autocomplete="username" i]',
    'input[autocomplete*="username" i]',
    'input[autocomplete*="email" i]',
    'input[type="text"]:visible',
    'input:not([type]):visible',
    'input:not([type="hidden"]):not([type="password"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):visible',
)

# Password field selectors, tried in this exact order.
PASSWORD_PRIORITY: tuple[str, ...] = (
    'input[type="password"]',
    'input[name="password" i]',
    'input[name="pass" i]',
    'input[name*="pass" i]',
    'input[name*="pwd" i]',
    'input[id="password" i]',
    'input[id="pass" i]',
    'input[id*="pass" i]',
    'input[id*="pwd" i]',
    'input[placeholder="password" i]',
    'input[placeholder*="pass" i]',
    'input[placeholder*="pwd" i]',
    'input[aria-label="password" i]',
    'input[aria-label*="pass" i]',
    'input[autocomplete="current-password" i]',
    'input[autocomplete="password" i]',
    'input[autocomplete*="password" i]',
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

    Raises RuntimeError on any failure. Captures a diagnostic snapshot and
    screenshot to screenshots/login_failure.png before raising.
    """
    try:
        logger.info(f"[login] Navigating to {base_url}")
        page.goto(base_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)

        context, username_field, username_selector, password_field, password_selector = (
            _wait_for_login_form(page)
        )

        logger.info(f"[login] Form detected in context ({type(context).__name__})")
        logger.info(f"[login] Username field selector: {username_selector}")
        logger.info(f"[login] Password field selector: {password_selector}")

        submit_button, submit_selector = _find_submit_button(context)
        logger.info(f"[login] Submit button selector: {submit_selector}")

        logger.info("[login] Filling credentials")
        username_field.fill(username, timeout=LOGIN_TIMEOUT)
        password_field.fill(password, timeout=LOGIN_TIMEOUT)
        page.wait_for_timeout(500)

        logger.info("[login] Submitting form")
        if submit_button is None:
            password_field.press("Enter", timeout=LOGIN_TIMEOUT)
        else:
            submit_button.click(timeout=LOGIN_TIMEOUT)

        _wait_for_login_completion(page)
        _validate_login_success(page)

        logger.info(f"[login] Login successful — current URL: {page.url}")

    except Exception as exc:
        diagnostics = _capture_login_diagnostics(page)
        _screenshot_failure(page)
        logger.error(
            "[login] Login failed: %s | Diagnostics: URL=%s, Frames=%d, Inputs=%d, Buttons=%d",
            exc,
            diagnostics.get("final_url"),
            diagnostics.get("frame_count", 0),
            len(diagnostics.get("detected_inputs", [])),
            len(diagnostics.get("detected_buttons", [])),
        )
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Login failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Step 1 — wait for login form (with SPA hydration & multi-frame support)
# ---------------------------------------------------------------------------


def _wait_for_login_form(
    page: Page,
    timeout_ms: int = LOGIN_TIMEOUT,
) -> tuple[Page | Frame, Locator, str, Locator, str]:
    """Wait for username and password fields to become available and interactable.

    Supports client-rendered SPAs (OrangeHRM, Angular, React) where inputs may
    hydrate after initial shell rendering, and inspects same-origin frames if
    the login form is rendered in an iframe.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    poll_interval = 0.25

    while time.monotonic() < deadline:
        # Check main page and all attached frames
        contexts: list[Page | Frame] = [page]
        try:
            contexts.extend(page.frames)
        except Exception:
            pass

        for ctx in contexts:
            try:
                # Fast existence check
                if ctx.locator("input").count() == 0:
                    continue

                username_candidate, u_sel = _find_username_field(ctx)
                password_candidate, p_sel = _find_password_field(ctx)

                if username_candidate is not None and password_candidate is not None:
                    return ctx, username_candidate, u_sel, password_candidate, p_sel
            except Exception:
                continue

        time.sleep(poll_interval)

    # If we timed out, check what exists to provide an accurate exception
    has_any_input = False
    contexts = [page] + list(page.frames)
    for ctx in contexts:
        try:
            if ctx.locator("input").count() > 0:
                has_any_input = True
                break
        except Exception:
            pass

    if not has_any_input:
        raise RuntimeError("no input fields found on the login page")
    raise RuntimeError("password field never appeared on the login page")


# ---------------------------------------------------------------------------
# Step 2 — username field (priority + label heuristic)
# ---------------------------------------------------------------------------


def _find_username_field(context: Page | Frame) -> tuple[Locator | None, str]:
    # 1. Primary selector priority list
    for selector in USERNAME_PRIORITY:
        candidate = _first_interactable(context.locator(selector))
        if candidate is not None:
            return candidate, selector

    # 2. Label text heuristic: find label element referencing an input
    for label_word in ("username", "email", "user id", "account", "user"):
        try:
            labels = context.locator(f"label:has-text('{label_word}')")
            for i in range(labels.count()):
                lbl = labels.nth(i)
                for_attr = lbl.get_attribute("for")
                if for_attr:
                    target = context.locator(f"#{for_attr}")
                    if _is_visible(target) and _is_enabled(target):
                        return target, f"label[for='{for_attr}']"
                nested_input = lbl.locator("input")
                if nested_input.count() > 0:
                    first = _first_interactable(nested_input)
                    if first is not None:
                        return first, f"label:has-text('{label_word}') input"
        except Exception:
            pass

    return None, ""


# ---------------------------------------------------------------------------
# Step 3 — password field
# ---------------------------------------------------------------------------


def _find_password_field(context: Page | Frame) -> tuple[Locator | None, str]:
    for selector in PASSWORD_PRIORITY:
        candidate = _first_interactable(context.locator(selector))
        if candidate is not None:
            return candidate, selector

    # Label text heuristic for password
    try:
        labels = context.locator("label:has-text('password')")
        for i in range(labels.count()):
            lbl = labels.nth(i)
            for_attr = lbl.get_attribute("for")
            if for_attr:
                target = context.locator(f"#{for_attr}")
                if _is_visible(target) and _is_enabled(target):
                    return target, f"label[for='{for_attr}']"
            nested_input = lbl.locator("input")
            if nested_input.count() > 0:
                first = _first_interactable(nested_input)
                if first is not None:
                    return first, "label:has-text('password') input"
    except Exception:
        pass

    return None, ""


# ---------------------------------------------------------------------------
# Step 4 — submit button (strict priority + SSO exclusion)
# ---------------------------------------------------------------------------


def _find_submit_button(context: Page | Frame) -> tuple[Locator | None, str]:
    """Return a safe submit control, or a form-Enter fallback."""
    selectors = (
        "form input[type='submit']",
        "form button[type='submit']",
        "form [role='button']",
        "form button",
        "form input[type='button']",
        "input[type='submit']",
        "button[type='submit']",
        "[type='submit']",
        "button.oxd-button",
        "button",
        "[role='button']",
    )
    for selector in selectors:
        candidate = _first_safe_button(context.locator(selector))
        if candidate is not None:
            return candidate, selector

    # Text / accessibility name lookup
    for name in ("sign in", "login", "log in", "submit", "continue"):
        for locator in (
            context.get_by_role("button", name=name, exact=True),
            context.get_by_role("button", name=name, exact=False),
            context.get_by_text(name, exact=True),
        ):
            candidate = _first_safe_button(locator)
            if candidate is not None:
                return candidate, f"accessible/text:{name}"

    # Last resort: enter in password field
    forms = context.locator("form")
    try:
        for index in range(forms.count()):
            form = forms.nth(index)
            if _is_visible(form):
                return None, "form:Enter"
    except Exception:
        pass

    return None, "enter_key_fallback"


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
    """True if the button (or any descendant) references an SSO provider."""
    try:
        haystack = button.evaluate(
            """
            (el) => {
                const parts = [];
                ['aria-label', 'title', 'name', 'id', 'class', 'data-provider', 'data-testid']
                    .forEach(a => { const v = el.getAttribute(a); if (v) parts.push(v); });
                parts.push(el.innerText || '');
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
        page.wait_for_load_state("networkidle", timeout=min(LOGIN_TIMEOUT, 6000))
    except PlaywrightTimeoutError:
        logger.warning("[login] networkidle timeout after submit — continuing")
    page.wait_for_timeout(min(1000, LOGIN_TIMEOUT))


# ---------------------------------------------------------------------------
# Step 7 — validate & diagnostics
# ---------------------------------------------------------------------------


def _validate_login_success(page: Page) -> None:
    current = page.url.lower().strip()
    # A browser-level error page can change the URL away from /login without
    # establishing an authenticated application session. Treat it as a hard
    # failure instead of allowing discovery to scan chrome-error:// routes.
    if current.startswith(("chrome-error:", "chrome-error://", "about:blank", "data:", "file:", "javascript:")):
        raise RuntimeError(f"login navigation failed (browser error page: {page.url})")
    if not current.startswith(("http://", "https://")):
        raise RuntimeError(f"login navigation produced an invalid URL scheme: {page.url}")

    # Check if remaining on a login page path
    login_patterns = ("/auth/login", "/signin", "/sign-in", "/authenticate")
    is_login_path = any(p in current for p in login_patterns) or current.endswith("/login") or "login." in current or "login=" in current

    if is_login_path:
        # Give SPA router up to 2 extra seconds to complete route change
        for _ in range(8):
            time.sleep(0.25)
            current = page.url.lower().strip()
            if not (any(p in current for p in login_patterns) or current.endswith("/login") or "login." in current or "login=" in current):
                is_login_path = False
                break

    # If still on a login path, or if a visible password input / login button / login error remains
    has_visible_password = False
    try:
        pw = page.locator('input[type="password"]')
        has_visible_password = pw.count() > 0 and pw.first.is_visible()
    except Exception:
        pass

    if is_login_path or has_visible_password:
        raise RuntimeError(f"still on login page after submit (url={page.url})")


def _capture_login_diagnostics(page: Page) -> dict[str, Any]:
    """Capture diagnostic metadata without ever extracting or logging credentials."""
    diagnostics: dict[str, Any] = {
        "final_url": page.url,
        "frame_count": len(page.frames),
        "detected_inputs": [],
        "detected_buttons": [],
    }

    contexts: list[Page | Frame] = [page]
    try:
        contexts.extend(page.frames)
    except Exception:
        pass

    for ctx in contexts:
        ctx_name = getattr(ctx, "name", "main_page") or "frame"
        try:
            inputs_meta = ctx.locator("input, textarea").evaluate_all("""els => els.map(el => {
                const box = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && (box.width > 0 || box.height > 0);
                return {
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    classes: el.className || '',
                    visible: visible
                };
            })""")
            for im in inputs_meta:
                im["frame"] = ctx_name
                diagnostics["detected_inputs"].append(im)
        except Exception:
            pass

        try:
            buttons_meta = ctx.locator("button, [role='button'], input[type='submit'], input[type='button']").evaluate_all("""els => els.map(el => {
                const box = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && (box.width > 0 || box.height > 0);
                return {
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 50),
                    role: el.getAttribute('role') || '',
                    classes: el.className || '',
                    visible: visible
                };
            })""")
            for bm in buttons_meta:
                bm["frame"] = ctx_name
                diagnostics["detected_buttons"].append(bm)
        except Exception:
            pass

    return diagnostics


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

