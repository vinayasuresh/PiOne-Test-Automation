"""SessionManager: Handles credentials, storage state, and authenticated contexts.

Guarantees:
- Safe storage state persistence (cookies, localStorage, indexedDB).
- Masking and redaction of credentials in logs and outputs.
- Verification of session validity (detects redirects to login or expired tokens).
- Creation and restoration of authenticated Playwright contexts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from automation_framework.crawler.login_handler import login
from automation_framework.utils.browser_manager import save_storage_state
from automation_framework.utils.logger import logger


class SessionManager:
    """Manages credentials, session state, and authenticated Playwright contexts."""

    LOGIN_URL_INDICATORS = {
        "login", "sign-in", "signin", "auth", "authenticate", "sso",
    }

    def __init__(
        self,
        base_url: str,
        credentials: tuple[str, str] | None = None,
        storage_state_path: str | Path | None = None,
    ) -> None:
        self.base_url = base_url.strip()
        self.credentials = credentials
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials and len(self.credentials) == 2 and self.credentials[0])

    def create_context(
        self,
        browser: Browser,
        **context_kwargs: Any,
    ) -> tuple[BrowserContext, Page]:
        """Create a BrowserContext, restoring storage state if available."""
        options = dict(context_kwargs)
        if self.storage_state_path and self.storage_state_path.exists():
            options["storage_state"] = str(self.storage_state_path)
            logger.info("Restoring browser session state from %s", self.storage_state_path)

        context = browser.new_context(**options)
        page = context.new_page()
        return context, page

    def save_session(self, context: BrowserContext) -> None:
        """Persist current session state (cookies, IndexedDB) safely."""
        if not self.storage_state_path:
            return
        try:
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            save_storage_state(context, self.storage_state_path)
            logger.info("Session state saved to %s", self.storage_state_path)
        except Exception:
            logger.warning("Failed to save storage state to %s", self.storage_state_path)

    def is_login_page(self, page: Page) -> bool:
        """Detect whether the current page is a login/authentication screen."""
        try:
            parsed = urlparse(page.url)
            path_parts = {part.casefold() for part in unquote(parsed.path).split("/") if part}
            if path_parts & self.LOGIN_URL_INDICATORS:
                return True

            # If a visible password field exists, it's almost certainly a login screen
            return page.locator('input[type="password"]').first.is_visible()
        except Exception:
            return False

    def ensure_authenticated(self, page: Page, context: BrowserContext | None = None) -> bool:
        """Verify authentication and recover via login if session was lost."""
        if not self.is_login_page(page):
            return True

        if not self.has_credentials:
            logger.error("Authentication lost on %s but no credentials provided", page.url)
            from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError
            raise AuthenticationExpiredError("Authentication expired. Refresh the session to continue.")

        username, password = self.credentials  # type: ignore[misc]
        logger.warning("Session lost on %s. Re-authenticating user %s...", page.url, username[:3] + "***")
        login(page, username, password, self.base_url)

        if context and self.storage_state_path:
            self.save_session(context)

        return not self.is_login_page(page)
