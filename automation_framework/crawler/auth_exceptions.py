"""auth_exceptions.py: Common authentication lifecycle exceptions."""

from __future__ import annotations


class AuthenticationExpiredError(RuntimeError):
    """Raised when an authenticated session has expired, been logged out, or requires re-authentication."""


class LoginFailedError(RuntimeError):
    """Raised when authentication credentials are invalid or login form is rejected by the application."""


class DiscoveryUnavailableError(RuntimeError):
    """Raised when the target application is unreachable, network drops, or Playwright navigation fails."""
