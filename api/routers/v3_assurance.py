"""Additive V3 assurance API.

The router only exposes durable metadata; it never starts a browser crawl or
changes the V2 scan lifecycle.  Every endpoint is hidden until the explicit
V3 feature flag is enabled.
"""

from __future__ import annotations

import re
import os
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
    ApiContractIngestRequest,
    AuthFixtureRegistrationRequest,
    AuthConnectionProbeRequest,
    ApiTestPlanExecutionRequest,
    DatabaseCrossValidationRequest,
)
from api.services.v3_api_contract_service import (
    normalize_contract,
    validate_registered_operation,
    detect_and_parse_contract,
    fetch_remote_contract,
    execute_api_test_plan,
    get_api_plan_progress,
    restore_api_plan,
)
from api.services.v3_secret_vault import register_secret_fixture, probe_api_connection
from api.services.v3_api_report_service import generate_v3_api_excel_report
from api.services.v3_database_cross_validation_service import execute_database_cross_read
from api.models.v3_data_assertion_models import DataAssertionEvaluationRequest, DataAssertionScenarioRequest
from api.models.v3_database_models import DatabaseQueryRegistrationRequest, DatabaseReadRequest
from api.services.v3_database_assertion_service import ApprovedDatabaseQuery, compare_api_and_database, read_approved_database
from api.db.session import get_engine
from api.services.v3_data_assertion_service import capture_dashboard_ui_value, compare_dashboard_data, parse_timestamp, read_approved_source
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
        data_scenario = None
        if scenario["scenario_type"] == "api_contract":
            registration = DBRepository.get_v3_api_operation(scenario["reference_id"])
            outcome = execute_approved_scenario(scenario, registration)
        elif scenario["scenario_type"] == "data_assertion":
            data_scenario = DBRepository.get_v3_data_assertion_scenario(scenario["reference_id"])
            registration = DBRepository.get_v3_api_operation(data_scenario["source_operation_id"]) if data_scenario else None
            outcome = execute_approved_scenario(scenario, registration, data_assertion_scenario=data_scenario)
        else:
            outcome = {"verdict": "blocked", "category": "unsupported_scenario_type", "events": [{"event": "conclude", "detail": "blocked"}]}

        terminal_state = "completed" if outcome["verdict"] in {"reproduced", "not_reproduced"} else outcome["verdict"]
        attempt = DBRepository.add_v3_attempt(job_id, request.worker_id, terminal_state, outcome["verdict"], outcome["category"], outcome["events"])
        evidence = DBRepository.add_v3_evidence_manifest(job_id, {
            "source_step": "V3 bounded verification", "source_route": scenario["target_origin"],
            "content_type": "application/json",
            "storage_location": f"v3://verification-jobs/{job_id}/attempts/{attempt['attempt_id']}",
        })
        manifest_id = evidence.get("manifest_id") or evidence.get("id")
        if outcome.get("screenshot_storage_location"):
            DBRepository.add_v3_evidence_manifest(job_id, {
                "source_step": f"Dashboard UI Capture: {scenario.get('name', 'KPI')}",
                "source_route": data_scenario["route"] if data_scenario else scenario["target_origin"],
                "content_type": "image/png",
                "checksum": outcome.get("screenshot_checksum", ""),
                "storage_location": outcome["screenshot_storage_location"],
            })
        if scenario["scenario_type"] == "data_assertion" and data_scenario and outcome.get("result"):
            comp = outcome["result"]
            try:
                DBRepository.add_v3_data_assertion_observation({
                    "scenario_id": data_scenario["data_assertion_scenario_id"],
                    "job_id": job_id,
                    "evidence_manifest_id": manifest_id,
                    "scenario_version": job.get("scenario_version", "v3"),
                    "result": comp.get("result", outcome["verdict"]),
                    "classification": comp.get("classification", outcome["category"]),
                    "ui_raw_value": str(comp.get("ui_raw_value", "")),
                    "source_raw_value": str(comp.get("source_raw_value", "")),
                    "normalized_ui_value": str(comp["normalized_ui_value"]) if comp.get("normalized_ui_value") is not None else None,
                    "normalized_source_value": str(comp["normalized_source_value"]) if comp.get("normalized_source_value") is not None else None,
                    "delta": str(comp["delta"]) if comp.get("delta") is not None else None,
                    "filter_context": comp.get("filter_context", {}),
                    "source_filter_context": comp.get("source_filter_context", {}),
                    "source_fresh_at": parse_timestamp(comp.get("source_fresh_at")),
                    "captured_at": parse_timestamp(outcome.get("ui_capture", {}).get("captured_at")) or datetime.now(timezone.utc),
                    "source_metadata": comp.get("source_metadata", {}),
                })
            except Exception:
                pass
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
        parameter_values=request.parameter_values,
    )
    evidence_id = request.evidence_manifest_id
    if request.job_id and not evidence_id:
        manifest = DBRepository.add_v3_evidence_manifest(request.job_id, {
            "source_step": f"API Contract: {registration['operation']['operation_key']}",
            "source_route": registration["operation"]["path"],
            "content_type": "application/json",
            "storage_location": f"v3://contracts/{registration['contract'].get('contract_id') or registration['contract'].get('id')}/operations/{operation_id}",
        })
        evidence_id = manifest["id"]
    try:
        observation = DBRepository.add_v3_api_observation({
            "operation_id": operation_id,
            "job_id": request.job_id,
            "run_id": request.run_id,
            "evidence_manifest_id": evidence_id,
            "scenario_version": request.scenario_version,
            "result": observation_result["result"],
            "classification": observation_result["classification"],
            "status_code": observation_result["status_code"],
            "latency_ms": observation_result["latency_ms"],
            "request_metadata": observation_result["request_metadata"],
            "assertion_result": observation_result["assertion_result"],
            "evidence_link": f"v3://contracts/{registration['contract'].get('contract_id') or registration['contract'].get('id')}/observations/{operation_id}",
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


@router.post("/database-queries", status_code=status.HTTP_201_CREATED)
def register_database_query(request: DatabaseQueryRegistrationRequest) -> dict:
    """Register one explicitly approved, read-only test/staging query."""
    _require_v3()
    if request.environment == "production":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Production database reads are not allowed")
    try:
        query = DBRepository.register_v3_database_query(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "created", "query": query}


@router.get("/database-queries")
def list_database_queries() -> dict:
    _require_v3()
    return {"status": "ok", "queries": DBRepository.list_v3_database_queries()}


@router.get("/database-queries/{query_id}")
def get_database_query(query_id: str) -> dict:
    _require_v3(); _validate_id(query_id, "database query ID")
    query = DBRepository.get_v3_database_query(query_id)
    if not query:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database query not found")
    return {"status": "ok", "query": query}


@router.post("/database-queries/{query_id}/read")
def read_database_query(query_id: str, request: DatabaseReadRequest) -> dict:
    """Execute a registered query against the configured test/staging database."""
    _require_v3(); _validate_id(query_id, "database query ID")
    definition = DBRepository.get_v3_database_query_definition(query_id)
    if not definition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database query not found")
    if not definition["is_approved"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Database query is not approved")
    approved = ApprovedDatabaseQuery(
        query_id=definition["query_id"], sql=definition["sql"],
        parameter_names=tuple(definition.get("parameter_names", [])),
        database_ref=definition["database_ref"], environment=definition["environment"],
    )
    reading = read_approved_database(get_engine(), approved, request.parameters)
    response: dict = {"status": "ok", "reading": reading}
    if request.api_value is not None or request.api_result != "ok":
        database_value = None
        rows = reading.get("rows") or []
        if rows:
            first_row = rows[0]
            if len(first_row) == 1:
                database_value = next(iter(first_row.values()))
            else:
                database_value = first_row
        response["comparison"] = compare_api_and_database(
            request.api_value, database_value,
            tolerance=request.tolerance, api_result=request.api_result, database_result=reading.get("result", "blocked"),
        )
    return response


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


@router.get("/data-assertion-scenarios")
def list_data_assertion_scenarios() -> dict:
    _require_v3()
    return {"status": "ok", "scenarios": DBRepository.list_v3_data_assertion_scenarios()}


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
    """Compare UI to API and, when bound, API to an approved DB read."""
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
    eval_dict = request.model_dump()
    screenshot_loc = None
    screenshot_sum = ""
    if not eval_dict.get("ui_value") and (eval_dict.get("auto_capture") or not eval_dict.get("ui_value")):
        base_url = source_registration["contract"]["base_url"]
        ui_cap = capture_dashboard_ui_value(
            base_url,
            scenario["route"],
            scenario["ui_selector"],
            filter_context=scenario.get("expected_filters"),
            value_format=scenario.get("value_format", "number"),
        )
        if ui_cap.get("result") == "ok":
            eval_dict["ui_value"] = ui_cap.get("ui_value", "")
            eval_dict["filter_context"] = ui_cap.get("filter_context", {})
            eval_dict["captured_at"] = parse_timestamp(ui_cap.get("captured_at"))
            screenshot_loc = ui_cap.get("screenshot_storage_location")
            screenshot_sum = ui_cap.get("screenshot_checksum", "")
        else:
            eval_dict["ui_value"] = ""
            eval_dict["classification"] = ui_cap.get("classification", "ui_capture_failed")

    comparison = compare_dashboard_data(scenario, eval_dict, source_reading)
    database_comparison = None
    database_query_id = scenario.get("database_query_id")
    if database_query_id:
        definition = DBRepository.get_v3_database_query_definition(database_query_id)
        if not definition or not definition["is_approved"]:
            database_comparison = {
                "result": "blocked",
                "classification": "database_query_not_approved",
            }
        else:
            approved = ApprovedDatabaseQuery(
                query_id=definition["query_id"], sql=definition["sql"],
                parameter_names=tuple(definition.get("parameter_names", [])),
                database_ref=definition["database_ref"], environment=definition["environment"],
            )
            database_reading = read_approved_database(
                get_engine(), approved, request.database_parameters,
            )
            database_value = None
            rows = database_reading.get("rows") or []
            if not rows and database_reading.get("result") == "ok":
                database_reading = {
                    **database_reading,
                    "result": "blocked",
                    "classification": "database_result_empty",
                }
            elif rows:
                first_row = rows[0]
                if len(first_row) == 1:
                    database_value = next(iter(first_row.values()))
                else:
                    database_reading = {
                        **database_reading,
                        "result": "blocked",
                        "classification": "database_result_ambiguous",
                    }
            database_comparison = compare_api_and_database(
                source_reading.get("source_value"), database_value,
                tolerance=scenario.get("tolerance", "0"),
                api_result=source_reading.get("result", "blocked"),
                database_result=database_reading.get("result", "blocked"),
            )
        if database_comparison["result"] != "pass":
            comparison = {
                **comparison,
                "result": "blocked" if database_comparison["result"] == "blocked" else "fail",
                "classification": database_comparison["classification"],
            }
        comparison["database_query_id"] = database_query_id
        comparison["database_comparison"] = database_comparison
        comparison["source_metadata"] = {
            **(comparison.get("source_metadata") or {}),
            "database_query_id": database_query_id,
            "database_comparison": database_comparison,
        }
    evidence_id = request.evidence_manifest_id
    if request.job_id and not evidence_id and screenshot_loc:
        manifest = DBRepository.add_v3_evidence_manifest(request.job_id, {
            "source_step": f"Dashboard Capture: {scenario.get('name')}",
            "source_route": scenario["route"],
            "content_type": "image/png",
            "checksum": screenshot_sum,
            "storage_location": screenshot_loc,
        })
        evidence_id = manifest.get("manifest_id") or manifest.get("id")
    try:
        observation = DBRepository.add_v3_data_assertion_observation({
            "scenario_id": scenario_id,
            "job_id": request.job_id,
            "run_id": request.run_id,
            "evidence_manifest_id": evidence_id,
            "scenario_version": request.scenario_version,
            "result": comparison["result"],
            "classification": comparison["classification"],
            "ui_raw_value": str(comparison.get("ui_raw_value", "")),
            "source_raw_value": str(comparison.get("source_raw_value", "")),
            "normalized_ui_value": str(comparison["normalized_ui_value"]) if comparison.get("normalized_ui_value") is not None else None,
            "normalized_source_value": str(comparison["normalized_source_value"]) if comparison.get("normalized_source_value") is not None else None,
            "delta": str(comparison["delta"]) if comparison.get("delta") is not None else None,
            "filter_context": comparison["filter_context"],
            "source_filter_context": comparison["source_filter_context"],
            "source_fresh_at": parse_timestamp(comparison.get("source_fresh_at")),
            "captured_at": eval_dict.get("captured_at") or datetime.now(timezone.utc),
            "source_metadata": comparison["source_metadata"],
        })
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "created", "observation": observation, "comparison": comparison}


@router.get("/data-assertion-scenarios/{scenario_id}/observations/export")
def export_data_assertion_observations(scenario_id: str) -> JSONResponse:
    _require_v3()
    _validate_id(scenario_id, "data assertion scenario ID")
    if not DBRepository.get_v3_data_assertion_scenario(scenario_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data assertion scenario not found")
    return JSONResponse({"status": "ok", "observations": DBRepository.list_v3_data_assertion_observations(scenario_id)})


# ---------------------------------------------------------------------------
# V3 API Testing Workflow & Database Cross-Read Validation Endpoints
# ---------------------------------------------------------------------------

@router.post("/api-contracts/ingest", status_code=status.HTTP_201_CREATED)
def ingest_api_contract(request: ApiContractIngestRequest) -> dict:
    """Ingest OpenAPI, Swagger, or Postman contracts from URL, file, or paste."""
    _require_v3()
    try:
        if request.input_mode == "url":
            if not request.url:
                raise ValueError("URL must be provided when input_mode is 'url'")
            is_mock = request.url.endswith((".test", ".example", ".invalid", ":8000", ":8001"))
            detected_type, parsed_doc = fetch_remote_contract(request.url, is_mock=is_mock)
        elif request.input_mode in {"file", "paste"}:
            if not request.content:
                raise ValueError("Content must be provided when input_mode is 'file' or 'paste'")
            detected_type, parsed_doc = detect_and_parse_contract(
                request.content,
                source_type_hint=request.source_type if request.source_type != "auto" else None,
            )
        else:
            raise ValueError(f"Unsupported input mode: {request.input_mode}")

        source_type = detected_type if detected_type in {"openapi", "swagger", "postman"} else "openapi"
        operations = normalize_contract(source_type, parsed_doc, request.base_url)

        contract = DBRepository.register_v3_api_contract(
            {
                "name": request.name,
                "version": request.version,
                "source_type": source_type,
                "base_url": request.base_url,
                "environment": request.environment,
                "approved": request.approved,
            },
            operations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "status": "created",
        "detected_type": detected_type,
        "operations_count": len(operations),
        "contract": contract,
        "operations": operations,
    }


@router.post("/auth/register-fixture", status_code=status.HTTP_201_CREATED)
def register_auth_fixture(request: AuthFixtureRegistrationRequest) -> dict:
    """Register credentials in ephemeral server vault and return safe fixture reference."""
    _require_v3()
    try:
        fixture_meta = register_secret_fixture(
            request.auth_type,
            token=request.token,
            api_key_name=request.api_key_name,
            api_key_location=request.api_key_location,
            api_key_value=request.api_key_value,
            username=request.username,
            password=request.password,
            client_id=request.client_id,
            client_secret=request.client_secret,
            token_url=request.token_url,
            scope=request.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"status": "created", "fixture": fixture_meta}


@router.post("/auth/test-connection")
def check_auth_connection(request: AuthConnectionProbeRequest) -> dict:
    """Safe connection probe action without echoing credentials."""
    _require_v3()
    is_mock = request.base_url.endswith((".test", ".example", ".invalid", ":8000", ":8001"))
    result = probe_api_connection(
        request.base_url,
        fixture_ref=request.auth_fixture_ref,
        probe_path=request.probe_path,
        probe_method=request.probe_method,
        is_mock=is_mock,
    )
    return {"status": "ok", "probe": result}


@router.post("/api-contracts/execute-plan", status_code=status.HTTP_201_CREATED)
def run_api_contract_plan(request: ApiTestPlanExecutionRequest) -> dict:
    """Execute an API test plan with deduplication locking, per-operation checkpoints, and resume."""
    _require_v3()
    _validate_id(request.contract_id, "contract ID")
    contract = DBRepository.get_v3_api_contract(request.contract_id)
    if not contract:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API contract not found")

    all_operations = contract.get("operations") or []
    if request.operation_keys:
        op_keys_set = set(request.operation_keys)
        target_operations = [op for op in all_operations if op["operation_key"] in op_keys_set]
    else:
        target_operations = all_operations

    if not target_operations:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No operations selected for execution")

    # Resumes must address the original plan.  Generating a fresh id for every
    # request silently discarded checkpoints and made the UI's Resume action
    # look successful while restarting from zero.
    import uuid
    plan_id = request.plan_id if request.resume_checkpoint and request.plan_id else f"plan_{uuid.uuid4().hex[:12]}"
    if request.resume_checkpoint and not request.plan_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="plan_id is required when resuming an API plan")

    if request.resume_checkpoint:
        persisted_plan = DBRepository.get_v3_api_plan(request.plan_id)
        if persisted_plan:
            if persisted_plan.get("contract_id") != request.contract_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan belongs to a different API contract")
            restore_api_plan(persisted_plan)

    if request.allow_mutation:
        # A client-side checkbox is not authorization.  Mutations are only
        # allowed for explicitly approved test/staging runs with a server-side
        # feature flag and an unambiguous confirmation phrase.
        if request.mutation_confirmation != "I_CONFIRM_STAGING_MUTATIONS":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Mutation execution requires explicit staging confirmation")
        if os.getenv("V3_ALLOW_API_MUTATIONS", "").strip().lower() not in {"1", "true", "yes"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API mutations are disabled by server policy")
        if contract.get("environment") not in {"staging", "test"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API mutations are not allowed for this environment")

    try:
        plan_result = execute_api_test_plan(
            plan_id,
            contract,
            target_operations,
            auth_fixture_ref=request.auth_fixture_ref,
            parameter_overrides=request.parameter_overrides,
            allow_mutation=request.allow_mutation,
            concurrency=request.concurrency,
            timeout_seconds=request.timeout_seconds,
            retry_limit=request.retry_limit,
            resume_checkpoint=request.resume_checkpoint,
            checkpoint_callback=DBRepository.save_v3_api_plan,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"status": "completed", "plan": plan_result}


@router.get("/api-contracts/plans/{plan_id}/progress")
def get_plan_progress(plan_id: str) -> dict:
    """Retrieve execution progress and per-operation checkpoints."""
    _require_v3()
    _validate_id(plan_id, "plan ID")
    plan = get_api_plan_progress(plan_id)
    if not plan:
        plan = DBRepository.get_v3_api_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return {"status": "ok", "plan": plan}


@router.get("/api-contracts/plans/{plan_id}/report.xlsx")
def download_api_excel_report(plan_id: str) -> Response:
    """Stream the 10-sheet functional Excel report with zero secret leakage."""
    _require_v3()
    _validate_id(plan_id, "plan ID")
    plan = get_api_plan_progress(plan_id)
    if not plan:
        plan = DBRepository.get_v3_api_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    contract = DBRepository.get_v3_api_contract(plan["contract_id"])
    if not contract:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Associated contract not found")
    operations = contract.get("operations") or []

    excel_bytes = generate_v3_api_excel_report(plan, contract, operations)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="omnitest_api_report_{plan_id}.xlsx"'},
    )


@router.post("/database-cross-validation/validate")
def validate_database_cross_read(request: DatabaseCrossValidationRequest) -> dict:
    """Execute server-side read-only DB query and run 3-way DB/API/UI cross validation."""
    _require_v3()
    _validate_id(request.query_id, "database query ID")
    definition = DBRepository.get_v3_database_query_definition(request.query_id)
    if not definition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database query not found")
    if not definition["is_approved"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Database query is not approved")

    approved = ApprovedDatabaseQuery(
        query_id=definition["query_id"],
        sql=definition["sql"],
        parameter_names=tuple(definition.get("parameter_names", [])),
        database_ref=definition["database_ref"],
        environment=definition["environment"],
    )

    result = execute_database_cross_read(
        get_engine(),
        approved,
        parameters=request.parameters,
        api_value=request.api_value,
        dashboard_ui_value=request.dashboard_ui_value,
        tolerance=request.tolerance,
        context_metadata=request.context_metadata,
    )
    return {"status": "ok", "cross_validation": result}
