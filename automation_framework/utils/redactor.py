"""redactor.py: Recursive secret, credential, token, and path redaction.

Guarantees:
- Recursively cleans dictionaries, lists, tuples, sets, and strings.
- Replaces values for sensitive keys (passwords, tokens, cookies, secrets, auth headers).
- Redacts embedded secrets in string logs, error messages, and URLs (Bearer, Basic, API keys, query tokens).
- Sanitizes server file paths to avoid exposing internal filesystem structures.
"""

from __future__ import annotations

import re
from typing import Any

# Sensitive dictionary keys (case-insensitive)
SENSITIVE_KEY_NAMES = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "authorization",
    "auth_header",
    "cookie",
    "cookies",
    "storage_state",
    "client_secret",
    "private_key",
    "credential",
    "credentials",
    "bearer",
}

# Regex patterns for embedded tokens / credentials in text
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-zA-Z0-9_\-\.~+/]+=*", re.IGNORECASE)
_BASIC_PATTERN = re.compile(r"(?i)\bbasic\s+[a-zA-Z0-9+/=]{10,}", re.IGNORECASE)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b")
_GEMINI_KEY_PATTERN = re.compile(r"\bAIzaSy[a-zA-Z0-9_\-]{33}\b")
_KEY_VAL_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api_key|apikey|secret|client_secret)\s*[:=]\s*([^\s,;&\"']+)",
    re.IGNORECASE,
)
_URL_SECRET_PARAM = re.compile(
    r"(?i)([?&](?:password|passwd|pwd|token|api_key|apikey|secret|key)=)[^&\s]+",
    re.IGNORECASE,
)
_AUTH_HEADER_VALUE = re.compile(
    r"(?i)(authorization:\s*)(?:bearer\s+|basic\s+)?[^\r\n,;]+",
    re.IGNORECASE,
)

# Sensitive filesystem path pattern (e.g. C:\Users\... or /home/...)
_WINDOWS_USER_PATH = re.compile(r"[A-Za-z]:\\Users\\[^\\]+\\", re.IGNORECASE)
_UNIX_USER_PATH = re.compile(r"/(?:home|Users)/[^/]+/", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Redact embedded credentials, tokens, and server paths from a string."""
    if not text or not isinstance(text, str):
        return text

    s = text
    s = _BEARER_PATTERN.sub("Bearer [REDACTED]", s)
    s = _BASIC_PATTERN.sub("Basic [REDACTED]", s)
    s = _OPENAI_KEY_PATTERN.sub("[REDACTED_API_KEY]", s)
    s = _GEMINI_KEY_PATTERN.sub("[REDACTED_API_KEY]", s)
    s = _KEY_VAL_SECRET.sub(r"\1=[REDACTED]", s)
    s = _URL_SECRET_PARAM.sub(r"\1[REDACTED]", s)
    s = _AUTH_HEADER_VALUE.sub(r"\1[REDACTED]", s)
    s = _WINDOWS_USER_PATH.sub(r"[SERVER_PATH]\\", s)
    s = _UNIX_USER_PATH.sub(r"[SERVER_PATH]/", s)
    return s


def redact_sensitive_data(obj: Any) -> Any:
    """Recursively redact secrets and credentials from nested structures."""
    if obj is None:
        return None

    if isinstance(obj, str):
        return redact_text(obj)

    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            k_str = str(k).lower().strip()
            # If the key itself indicates sensitive content, redact its value directly
            if any(sens in k_str for sens in SENSITIVE_KEY_NAMES):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_sensitive_data(v)
        return cleaned

    if isinstance(obj, list):
        return [redact_sensitive_data(item) for item in obj]

    if isinstance(obj, tuple):
        return tuple(redact_sensitive_data(item) for item in obj)

    if isinstance(obj, set):
        return {redact_sensitive_data(item) for item in obj}

    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        # Pydantic model
        return redact_sensitive_data(obj.model_dump(mode="json"))

    if hasattr(obj, "__dict__"):
        return redact_sensitive_data(dict(obj.__dict__))

    return obj
