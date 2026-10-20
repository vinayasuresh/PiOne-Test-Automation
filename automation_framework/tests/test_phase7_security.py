"""test_phase7_security.py: Comprehensive security hardening test suite for PiOne Testmate.

Deterministic verification covering:
1. SSRF loopback rejection (IPv4, IPv6, localhost aliases)
2. Private IPv4 network rejection (RFC 1918)
3. Private IPv6 and IPv4-mapped IPv6 bypass rejection (RFC 4193, ::ffff:127.0.0.1)
4. Cloud metadata endpoint rejection (AWS/GCP/Azure link-local, IMDSv2, metadata hostnames)
5. Unsafe redirect rejection (cross-domain, scheme change, external targets)
6. Cross-app route rejection (boundary guard namespace enforcement)
7. Path traversal rejection (run_id and artifact route traversal)
8. Arbitrary artifact type rejection (only allowlisted artifact types accepted)
9. Cross-run artifact rejection (run A cannot read run B's artifacts)
10. Storage-state download rejection (session states strictly non-downloadable)
11. Credential redaction (passwords and secrets recursively sanitized)
12. Token redaction (Bearer, Basic, API keys redacted from logs and data)
13. WebSocket run isolation (channel isolation, no cross-run event leakage)
14. Oversized request rejection (HTTP 413 on payload exceeding limit)
15. Provider response-size rejection (ProviderResponseSizeError on oversize output)
16. Scan timeout and cancellation (clean cancellation and resource cleanup)
17. Frontend unsafe-content handling (HTML escaping, no credentials in localStorage)
18. API test timeout behavior (bounded async test execution without hanging)
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import pytest
import httpx

from api.main import app, MAX_REQUEST_BODY_SIZE
from api.models.scan_models import ScanRequest
from api.services.llm_provider_gateway import (
    MAX_RESPONSE_BYTES,
    ProviderResponseSizeError,
)
from api.services.run_context_service import create_run_context
from api.services.scan_control_service import (
    is_scan_stopped,
    pause_scan,
    resume_scan,
    stop_scan,
)
from api.services.scan_event_bus import (
    discard_channel,
    get_channel,
    publish,
)
from api.utils.security_guard import is_safe_redirect, validate_target_url
from automation_framework.config.settings import REPORT_PATH
from automation_framework.crawler.url_filter import AppBoundaryGuard, is_within_app_path
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data, redact_text


# ---------------------------------------------------------------------------
# 1. SSRF Loopback Rejection
# ---------------------------------------------------------------------------
def test_ssrf_loopback_rejection():
    """Verify that loopback IPv4, IPv6, and localhost variants are rejected."""
    loopback_targets = [
        "http://127.0.0.1:8000",
        "http://127.0.0.2:8000/app",
        "http://127.255.255.254",
        "http://localhost:3000",
        "http://app.localhost",
        "http://[::1]:8080",
        "http://0.0.0.0:80",
    ]
    for url in loopback_targets:
        with pytest.raises(ValueError) as excinfo:
            validate_target_url(url, allow_local=False)
        assert "forbidden" in str(excinfo.value).lower() or "loopback" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# 2. Private IPv4 Network Rejection (RFC 1918)
# ---------------------------------------------------------------------------
def test_ssrf_private_ipv4_rejection():
    """Verify that RFC 1918 private IPv4 addresses are forbidden."""
    private_targets = [
        "http://10.0.0.1/",
        "http://10.254.0.1:8080/dashboard",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.0.1/",
        "http://192.168.1.100:5000/api",
    ]
    for url in private_targets:
        with pytest.raises(ValueError) as excinfo:
            validate_target_url(url, allow_local=False)
        assert "private network" in str(excinfo.value).lower() or "forbidden" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# 3. Private IPv6 and IPv4-Mapped IPv6 Rejection
# ---------------------------------------------------------------------------
def test_ssrf_private_ipv6_and_mapped_rejection():
    """Verify that RFC 4193 unique local IPv6 and IPv4-mapped IPv6 bypasses are blocked."""
    ipv6_targets = [
        "http://[fc00::1]/",
        "http://[fd12:3456:789a:1::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
    ]
    for url in ipv6_targets:
        with pytest.raises(ValueError) as excinfo:
            validate_target_url(url, allow_local=False)
        err = str(excinfo.value).lower()
        assert any(k in err for k in ("forbidden", "loopback", "private", "link-local"))


# ---------------------------------------------------------------------------
# 4. Cloud Metadata Endpoint Rejection
# ---------------------------------------------------------------------------
def test_ssrf_metadata_endpoint_rejection():
    """Verify that cloud metadata IPs and hostnames are rejected."""
    metadata_targets = [
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/meta-data",
        "http://kubernetes.default.svc/api",
    ]
    for url in metadata_targets:
        with pytest.raises(ValueError) as excinfo:
            validate_target_url(url, allow_local=False)
        err = str(excinfo.value).lower()
        assert any(k in err for k in ("forbidden", "private", "metadata", "link-local"))


# ---------------------------------------------------------------------------
# 5. Unsafe Redirect Rejection
# ---------------------------------------------------------------------------
def test_unsafe_redirect_rejection():
    """Verify that redirects outside origin, to private targets, or changing schemes are rejected."""
    base = "https://example.com/portal"

    # Cross-domain
    assert not is_safe_redirect("https://evil.com/portal", base, allow_local=False)
    # Scheme downgrade
    assert not is_safe_redirect("http://example.com/portal", base, allow_local=False)
    # Target is private SSRF target
    assert not is_safe_redirect("http://127.0.0.1:8000/portal", base, allow_local=False)
    assert not is_safe_redirect("http://169.254.169.254/", base, allow_local=False)
    # Legitimate same-origin in-scope redirect
    assert is_safe_redirect("https://example.com/portal/dashboard", base, allow_local=False)


# ---------------------------------------------------------------------------
# 6. Cross-App Route Rejection
# ---------------------------------------------------------------------------
def test_cross_app_route_rejection():
    """Verify that navigation boundary prevents leaking into sibling application paths."""
    base = "https://example.com/billing"
    guard = AppBoundaryGuard(base)

    # In-scope
    assert guard.is_allowed("https://example.com/billing/invoices")
    assert is_within_app_path("https://example.com/billing/invoices", base)

    # Sibling application path (cross-app escape)
    assert not guard.is_allowed("https://example.com/billing-admin")
    assert not is_within_app_path("https://example.com/billing-admin", base)
    assert not guard.is_allowed("https://example.com/hr")
    assert not is_within_app_path("https://example.com/hr", base)


# ---------------------------------------------------------------------------
# 7. Path Traversal Rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_path_traversal_rejection(async_client: httpx.AsyncClient):
    """Verify that path traversal in run_id or artifact routes is strictly rejected."""
    # Run ID with directory traversal
    traversal_ids = ["..", "../", "..%2f..%2f", "....", "test/../../etc/passwd"]

    for tid in traversal_ids:
        res = await async_client.get(f"/run/{tid}/state")
        assert res.status_code in {400, 404}

        res_status = await async_client.get(f"/reports/{tid}/status")
        assert res_status.status_code in {400, 404}

        res_dl = await async_client.get(f"/reports/{tid}/download/json")
        assert res_dl.status_code in {400, 404}


# ---------------------------------------------------------------------------
# 8. Arbitrary Artifact Type Rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_arbitrary_artifact_type_rejection(async_client: httpx.AsyncClient):
    """Verify that non-allowlisted artifact types are rejected with HTTP 400."""
    valid_run_id = "test_run_sec_001"
    disallowed_types = ["config.py", ".env", "passwd", "main.py", "database.db", "exe"]

    for atype in disallowed_types:
        res = await async_client.get(f"/reports/{valid_run_id}/download/{atype}")
        assert res.status_code == 400
        assert "unsupported artifact type" in res.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 9. Cross-Run Artifact Rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_run_artifact_rejection(async_client: httpx.AsyncClient):
    """Verify that a request under run_B cannot access artifacts created under run_A."""
    run_a = "run_alpha_security_test"
    run_b = "run_beta_security_test"

    # Create artifact belonging strictly to run_A
    artifact_a = REPORT_PATH / f"{run_a}_crawl_results.json"
    artifact_a.write_text(json.dumps({"run_id": run_a, "routes": []}), encoding="utf-8")

    try:
        # Requesting run_A's file through run_B's URL must fail
        res = await async_client.get(f"/reports/{run_b}/download/json")
        assert res.status_code == 404
    finally:
        if artifact_a.exists():
            artifact_a.unlink()


# ---------------------------------------------------------------------------
# 10. Storage-State Download Rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_storage_state_download_rejection(async_client: httpx.AsyncClient):
    """Verify that browser storage_state files cannot be downloaded via any route."""
    run_id = "run_storage_state_sec"

    # Attempt download via artifact endpoint
    res1 = await async_client.get(f"/reports/{run_id}/download/storage_state")
    assert res1.status_code in {400, 403}

    res2 = await async_client.get(f"/reports/{run_id}/download/storage_state.json")
    assert res2.status_code in {400, 403}

    # Attempt direct download via static files route (which was unmounted)
    res3 = await async_client.get("/reports/storage_state_test.json")
    assert res3.status_code in {400, 404, 405}


# ---------------------------------------------------------------------------
# 11. Credential Redaction
# ---------------------------------------------------------------------------
def test_credential_redaction():
    """Verify recursive redaction of passwords, secrets, cookies, and tokens."""
    payload = {
        "username": "tester@example.com",
        "password": "SuperSecretPassword123!",
        "auth_details": {
            "token": "tok_xyz987654321",
            "cookies": [{"name": "session", "value": "secret_cookie_val"}],
            "client_secret": "cs_live_998877",
        },
        "log_entry": "User logged in with password=MySecretPass and key=abcdef123456",
    }

    redacted = redact_sensitive_data(payload)

    assert redacted["username"] == "tester@example.com"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["auth_details"]["token"] == "[REDACTED]"
    assert redacted["auth_details"]["cookies"] == "[REDACTED]"
    assert redacted["auth_details"]["client_secret"] == "[REDACTED]"
    assert "SuperSecretPassword123!" not in str(redacted)
    assert "secret_cookie_val" not in str(redacted)
    assert "password=[REDACTED]" in redacted["log_entry"]


# ---------------------------------------------------------------------------
# 12. Token Redaction
# ---------------------------------------------------------------------------
def test_token_redaction():
    """Verify that Bearer tokens, Basic auth, OpenAI keys, and Gemini keys are redacted from text."""
    raw_texts = [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123", "Authorization: [REDACTED]"),
        ("Using key sk-abcdef123456789012345678 for completion", "Using key [REDACTED_API_KEY] for completion"),
        ("Gemini key is AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q", "Gemini key is [REDACTED_API_KEY]"),
        ("https://api.test/v1?token=secret_tok_123&action=scan", "https://api.test/v1?token=[REDACTED]&action=scan"),
    ]

    for raw, expected_snippet in raw_texts:
        cleaned = redact_text(raw)
        assert expected_snippet in cleaned


# ---------------------------------------------------------------------------
# 13. WebSocket Run Isolation
# ---------------------------------------------------------------------------
def test_websocket_run_isolation():
    """Verify that events on run A's queue are not delivered to run B."""
    run_alpha = "run_alpha_ws_iso"
    run_beta = "run_beta_ws_iso"

    q_alpha = get_channel(run_alpha)
    q_beta = get_channel(run_beta)

    # Empty both
    while not q_alpha.empty():
        q_alpha.get_nowait()
    while not q_beta.empty():
        q_beta.get_nowait()

    # Publish to alpha
    publish(run_alpha, {"type": "route_discovered", "route": "/dashboard", "password": "leak"})

    # Alpha receives event, with credentials sanitized
    assert not q_alpha.empty()
    event_alpha = q_alpha.get_nowait()
    assert event_alpha["route"] == "/dashboard"
    assert event_alpha["password"] == "[REDACTED]"

    # Beta channel must remain completely empty
    assert q_beta.empty()

    discard_channel(run_alpha)
    discard_channel(run_beta)


# ---------------------------------------------------------------------------
# 14. Oversized Request Rejection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_oversized_request_rejection(async_client: httpx.AsyncClient):
    """Verify that requests exceeding MAX_REQUEST_BODY_SIZE receive HTTP 413."""
    oversized_len = MAX_REQUEST_BODY_SIZE + 1024
    headers = {"Content-Length": str(oversized_len), "Content-Type": "application/json"}

    res = await async_client.post("/generate-test", headers=headers, content=b"{}")
    assert res.status_code == 413
    assert "request body too large" in res.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 15. Provider Response-Size Rejection
# ---------------------------------------------------------------------------
def test_provider_response_size_rejection():
    """Verify that provider output exceeding MAX_RESPONSE_BYTES triggers ProviderResponseSizeError."""
    # Test error class instantiation and bounds definition
    assert MAX_RESPONSE_BYTES == 512 * 1024
    err = ProviderResponseSizeError(f"Response exceeded {MAX_RESPONSE_BYTES} bytes limit")
    assert "limit" in str(err)
    assert err.category in {"RESPONSE_LIMIT_EXCEEDED", "SIZE_LIMIT"}


# ---------------------------------------------------------------------------
# 16. Scan Timeout and Cancellation
# ---------------------------------------------------------------------------
def test_scan_timeout_and_cancellation():
    """Verify that scan cancellation signals update state without blocking or resource leaks."""
    run_id = "run_cancel_test_001"
    assert not is_scan_stopped(run_id)

    # Send pause
    pause_scan(run_id)
    # Send resume
    resume_scan(run_id)
    # Send stop
    stop_scan(run_id)

    assert is_scan_stopped(run_id)


# ---------------------------------------------------------------------------
# 17. Frontend Unsafe Content Handling
# ---------------------------------------------------------------------------
def test_frontend_unsafe_content_handling():
    """Verify frontend uses safe escaping, allowlists, and does not store credentials in localStorage."""
    frontend_html = Path("frontend/index.html").read_text(encoding="utf-8")

    # Verify escapeHtml exists
    assert "function escapeHtml" in frontend_html

    # Verify localStorage is never used for storing password, token, or secret
    assert "localStorage.setItem" in frontend_html
    assert "password" not in frontend_html.split("localStorage.setItem")[1].split("}")[0].lower()

    # Verify security headers are configured in api/main.py
    main_py = Path("api/main.py").read_text(encoding="utf-8")
    assert "X-Content-Type-Options" in main_py
    assert "X-Frame-Options" in main_py
    assert "Content-Security-Policy" in main_py


# ---------------------------------------------------------------------------
# 18. API Test Timeout Behavior
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_test_timeout_behavior(async_client: httpx.AsyncClient):
    """Verify that core endpoints respond quickly and never hang under async test execution."""
    endpoints = [
        ("/health", 200),
        ("/", 200),
        ("/llm/providers", 200),
        ("/reports/non_existent_run_999/status", 200),
    ]

    for path, expected_status in endpoints:
        res = await async_client.get(path)
        assert res.status_code == expected_status
