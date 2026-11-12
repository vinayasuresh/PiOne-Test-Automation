"""Safe, signed intake helpers for connecting Tara/helpdesk systems to OmniTest."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from api.models.v3_tara_models import TaraConnectorConfigRequest, TaraTicketEventRequest
from api.utils.security_guard import validate_target_url


def verify_hmac_signature(raw_body: bytes, timestamp: str, signature: str, secret: str, *, tolerance_seconds: int | None = None) -> None:
    """Validate ``timestamp.body`` with an HMAC-SHA256 signature."""

    if not timestamp or not signature or not secret:
        raise ValueError("missing signature inputs")
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid signature timestamp") from exc
    if tolerance_seconds is None:
        try:
            tolerance = int(os.getenv("TARA_SIGNATURE_TOLERANCE_SECONDS", "300"))
        except ValueError as exc:
            raise ValueError("invalid signature tolerance") from exc
    else:
        tolerance = tolerance_seconds
    if tolerance < 1 or tolerance > 900:
        raise ValueError("invalid signature tolerance")
    if timestamp != str(timestamp_value):
        raise ValueError("invalid signature timestamp")
    if abs(int(time.time()) - timestamp_value) > tolerance:
        raise ValueError("stale signature")
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw_body, hashlib.sha256).hexdigest()
    provided = signature.removeprefix("sha256=")
    if len(provided) != 64 or any(char not in "0123456789abcdefABCDEF" for char in provided):
        raise ValueError("invalid signature")
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid signature")


def validate_connector_config(request: TaraConnectorConfigRequest) -> dict[str, Any]:
    allow_local = os.getenv("TARA_CONNECTOR_ALLOW_LOCAL", "").strip().lower() in {"1", "true", "yes", "on"}
    tenant_url = validate_target_url(request.tenant_api_base_url, allow_local=allow_local, is_mock=True)
    admin_url = None
    if request.admin_api_base_url:
        admin_url = validate_target_url(request.admin_api_base_url, allow_local=allow_local, is_mock=True)
    if request.auth_mode != "none" and not request.secret_reference:
        raise ValueError("secret_reference is required when authentication is enabled")
    return {
        "connector_name": request.connector_name,
        "tenant_id": request.tenant_id,
        "tenant_api_base_url": tenant_url,
        "admin_api_base_url": admin_url,
        "result_callback_path": request.result_callback_path,
        "auth_mode": request.auth_mode,
        "trigger_mode": request.trigger_mode,
        "polling_interval_seconds": request.polling_interval_seconds,
        "secret_configured": bool(request.secret_reference),
    }


def normalize_ticket_event(request: TaraTicketEventRequest) -> dict[str, Any]:
    seed = f"{request.tenant_id}|{request.ticket_id}|{request.event_id}".encode("utf-8")
    correlation_id = "tara_" + hashlib.sha256(seed).hexdigest()[:40]
    mode = {"functional": "functional_only", "security": "security_only", "both": "both"}[request.assessment_mode]
    return {
        "correlation_id": correlation_id,
        "ticket_reference": request.ticket_id,
        "grouping_key": f"{request.tenant_id}:{request.ticket_id}",
        "summary": request.summary,
        "affected_feature": request.affected_feature,
        "reporter_role": request.reporter_role,
        "expected_behavior": request.expected_behavior,
        "actual_behavior": request.actual_behavior,
        "priority": request.priority.title(),
        "scenario_version": request.scenario_version,
        "target": request.target_origin,
        "authorized_role": request.authorized_role,
        "mode": mode,
        "retry_limit": request.retry_limit,
        "links": {},
    }
