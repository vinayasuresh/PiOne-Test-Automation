"""auth_exceptions.py: Common authentication lifecycle exceptions."""

from __future__ import annotations


class AuthenticationExpiredError(Exception):
    """Raised when an authenticated session has expired, been logged out, or requires re-authentication."""
