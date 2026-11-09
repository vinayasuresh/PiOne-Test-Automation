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
from urllib.parse import urljoin, urlparse

from automation_framework.config.settings import REPORT_PATH, ROUTE_LOAD_TIMEOUT
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.url_filter import install_document_scope_guard, is_valid_url
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
    *,
    state_path_override: Path | None = None,
) -> tuple[bool, str]:
    """Check if the session state is active and not redirected to login.
    
    Returns (is_valid, detail_message).
    """
    if not auth_session_id:
        return False, "No authentication session provided."

    if auth_session_id.startswith("mock_") or auth_session_id.startswith("mock_auth_"):
        return True, "Mock demo session active"

    session_file = state_path_override or auth_session_path(auth_session_id)
    if not session_file.is_file():
        return False, "Authentication session has expired; please refresh the session."

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        now = time.time()
        # SPAs such as CAT commonly keep the access token in localStorage and
        # have no durable auth cookie at all.  Check that token before
        # rejecting a cookie-less storage state; otherwise every deep crawl
        # is incorrectly reported as expired immediately after discovery.
        token_reason = _unexpired_access_token_reason(data, url, now)
        if not cookies and not token_reason:
            return False, "Authentication expired: No active session cookies or access token found."
        valid_cookies = [c for c in cookies if c.get("expires", -1) == -1 or c.get("expires", 0) > now]
        if cookies and not valid_cookies and not token_reason:
            return False, "Authentication expired: All session cookies and access tokens have expired."
    except Exception as exc:
        return False, f"Invalid storage state file: {exc}"

    # Determine probe URL: if root/empty path, probe protected route
    probe_url = url
    parsed = urlparse(url)
    if not parsed.path or parsed.path == "/":
        if "saucedemo.com" in parsed.netloc:
            probe_url = "https://www.saucedemo.com/inventory.html"
        elif run_id:
            from api.services.run_context_service import get_run_context
            ctx = get_run_context(run_id)
            if ctx and ctx.auth_probe_path:
                candidate = urljoin(url.rstrip("/") + "/", ctx.auth_probe_path.lstrip("/"))
                # Only use a path captured from the same origin during login.
                # Revalidate here because this value is persisted run state.
                if ctx.auth_probe_path.startswith("/") and not ctx.auth_probe_path.startswith("//") and is_valid_url(candidate, url):
                    probe_url = candidate
            if probe_url == url and ctx and ctx.discovered_modules:
                for mod in ctx.discovered_modules:
                    r = mod.get("route")
                    if r and not r.endswith(("/", "/login")):
                        candidate = urljoin(url.rstrip("/") + "/", r)
                        if is_valid_url(candidate, url):
                            probe_url = candidate
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
        return True, "Saved session reopened on a protected route."
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


def _unexpired_access_token_reason(
    storage_state: dict[str, Any], target_url: str, now: float | None = None
) -> str | None:
    """Return a diagnostic for a non-expired JWT stored for this target origin.

    Token expiry is only a prerequisite for attempting the browser probe; it is
    never proof that the target still accepts the token.
    """
    now = now or time.time()
    target = urlparse(target_url)
    target_origin = f"{target.scheme.lower()}://{target.netloc.lower()}"
    for origin in storage_state.get("origins", []) or []:
        saved_origin = urlparse(str(origin.get("origin") or ""))
        if f"{saved_origin.scheme.lower()}://{saved_origin.netloc.lower()}" != target_origin:
            continue
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
    # Keep the currently usable state until the replacement has been reopened
    # independently and proven against the target's protected page.
    candidate_path = state_path.with_name(f".{state_path.stem}.{uuid4().hex}.candidate.json")
    playwright = browser = context = None
    try:
        playwright, browser, context, page = start_persistent_browser()
        install_document_scope_guard(page, url)
        login(page, username, password, url)

        # Check if login succeeded
        if _is_login_page_active(page):
            raise ValueError("Re-authentication failed: login form still present with provided credentials.")

        save_storage_state(context, candidate_path)
        # The sync Playwright API cannot start a second driver on the same
        # thread while the first is running. Close login before independently
        # reopening the candidate session for verification.
        context.close()
        context = None
        browser.close()
        browser = None
        playwright.stop()
        playwright = None
        verified, detail = check_auth_session_validity(
            url,
            resolved_session_id,
            run_id,
            state_path_override=candidate_path,
        )
        if not verified:
            raise ValueError(
                "Re-authentication could not be verified on a protected page. "
                "The target may have rejected the credentials or not persisted its session."
            )

        candidate_path.replace(state_path)
        logger.info("[Auth] Session '%s' refreshed and verified for %s", resolved_session_id, url)

        if run_id:
            from api.services.run_context_service import update_run_context
            update_run_context(
                run_id,
                auth_session_id=resolved_session_id,
                auth_status="authenticated",
                username=username,
                error_message=None,
            )

        return {
            "run_id": run_id or "",
            "auth_session_id": resolved_session_id,
            "state_path": str(state_path),
            "verified": True,
            "verification_detail": detail,
        }
    except Exception:
        try:
            candidate_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
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
