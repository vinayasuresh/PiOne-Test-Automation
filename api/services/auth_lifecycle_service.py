"""auth_lifecycle_service.py: Server-side session lifecycle, expiry detection, and refresh workflows.

Guarantees:
- Server-side storage state validation (detects expired cookies, login redirects, and visible password fields).
- Safe session refresh without restarting scans or losing discovered modules and test plans.
- Re-binds refreshed sessions to the exact same run_id.
- Clear error contract: "Authentication expired. Refresh the session to continue."
"""

from __future__ import annotations

import json
import time
import base64
from pathlib import Path
from uuid import uuid4
from typing import Any
from urllib.parse import urlparse

from automation_framework.config.settings import REPORT_PATH, ROUTE_LOAD_TIMEOUT
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.url_filter import install_document_scope_guard
from automation_framework.utils.browser_manager import save_storage_state, start_persistent_browser
from automation_framework.utils.logger import logger


from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError


def auth_session_path(auth_session_id: str | None) -> Path:
    """Return the server-side storage state path for an auth_session_id."""
    if not auth_session_id or not auth_session_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid authentication session ID")
    state_dir = REPORT_PATH / "auth_sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{auth_session_id}.json"


def check_auth_session_validity(
    url: str,
    auth_session_id: str | None,
    run_id: str | None = None,
) -> tuple[bool, str]:
    """Check if the session state is active and not redirected to login.
    
    Returns (is_valid, detail_message).
    """
    if not auth_session_id:
        return False, "No authentication session provided."

    if auth_session_id.startswith("mock_") or auth_session_id.startswith("mock_auth_"):
        return True, "Mock demo session active"

    session_file = auth_session_path(auth_session_id)
    if not session_file.is_file():
        return False, "Authentication session has expired; please refresh the session."

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        if not cookies:
            return False, "Authentication expired: No active session cookies found."
        now = time.time()
        valid_cookies = [c for c in cookies if c.get("expires", -1) == -1 or c.get("expires", 0) > now]
        if not valid_cookies:
            return False, "Authentication expired: All session cookies have expired."
        # Modern SPAs commonly keep the access token in origin localStorage
        # rather than a target-domain cookie.  A refreshed session can
        # therefore have a valid JWT and only a refresh-token cookie.  Trust
        # the token's unexpired timestamp for the preflight check; the actual
        # crawl still verifies the authenticated page before scanning.
        token_reason = _unexpired_access_token_reason(data, now)
        if token_reason:
            return True, token_reason
    except Exception as exc:
        return False, f"Invalid storage state file: {exc}"

    # Determine probe URL: if root/empty path, probe protected route
    probe_url = url
    parsed = urlparse(url)
    if not parsed.path or parsed.path == "/":
        if "saucedemo.com" in parsed.netloc:
            probe_url = "https://www.saucedemo.com/inventory.html"
        elif run_id:
            from urllib.parse import urljoin
            from api.services.run_context_service import get_run_context
            ctx = get_run_context(run_id)
            if ctx and ctx.discovered_modules:
                for mod in ctx.discovered_modules:
                    r = mod.get("route")
                    if r and not r.endswith(("/", "/login")):
                        probe_url = urljoin(url.rstrip("/") + "/", r.lstrip("/"))
                        break

    # Probe page live in browser
    playwright = browser = context = None
    try:
        playwright, browser, context, page = start_persistent_browser(session_file)
        page.set_default_navigation_timeout(6000)
        page.goto(probe_url, wait_until="domcontentloaded", timeout=6000)
        
        # Check login detection
        is_login = _is_login_page_active(page)
        if is_login:
            return False, "Authentication expired. Refresh the session to continue."
        return True, "Session is active and valid."
    except Exception as exc:
        logger.debug("Session validity probe non-fatal warning: %s", exc)
        return False, f"Could not verify session: {exc}"
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass


def _is_login_page_active(page: Any) -> bool:
    """Detect whether page is on a login form or session-expired landing screen."""
    try:
        current_url = str(page.url).lower()
        if any(keyword in current_url for keyword in ["login", "signin", "sign-in", "authenticate"]):
            return True

        # Check for visible password fields
        pw_locator = page.locator('input[type="password"]')
        if pw_locator.count() > 0 and pw_locator.first.is_visible():
            return True

        # Check for SauceDemo error message or login button
        login_btn = page.locator('#login-button, [data-test="login-button"]')
        if login_btn.count() > 0 and login_btn.first.is_visible():
            return True

        error_msg = page.locator('[data-test="error"]')
        if error_msg.count() > 0 and error_msg.first.is_visible():
            text = (error_msg.first.inner_text() or "").lower()
            if "logged in" in text or "login" in text:
                return True

        return False
    except Exception:
        return False


def _unexpired_access_token_reason(storage_state: dict[str, Any], now: float | None = None) -> str | None:
    """Return a diagnostic when storage state contains a non-expired JWT."""
    now = now or time.time()
    for origin in storage_state.get("origins", []) or []:
        for item in origin.get("localStorage", []) or []:
            name = str(item.get("name") or "").lower()
            token = str(item.get("value") or "")
            if name not in {"access_token", "accesstoken", "id_token", "idtoken", "token"}:
                continue
            parts = token.split(".")
            if len(parts) != 3:
                continue
            try:
                encoded = parts[1] + ("=" * (-len(parts[1]) % 4))
                payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
                exp = float(payload.get("exp", 0) or 0)
                if exp > now:
                    return "Unexpired access token found in saved browser session."
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
                continue
    return None


def refresh_auth_session(
    url: str,
    username: str,
    password: str,
    auth_session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Perform login and refresh the server-side storage state in place."""
    if not username or not password:
        raise ValueError("Username and password are required to refresh the session.")

    resolved_session_id = auth_session_id
    if not resolved_session_id and run_id:
        from api.services.run_context_service import get_run_context
        ctx = get_run_context(run_id)
        if ctx and ctx.auth_session_id:
            resolved_session_id = ctx.auth_session_id

    if not resolved_session_id:
        resolved_session_id = uuid4().hex

    state_path = auth_session_path(resolved_session_id)
    playwright = browser = context = None
    try:
        playwright, browser, context, page = start_persistent_browser()
        install_document_scope_guard(page, url)
        login(page, username, password, url)

        # Check if login succeeded
        if _is_login_page_active(page):
            raise ValueError("Re-authentication failed: login form still present with provided credentials.")

        save_storage_state(context, state_path)
        logger.info("[Auth] Session '%s' refreshed successfully for %s", resolved_session_id, url)

        if run_id:
            from api.services.run_context_service import update_run_context
            update_run_context(
                run_id,
                auth_session_id=resolved_session_id,
                auth_status="authenticated",
                username=username,
            )

        return {
            "run_id": run_id or "",
            "auth_session_id": resolved_session_id,
            "state_path": str(state_path),
        }
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
