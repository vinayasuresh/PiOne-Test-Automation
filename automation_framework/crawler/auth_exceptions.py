"""auth_exceptions.py: Common authentication lifecycle exceptions."""

from __future__ import annotations


class AuthenticationExpiredError(Exception):
    """Raised when an authenticated session has expired, been logged out, or requires re-authentication."""


class LoginFailedError(Exception):
    """Raised when authentication credentials are invalid or login form is rejected by the application."""


class DiscoveryUnavailableError(Exception):
    """Raised when the target application is unreachable, network drops, or Playwright navigation fails."""
