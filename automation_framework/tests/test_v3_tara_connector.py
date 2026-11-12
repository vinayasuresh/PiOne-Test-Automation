"""CONTRACT TESTS ONLY — NOT LIVE INTEGRATION PROOF.

These tests verify OmniTest's Tara connector contract, signature validation,
payload bounds, deduplication, and feature gating using safe deterministic local fixtures.
Live external integration is NOT verified until real CAT Sentinel staging webhook credentials,
signing secret, callback URL, and event payloads are provided and verified.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from api.models.v3_tara_models import TaraConnectorConfigRequest, TaraTicketEventRequest
from api.db.repository import DBRepository
from api.routers.v3_tara import _process_webhook, connector_status, validate_connector
from api.services.v3_tara_connector import normalize_ticket_event, validate_connector_config, verify_hmac_signature


def _event_payload():
    return {
        "tenant_id": "tenant-1",
        "ticket_id": "T-7",
        "event_id": "evt-1",
        "event_type": "ticket.created",
        "summary": "Report is stale",
        "affected_feature": "Reports",
        "expected_behavior": "Report loads current data",
        "actual_behavior": "Report remains stale",
        "target_origin": "https://example.com",
        "authorized_role": "analyst",
        "priority": "high",
        "retry_limit": 1,
        "assessment_mode": "both",
        "scenario_version": "1.0",
        "reporter_role": "user",
    }


def _signed_headers(body, secret="secret"):
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-OmniTest-Timestamp": timestamp,
        "X-OmniTest-Signature": signature,
        "content-type": "application/json",
    }


def test_hmac_signature_accepts_valid_and_rejects_invalid():
    """CONTRACT TEST: Validates HMAC-SHA256 signature acceptance and rejection."""
    body = b'{"ticket_id":"T-1"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"secret", f"{timestamp}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    verify_hmac_signature(body, timestamp, signature, "secret")
    with pytest.raises(ValueError):
        verify_hmac_signature(body, timestamp, "bad", "secret")


def test_hmac_rejects_stale_timestamp():
    """CONTRACT TEST: Rejects timestamps exceeding tolerance window."""
    with pytest.raises(ValueError, match="stale"):
        verify_hmac_signature(b"{}", str(int(time.time()) - 1000), "sha256=bad", "secret", tolerance_seconds=10)


def test_connector_config_is_sanitized_and_requires_reference():
    """CONTRACT TEST: Ensures connector config sanitization and secret_reference requirement."""
    request = TaraConnectorConfigRequest(connector_name="CAT", tenant_id="tenant-1", tenant_api_base_url="https://example.com", auth_mode="none")
    result = validate_connector_config(request)
    assert result["secret_configured"] is False
    assert "secret_reference" not in result
    with pytest.raises(ValueError, match="secret_reference"):
        validate_connector_config(request.model_copy(update={"auth_mode": "header_secret"}))


def test_ticket_normalization_is_deterministic_and_grounded():
    """CONTRACT TEST: Verifies deterministic normalization of incoming ticket event payload."""
    request = TaraTicketEventRequest(
        tenant_id="tenant-1", ticket_id="T-7", event_id="evt-1", summary="Report is stale",
        target_origin="https://example.com", assessment_mode="both", priority="high",
    )
    first = normalize_ticket_event(request)
    second = normalize_ticket_event(request)
    assert first == second
    assert first["mode"] == "both"
    assert first["correlation_id"].startswith("tara_")
    assert json.dumps(first).find("secret") == -1


def test_routes_are_feature_gated(monkeypatch):
    """CONTRACT TEST: Verifies that Tara endpoints return 404/503 when feature flags are disabled."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "false")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "false")
    with pytest.raises(HTTPException) as status_error:
        connector_status()
    assert status_error.value.status_code == 404
    with pytest.raises(HTTPException) as validation_error:
        validate_connector(
            TaraConnectorConfigRequest(
                connector_name="CAT",
                tenant_id="tenant-1",
                tenant_api_base_url="https://example.com",
                auth_mode="none",
            )
        )
    assert validation_error.value.status_code == 404


def test_status_reports_truthful_scope(monkeypatch):
    """CONTRACT TEST: Verifies /v3/tara/status truthfully reports enabled connector capabilities."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "true")
    assert connector_status() == {
        "enabled": True,
        "webhook": True,
        "polling": False,
        "result_callback": False,
    }


def test_connector_validation_sanitizes_secret(monkeypatch):
    """CONTRACT TEST: Validates that secret references are never echoed back in connector validation."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "true")
    result = validate_connector(
        TaraConnectorConfigRequest(
            connector_name="CAT",
            tenant_id="tenant-1",
            tenant_api_base_url="https://example.com",
            auth_mode="header_secret",
            secret_reference="vault://tara/webhook",
        )
    )
    assert result["config"]["secret_configured"] is True
    assert "vault://tara/webhook" not in json.dumps(result)
    assert "secret_reference" not in result["config"]


def test_webhook_rejects_bad_signature_and_oversized_content(monkeypatch):
    """CONTRACT TEST: Enforces signature verification and 256KB payload size limit."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("TARA_WEBHOOK_SECRET", "secret")
    body = json.dumps(_event_payload()).encode()
    bad = _signed_headers(body)
    bad["X-OmniTest-Signature"] = "0" * 64
    with pytest.raises(HTTPException) as bad_error:
        _process_webhook(body, bad, Response())
    assert bad_error.value.status_code == 401
    oversized = _signed_headers(body)
    oversized["Content-Length"] = "262145"
    with pytest.raises(HTTPException) as size_error:
        _process_webhook(b"{}", oversized, Response())
    assert size_error.value.status_code == 413


def test_webhook_rejects_malformed_json_and_payload_schema(monkeypatch):
    """CONTRACT TEST: Verifies rejection of non-JSON and missing required schema fields."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("TARA_WEBHOOK_SECRET", "secret")

    # Non-JSON raw bytes -> HTTP 422
    malformed_raw = b"this is not json"
    headers_bad_json = _signed_headers(malformed_raw)
    with pytest.raises(HTTPException) as exc_info:
        _process_webhook(malformed_raw, headers_bad_json, Response())
    assert exc_info.value.status_code == 422
    assert "invalid Tara ticket payload" in exc_info.value.detail

    # Missing required field (e.g. ticket_id, summary) -> HTTP 422
    incomplete_raw = json.dumps({"tenant_id": "tenant-1"}).encode()
    headers_incomplete = _signed_headers(incomplete_raw)
    with pytest.raises(HTTPException) as schema_exc:
        _process_webhook(incomplete_raw, headers_incomplete, Response())
    assert schema_exc.value.status_code == 422
    assert "invalid Tara ticket payload" in schema_exc.value.detail


def test_webhook_accepts_and_deduplicates_with_allowlisted_response(monkeypatch):
    """CONTRACT TEST: Verifies idempotent handling, returning 201 for first and 200 for duplicates."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("TARA_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("TARA_WEBHOOK_SECRET", "secret")
    calls = []

    def fake(payload):
        calls.append(payload)
        return {
            "created": len(calls) == 1,
            "incident": {"incident_id": "inc-1", "jobs": [{"job_id": "job-1"}]},
        }

    monkeypatch.setattr(DBRepository, "create_or_get_v3_incident_job", staticmethod(fake))
    body = json.dumps(_event_payload()).encode()
    first_response = Response()
    second_response = Response()
    first = _process_webhook(body, _signed_headers(body), first_response)
    second = _process_webhook(body, _signed_headers(body), second_response)
    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert set(first) <= {"status", "correlation_id", "created", "incident_id", "job_id"}
    assert "secret" not in json.dumps(first).lower()
