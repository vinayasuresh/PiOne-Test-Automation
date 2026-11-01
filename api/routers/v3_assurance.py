"""Additive V3 assurance API.

The router only exposes durable metadata; it never starts a browser crawl or
changes the V2 scan lifecycle.  Every endpoint is hidden until the explicit
V3 feature flag is enabled.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse

from api.db.repository import DBRepository
from api.models.v3_assurance_models import (
    V3AttemptRequest,
    V3EvidenceManifestRequest,
    V3IncidentIntakeRequest,
    V3JobTransitionRequest,
)
from api.models.v3_api_contract_models import (
    ApiContractRegistrationRequest,
    ApiOperationConfigurationRequest,
    ApiOperationValidationRequest,
)
from api.services.v3_api_contract_service import normalize_contract, validate_registered_operation
from api.services.v3_feature_flags import is_v3_assurance_enabled


router = APIRouter(prefix="/v3", tags=["V3 assurance"])
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _require_v3() -> None:
    if not is_v3_assurance_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="V3 assurance is disabled")


def _validate_id(value: str, label: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {label}")


@router.post("/incidents/intake", status_code=status.HTTP_201_CREATED)
def intake_incident(request: V3IncidentIntakeRequest, response: Response) -> dict:
    """Idempotently persist a sanitized incident and one durable job."""
    _require_v3()
    result = DBRepository.create_or_get_v3_incident_job(request.model_dump())
    # A replay is successful, but must truthfully communicate that no duplicate
    # record or V2 run was started.
    response.status_code = status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
    return {"status": "created" if result["created"] else "existing", **result}


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str) -> dict:
    _require_v3()
    _validate_id(incident_id, "incident ID")
    incident = DBRepository.get_v3_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return {"status": "ok", "incident": incident}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    _require_v3()
    _validate_id(job_id, "job ID")
    job = DBRepository.get_v3_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification job not found")
    return {"status": "ok", "job": job}


@router.post("/jobs/{job_id}/transition")
def transition_job(job_id: str, request: V3JobTransitionRequest) -> dict:
    _require_v3()
    _validate_id(job_id, "job ID")
    try:
        job = DBRepository.transition_v3_job(job_id, request.state, request.verdict, request.technical_category)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "ok", "job": job}


@router.post("/jobs/{job_id}/attempts", status_code=status.HTTP_201_CREATED)
def add_attempt(job_id: str, request: V3AttemptRequest) -> dict:
    _require_v3()
    _validate_id(job_id, "job ID")
    try:
        attempt = DBRepository.add_v3_attempt(job_id, **request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "attempt": attempt}


@router.post("/jobs/{job_id}/evidence", status_code=status.HTTP_201_CREATED)
def add_evidence_manifest(job_id: str, request: V3EvidenceManifestRequest) -> dict:
    _require_v3()
    _validate_id(job_id, "job ID")
    try:
        manifest = DBRepository.add_v3_evidence_manifest(job_id, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "created", "evidence_manifest": manifest}


@router.post("/api-contracts", status_code=status.HTTP_201_CREATED)
def register_api_contract(request: ApiContractRegistrationRequest) -> dict:
    """Register only normalized, reviewed API operations behind the V3 flag."""
    _require_v3()
    try:
        operations = normalize_contract(request.source_type, request.source_document, request.base_url)
        # source_document is deliberately excluded from persistence.
        contract = DBRepository.register_v3_api_contract(
            request.model_dump(exclude={"source_document"}),
            operations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created", "contract": contract}


@router.get("/api-contracts/{contract_id}")
def get_api_contract(contract_id: str) -> dict:
    _require_v3()
    _validate_id(contract_id, "contract ID")
    contract = DBRepository.get_v3_api_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API contract not found")
    return {"status": "ok", "contract": contract}


@router.put("/api-operations/{operation_id}/rules")
def configure_api_operation(operation_id: str, request: ApiOperationConfigurationRequest) -> dict:
    _require_v3()
    _validate_id(operation_id, "operation ID")
    try:
        operation = DBRepository.configure_v3_api_operation(operation_id, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "ok", "operation": operation}


@router.post("/api-operations/{operation_id}/validate", status_code=status.HTTP_201_CREATED)
def validate_api_operation(operation_id: str, request: ApiOperationValidationRequest) -> dict:
    """Validate exactly one approved, registered read-only operation."""
    _require_v3()
    _validate_id(operation_id, "operation ID")
    registration = DBRepository.get_v3_api_operation(operation_id)
    if not registration or not registration.get("contract"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API operation not found")
    observation_result = validate_registered_operation(
        registration["contract"],
        registration["operation"],
        negative_case=request.negative_case,
    )
    try:
        observation = DBRepository.add_v3_api_observation({
            "operation_id": operation_id,
            "job_id": request.job_id,
            "run_id": request.run_id,
            "evidence_manifest_id": request.evidence_manifest_id,
            "scenario_version": request.scenario_version,
            "result": observation_result["result"],
            "classification": observation_result["classification"],
            "status_code": observation_result["status_code"],
            "latency_ms": observation_result["latency_ms"],
            "request_metadata": observation_result["request_metadata"],
            "assertion_result": observation_result["assertion_result"],
            "evidence_link": "",
        })
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "observation": observation}


@router.get("/api-contracts/{contract_id}/observations/export")
def export_api_observations(contract_id: str) -> JSONResponse:
    """Return sanitized observation records; credentials are never stored or exported."""
    _require_v3()
    _validate_id(contract_id, "contract ID")
    if not DBRepository.get_v3_api_contract(contract_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API contract not found")
    return JSONResponse({"status": "ok", "observations": DBRepository.list_v3_api_observations(contract_id)})
