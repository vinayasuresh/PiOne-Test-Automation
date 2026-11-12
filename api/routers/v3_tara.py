"""Feature-gated signed webhook endpoints for Tara/helpdesk intake."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request, Response

from api.models.v3_tara_models import TaraConnectorConfigRequest, TaraTicketEventRequest
from api.db.repository import DBRepository
from api.services.v3_feature_flags import is_tara_integration_enabled
from api.services.v3_tara_connector import normalize_ticket_event, validate_connector_config, verify_hmac_signature


router = APIRouter(prefix="/v3/tara", tags=["V3 Tara connector"])


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Return a request header without depending on casing of a test/client mapping."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _require_tara() -> None:
    if not is_tara_integration_enabled():
        raise HTTPException(status_code=404, detail="Tara integration is disabled")


@router.get("/status")
def connector_status() -> dict[str, object]:
    _require_tara()
    return {"enabled": True, "webhook": True, "polling": False, "result_callback": False}


@router.post("/connectors/validate")
def validate_connector(request: TaraConnectorConfigRequest) -> dict[str, object]:
    _require_tara()
    try:
        return {"valid": True, "config": validate_connector_config(request)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _process_webhook(
    raw_body: bytes, headers: Mapping[str, str], response: Response
) -> dict[str, object]:
    _require_tara()
    secret = os.getenv("TARA_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Tara webhook secret is not configured")
    content_length = _header(headers, "Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if declared_length > 262_144:
            raise HTTPException(status_code=413, detail="webhook payload is too large")
    if len(raw_body) > 262_144:
        raise HTTPException(status_code=413, detail="webhook payload is too large")
    try:
        verify_hmac_signature(
            raw_body,
            _header(headers, "X-OmniTest-Timestamp") or "",
            _header(headers, "X-OmniTest-Signature") or "",
            secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        event = TaraTicketEventRequest.model_validate_json(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid Tara ticket payload") from exc
    payload = normalize_ticket_event(event)
    result = DBRepository.create_or_get_v3_incident_job(payload)
    created = bool(result.get("created"))
    response.status_code = 201 if created else 200
    incident = result.get("incident") if isinstance(result, dict) else None
    incident = incident if isinstance(incident, dict) else {}
    jobs = incident.get("jobs") if isinstance(incident.get("jobs"), list) else []
    first_job = jobs[0] if jobs and isinstance(jobs[0], dict) else {}
    return {
        "status": "accepted" if created else "duplicate",
        "correlation_id": payload["correlation_id"],
        "created": created,
        "incident_id": incident.get("incident_id"),
        "job_id": first_job.get("job_id"),
    }


@router.post("/webhooks/tickets")
async def webhook(request: Request, response: Response) -> dict[str, object]:
    return _process_webhook(await request.body(), dict(request.headers), response)
