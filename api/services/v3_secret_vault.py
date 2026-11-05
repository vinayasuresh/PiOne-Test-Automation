"""Ephemeral server-side secret vault and safe connection prober for OmniTest V3.

Zero-secret-leakage guarantee:
- Credentials (Bearer tokens, API keys, Basic credentials, OAuth client secrets)
  are held strictly in server-side memory under an ephemeral reference (e.g. sec_fix_<uuid>).
- Raw secrets are NEVER written to the database, logs, browser storage,
  URLs, Excel reports, or client responses.
- Test Connection probes issue bounded, safe requests without persisting or echoing credentials.
"""

from __future__ import annotations

import base64
import os
import threading
import uuid
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


_VAULT_LOCK = threading.RLock()
_FIXTURE_STORE: dict[str, dict[str, Any]] = {}


def register_secret_fixture(
    auth_type: Literal["bearer", "api_key", "basic", "oauth2"],
    *,
    token: str | None = None,
    api_key_name: str | None = None,
    api_key_location: Literal["header", "query"] = "header",
    api_key_value: str | None = None,
    username: str | None = None,
    password: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    token_url: str | None = None,
    scope: str = "",
) -> dict[str, str]:
    """Register credentials in the server-side vault and return a sanitized fixture reference."""
    fixture_id = f"sec_fix_{uuid.uuid4().hex[:16]}"
    record: dict[str, Any] = {
        "fixture_ref": fixture_id,
        "auth_type": auth_type,
    }

    if auth_type == "bearer":
        if not token or not token.strip():
            raise ValueError("Bearer token cannot be empty")
        record["token"] = token.strip()
        masked = f"bearer_{'*' * 8}{token.strip()[-4:] if len(token.strip()) > 4 else '****'}"

    elif auth_type == "api_key":
        if not api_key_name or not api_key_name.strip():
            raise ValueError("API Key name cannot be empty")
        if not api_key_value or not api_key_value.strip():
            raise ValueError("API Key value cannot be empty")
        record["api_key_name"] = api_key_name.strip()
        record["api_key_location"] = api_key_location
        record["api_key_value"] = api_key_value.strip()
        val = api_key_value.strip()
        masked = f"key_{'*' * 8}{val[-4:] if len(val) > 4 else '****'}"

    elif auth_type == "basic":
        if not username or not password:
            raise ValueError("Basic auth requires both username and password")
        record["username"] = username.strip()
        record["password"] = password.strip()
        encoded = base64.b64encode(f"{username.strip()}:{password.strip()}".encode()).decode()
        record["basic_header"] = f"Basic {encoded}"
        masked = f"basic_{username.strip()}:****"

    elif auth_type == "oauth2":
        if not client_id or not client_secret or not token_url:
            raise ValueError("OAuth2 client credentials require client_id, client_secret, and token_url")
        record["client_id"] = client_id.strip()
        record["client_secret"] = client_secret.strip()
        record["token_url"] = token_url.strip()
        record["scope"] = scope.strip()
        record["cached_token"] = None
        record["token_expiry"] = 0.0
        masked = f"oauth_{client_id.strip()}:****"

    else:
        raise ValueError(f"Unsupported auth type: {auth_type}")

    with _VAULT_LOCK:
        _FIXTURE_STORE[fixture_id] = record

    return {
        "fixture_ref": fixture_id,
        "auth_type": auth_type,
        "masked_preview": masked,
    }


def get_secret_fixture(fixture_ref: str) -> dict[str, Any] | None:
    """Retrieve fixture by reference (internal use only, never expose over HTTP)."""
    with _VAULT_LOCK:
        fixture = _FIXTURE_STORE.get(fixture_ref)
        if fixture:
            return fixture.copy()
        return None


def clear_secret_vault() -> None:
    """Clear all stored fixtures (for test cleanup)."""
    with _VAULT_LOCK:
        _FIXTURE_STORE.clear()


def apply_fixture_to_request(
    fixture_ref: str,
    headers: dict[str, str],
    query_items: list[tuple[str, str]],
    *,
    client: httpx.Client | None = None,
) -> None:
    """Apply credentials to the outgoing request headers or query items without revealing secrets."""
    fixture = get_secret_fixture(fixture_ref)
    if not fixture:
        return

    auth_type = fixture["auth_type"]
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {fixture['token']}"

    elif auth_type == "api_key":
        name = fixture["api_key_name"]
        val = fixture["api_key_value"]
        if fixture["api_key_location"] == "header":
            headers[name] = val
        else:
            query_items.append((name, val))

    elif auth_type == "basic":
        headers["Authorization"] = fixture["basic_header"]

    elif auth_type == "oauth2":
        token = _get_or_refresh_oauth2_token(fixture, client=client)
        if token:
            headers["Authorization"] = f"Bearer {token}"


def _get_or_refresh_oauth2_token(fixture: dict[str, Any], client: httpx.Client | None = None) -> str | None:
    now = perf_counter()
    with _VAULT_LOCK:
        if fixture.get("cached_token") and fixture.get("token_expiry", 0) > now + 30:
            return fixture["cached_token"]

    token_url = fixture["token_url"]
    # SSRF guard on token URL
    try:
        validated_url = validate_target_url(token_url, is_mock=True)
    except Exception:
        return None

    owns_client = client is None
    http_client = client or httpx.Client(timeout=10.0, follow_redirects=False)
    try:
        response = http_client.post(
            validated_url,
            data={
                "grant_type": "client_credentials",
                "client_id": fixture["client_id"],
                "client_secret": fixture["client_secret"],
                "scope": fixture.get("scope", ""),
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code == 200:
            payload = response.json()
            access_token = payload.get("access_token")
            expires_in = int(payload.get("expires_in") or 3600)
            if access_token:
                with _VAULT_LOCK:
                    ref = fixture["fixture_ref"]
                    if ref in _FIXTURE_STORE:
                        _FIXTURE_STORE[ref]["cached_token"] = access_token
                        _FIXTURE_STORE[ref]["token_expiry"] = perf_counter() + expires_in
                return access_token
    except Exception:
        pass
    finally:
        if owns_client:
            http_client.close()
    return None


def probe_api_connection(
    base_url: str,
    fixture_ref: str | None = None,
    probe_path: str = "/",
    probe_method: str = "GET",
    *,
    is_mock: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute a safe, bounded connection probe against the target API.

    Returns sanitized connectivity details without echoing secrets.
    """
    is_mock = is_mock or base_url.endswith((".test", ".example", ".invalid", ":8000", ":8001"))
    try:
        validated_base = validate_target_url(base_url, is_mock=is_mock, allow_local=is_mock)
    except ValueError as exc:
        return {
            "connected": False,
            "status_code": None,
            "latency_ms": 0,
            "classification": "invalid_target_origin",
            "message": f"Target origin validation failed: {str(exc)}",
        }

    # Normalize probe path
    clean_path = probe_path if probe_path.startswith("/") else f"/{probe_path}"
    target_url = f"{validated_base.rstrip('/')}{clean_path}"

    headers: dict[str, str] = {
        "User-Agent": "PiOne-OmniTest/3.0 (Connection-Probe)",
        "Accept": "application/json, */*",
    }
    query_items: list[tuple[str, str]] = []

    if fixture_ref:
        fixture = get_secret_fixture(fixture_ref)
        if not fixture:
            return {
                "connected": False,
                "status_code": None,
                "latency_ms": 0,
                "classification": "missing_fixture",
                "message": f"Auth fixture reference '{fixture_ref}' not found in vault",
            }
        apply_fixture_to_request(fixture_ref, headers, query_items, client=client)

    owns_client = client is None
    http_client = client or httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False)

    started = perf_counter()
    try:
        method = probe_method.upper() if probe_method.upper() in {"GET", "HEAD", "OPTIONS"} else "GET"
        resp = http_client.request(method, target_url, headers=headers, params=query_items)
        latency_ms = round((perf_counter() - started) * 1000)

        # Evaluate connection and auth status
        status_code = resp.status_code
        if status_code in {200, 201, 204}:
            classification = "auth_success" if fixture_ref else "endpoint_reachable"
            connected = True
            msg = f"Connection successful (HTTP {status_code})"
        elif status_code in {401, 403}:
            classification = "auth_forbidden" if status_code == 403 else "auth_unauthorized"
            connected = False
            msg = f"Authentication rejected by target (HTTP {status_code})"
        elif status_code in {301, 302, 307, 308}:
            classification = "endpoint_redirected"
            connected = True
            msg = f"Target returned redirect (HTTP {status_code})"
        elif status_code == 404:
            classification = "probe_endpoint_not_found"
            connected = True
            msg = f"Target reachable but probe route returned 404"
        else:
            classification = f"http_{status_code}"
            connected = status_code < 500
            msg = f"Target responded with HTTP {status_code}"

        return {
            "connected": connected,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "classification": classification,
            "message": msg,
        }

    except httpx.TimeoutException:
        return {
            "connected": False,
            "status_code": None,
            "latency_ms": round((perf_counter() - started) * 1000),
            "classification": "connection_timeout",
            "message": "Connection attempt timed out after 5 seconds",
        }
    except httpx.HTTPError as exc:
        return {
            "connected": False,
            "status_code": None,
            "latency_ms": round((perf_counter() - started) * 1000),
            "classification": "connection_failed",
            "message": f"Connection failed: {type(exc).__name__}",
        }
    finally:
        if owns_client:
            http_client.close()
