"""Additive V3 assurance API.

The router only exposes durable metadata; it never starts a browser crawl or
changes the V2 scan lifecycle.  Every endpoint is hidden until the explicit
V3 feature flag is enabled.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

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
from api.models.v3_data_assertion_models import DataAssertionEvaluationRequest, DataAssertionScenarioRequest
from api.services.v3_data_assertion_service import compare_dashboard_data, parse_timestamp, read_approved_source
from api.services.v3_feature_flags import is_v3_assurance_enabled
from api.models.v3_orchestration_models import (
    AssuranceScenarioRequest, AssignmentRequest, AssignmentRuleRequest,
    ManualMonitoringTriggerRequest, MonitoringScheduleRequest, V3OrchestrateRequest, V3RetryRequest,
)
from api.services.v3_orchestration_service import execute_approved_scenario, plan_assurance_job


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


@router.get("/incidents")
def list_incidents(status_filter: str | None = None, owner_team: str | None = None) -> dict:
    """Internal helpdesk queue; it is intentionally flag-gated with V3."""
    _require_v3()
    return {"status": "ok", "incidents": DBRepository.list_v3_incidents(status_filter, owner_team)}


@router.post("/scenarios", status_code=status.HTTP_201_CREATED)
def register_assurance_scenario(request: AssuranceScenarioRequest) -> dict:
    _require_v3()
    try:
        scenario = DBRepository.register_v3_assurance_scenario(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "scenario": scenario}


@router.get("/scenarios")
def list_assurance_scenarios() -> dict:
    _require_v3()
    return {"status": "ok", "scenarios": DBRepository.list_v3_assurance_scenarios()}


@router.post("/jobs/{job_id}/orchestrate")
def orchestrate_job(job_id: str, request: V3OrchestrateRequest) -> dict:
    """Run exactly one approved, bounded V3 check; never a general browser agent."""
    _require_v3(); _validate_id(job_id, "job ID"); _validate_id(request.scenario_id, "scenario ID")
    job = DBRepository.get_v3_job(job_id)
    scenario = DBRepository.get_v3_assurance_scenario(request.scenario_id)
    if not job or not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification job or assurance scenario not found")
    incident = DBRepository.get_v3_incident(job["incident_id"])
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked incident not found")
    plan = plan_assurance_job(job, incident, scenario)
    try:
        DBRepository.bind_v3_job_scenario(job_id, request.scenario_id, plan["decision"])
        job = DBRepository.transition_v3_job(job_id, "queued") if job["state"] == "created" else job
        if not plan["ready"]:
            attempt = DBRepository.add_v3_attempt(job_id, request.worker_id, "blocked", plan["verdict"], plan["category"], plan["decision"]["events"])
            evidence = DBRepository.add_v3_evidence_manifest(job_id, {
                "source_step": "V3 policy decision", "source_route": scenario["target_origin"],
                "content_type": "application/json",
                "storage_location": f"v3://verification-jobs/{job_id}/attempts/{attempt['attempt_id']}",
            })
            job = DBRepository.transition_v3_job(job_id, "blocked", plan["verdict"], plan["category"])
            return {"status": "blocked", "job": job, "attempt": attempt, "evidence_manifest": evidence, "policy": plan["decision"]}
        DBRepository.transition_v3_job(job_id, "running")
        registration = DBRepository.get_v3_api_operation(scenario["reference_id"]) if scenario["scenario_type"] == "api_contract" else None
        outcome = execute_approved_scenario(scenario, registration)
        terminal_state = "completed" if outcome["verdict"] in {"reproduced", "not_reproduced"} else outcome["verdict"]
        attempt = DBRepository.add_v3_attempt(job_id, request.worker_id, terminal_state, outcome["verdict"], outcome["category"], outcome["events"])
        evidence = DBRepository.add_v3_evidence_manifest(job_id, {
            "source_step": "V3 bounded verification", "source_route": scenario["target_origin"],
            "content_type": "application/json",
            "storage_location": f"v3://verification-jobs/{job_id}/attempts/{attempt['attempt_id']}",
        })
        job = DBRepository.transition_v3_job(job_id, terminal_state, outcome["verdict"], outcome["category"])
        if outcome["verdict"] == "reproduced":
            DBRepository.auto_assign_v3_incident(job["incident_id"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "completed", "job": job, "attempt": attempt, "evidence_manifest": evidence, "outcome": outcome, "policy": plan["decision"]}


@router.post("/jobs/{job_id}/retry")
def retry_orchestration_job(job_id: str, request: V3RetryRequest) -> dict:
    """Retry the same approved scenario within its recorded retry limit."""
    _require_v3(); _validate_id(job_id, "job ID")
    try:
        job = DBRepository.retry_v3_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not job.get("scenario_id"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Retry requires a previously bound approved scenario")
    return orchestrate_job(job_id, V3OrchestrateRequest(scenario_id=job["scenario_id"], worker_id=request.worker_id))


@router.post("/assignment-rules", status_code=status.HTTP_201_CREATED)
def create_assignment_rule(request: AssignmentRuleRequest) -> dict:
    _require_v3()
    return {"status": "created", "rule": DBRepository.create_v3_assignment_rule(request.model_dump())}


@router.post("/incidents/{incident_id}/assign")
def assign_incident(incident_id: str, request: AssignmentRequest) -> dict:
    _require_v3(); _validate_id(incident_id, "incident ID")
    try:
        incident = DBRepository.assign_v3_incident(incident_id, request.owner_team, request.next_action)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "ok", "incident": incident}


@router.post("/incidents/{incident_id}/auto-assign")
def auto_assign_incident(incident_id: str) -> dict:
    _require_v3(); _validate_id(incident_id, "incident ID")
    try:
        incident = DBRepository.auto_assign_v3_incident(incident_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "ok", "incident": incident}


@router.post("/monitoring-schedules", status_code=status.HTTP_201_CREATED)
def create_monitoring_schedule(request: MonitoringScheduleRequest) -> dict:
    """Persist a schedule definition only. A production scheduler is not started here."""
    _require_v3()
    try:
        schedule = DBRepository.create_v3_monitoring_schedule(request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "created", "schedule": schedule}


@router.get("/monitoring-schedules")
def list_monitoring_schedules() -> dict:
    _require_v3()
    return {"status": "ok", "schedules": DBRepository.list_v3_monitoring_schedules(), "note": "Stored definitions do not start a scheduler."}


@router.post("/monitoring-schedules/{schedule_id}/trigger", status_code=status.HTTP_201_CREATED)
def trigger_monitoring_schedule(schedule_id: str, request: ManualMonitoringTriggerRequest) -> dict:
    """Create one operator-requested job; this endpoint never starts a scheduler."""
    _require_v3(); _validate_id(schedule_id, "monitoring schedule ID")
    try:
        result = DBRepository.trigger_v3_monitoring_schedule(schedule_id, request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "note": "Manual trigger created one verification job; no scheduler was started.", **result}


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


@router.post("/data-assertion-scenarios", status_code=status.HTTP_201_CREATED)
def register_data_assertion_scenario(request: DataAssertionScenarioRequest) -> dict:
    """Register an approved comparison against an existing read-only API operation."""
    _require_v3()
    try:
        scenario = DBRepository.register_v3_data_assertion_scenario(request.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "scenario": scenario}


@router.get("/data-assertion-scenarios/{scenario_id}")
def get_data_assertion_scenario(scenario_id: str) -> dict:
    _require_v3()
    _validate_id(scenario_id, "data assertion scenario ID")
    scenario = DBRepository.get_v3_data_assertion_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data assertion scenario not found")
    return {"status": "ok", "scenario": scenario}


@router.post("/data-assertion-scenarios/{scenario_id}/evaluate", status_code=status.HTTP_201_CREATED)
def evaluate_data_assertion_scenario(scenario_id: str, request: DataAssertionEvaluationRequest) -> dict:
    """Compare a captured UI value to the scenario's approved API source only."""
    _require_v3()
    _validate_id(scenario_id, "data assertion scenario ID")
    scenario = DBRepository.get_v3_data_assertion_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data assertion scenario not found")
    if not scenario["is_approved"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Data assertion scenario is not approved")
    source_registration = DBRepository.get_v3_api_operation(scenario["source_operation_id"])
    if not source_registration or not source_registration.get("contract"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved source operation is unavailable")
    source_reading = read_approved_source(
        source_registration["contract"], source_registration["operation"],
        scenario["source_field"], scenario["source_timestamp_field"],
    )
    comparison = compare_dashboard_data(scenario, request.model_dump(), source_reading)
    try:
        observation = DBRepository.add_v3_data_assertion_observation({
            "scenario_id": scenario_id,
            "job_id": request.job_id,
            "run_id": request.run_id,
            "evidence_manifest_id": request.evidence_manifest_id,
            "scenario_version": request.scenario_version,
            "result": comparison["result"],
            "classification": comparison["classification"],
            "ui_raw_value": comparison["ui_raw_value"],
            "source_raw_value": comparison["source_raw_value"],
            "normalized_ui_value": comparison.get("normalized_ui_value"),
            "normalized_source_value": comparison.get("normalized_source_value"),
            "delta": comparison.get("delta"),
            "filter_context": comparison["filter_context"],
            "source_filter_context": comparison["source_filter_context"],
            "source_fresh_at": parse_timestamp(comparison.get("source_fresh_at")),
            "captured_at": request.captured_at or datetime.now(timezone.utc),
            "source_metadata": comparison["source_metadata"],
        })
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "observation": observation}


@router.get("/data-assertion-scenarios/{scenario_id}/observations/export")
def export_data_assertion_observations(scenario_id: str) -> JSONResponse:
    _require_v3()
    _validate_id(scenario_id, "data assertion scenario ID")
    if not DBRepository.get_v3_data_assertion_scenario(scenario_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data assertion scenario not found")
    return JSONResponse({"status": "ok", "observations": DBRepository.list_v3_data_assertion_observations(scenario_id)})
