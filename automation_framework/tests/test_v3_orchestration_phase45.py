"""Phase 4/5 tests: bounded orchestration and helpdesk records stay additive."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from api.db.repository import DBRepository
from api.models.v3_assurance_models import V3IncidentIntakeRequest
from api.models.v3_orchestration_models import (
    AssignmentRuleRequest,
    AssuranceScenarioRequest,
    ManualMonitoringTriggerRequest,
    MonitoringScheduleRequest,
    V3RetryRequest,
)
from api.routers.v3_assurance import (
    create_assignment_rule, create_monitoring_schedule, intake_incident,
    orchestrate_job, register_assurance_scenario, retry_orchestration_job,
)
import api.routers.v3_assurance as assurance_router
from api.models.v3_orchestration_models import V3OrchestrateRequest
from api.services.v3_orchestration_service import execute_approved_scenario, plan_assurance_job
import api.services.v3_orchestration_service as orchestration_service


def _operation() -> dict:
    return {
        "operation_key": f"health-{uuid4().hex}", "method": "GET", "path": "/health",
        "expected_status_codes": [200], "required_fields": [], "max_latency_ms": 3000,
        "auth_requirement": "none", "auth_fixture_ref": "", "approved_role": "analyst",
        "allow_unauthenticated_probe": False, "safe_read_only": True, "is_enabled": True,
    }


def _registered_operation() -> dict:
    contract = DBRepository.register_v3_api_contract({
        "name": f"fixture-{uuid4().hex}", "version": "1", "source_type": "openapi",
        "base_url": "https://example.com", "environment": "staging", "approved": True,
    }, [_operation()])
    return contract["operations"][0]


def _intake(retry_limit: int = 0) -> dict:
    token = uuid4().hex
    return intake_incident(V3IncidentIntakeRequest(
        correlation_id=f"v3-phase45-{token}", grouping_key="reports:staging:daily",
        summary="The daily report did not load.", affected_feature="Reports",
        expected_behavior="The report opens.", actual_behavior="The report returned an error.",
        target="https://example.com/reports", authorized_role="analyst", mode="functional_only", retry_limit=retry_limit,
    ), Response())


def test_approved_scenario_requires_registered_read_only_source(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    operation = _registered_operation()
    scenario = register_assurance_scenario(AssuranceScenarioRequest(
        name="Reports health", feature="Reports", environment="staging", authorized_role="analyst",
        target_origin="https://example.com", scenario_type="api_contract",
        reference_id=operation["operation_id"], is_approved=True,
    ))["scenario"]
    assert scenario["is_approved"] is True
    assert scenario["max_actions"] == 3


def test_orchestration_policy_blocks_role_or_origin_escape(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setattr(orchestration_service, "validate_target_url", lambda target: target)
    operation = _registered_operation()
    scenario = DBRepository.register_v3_assurance_scenario({
        "name": "Bounded API check", "feature": "Reports", "environment": "staging",
        "authorized_role": "analyst", "target_origin": "https://example.com",
        "scenario_type": "api_contract", "reference_id": operation["operation_id"],
        "max_actions": 1, "is_approved": True,
    })
    job = _intake()["incident"]["jobs"][0]
    blocked = plan_assurance_job(job, {"incident_id": job["incident_id"]}, scenario)
    assert blocked["ready"] is True
    job["authorized_role"] = "admin"
    assert plan_assurance_job(job, {}, scenario)["category"] == "role_scope_mismatch"
    job["authorized_role"] = "analyst"; job["target"] = "https://www.example.org/reports"
    assert plan_assurance_job(job, {}, scenario)["category"] == "target_origin_mismatch"


def test_data_assertion_executor_is_honest_about_missing_ui_capture() -> None:
    outcome = execute_approved_scenario({"scenario_type": "data_assertion"}, None)
    assert outcome["verdict"] == "inconclusive"
    assert outcome["category"] == "ui_capture_required"


def test_orchestrator_records_a_bounded_terminal_attempt(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setattr(orchestration_service, "validate_target_url", lambda target: target)
    monkeypatch.setattr(assurance_router, "plan_assurance_job", lambda *_: {"ready": True, "verdict": "pending", "category": "", "decision": {"events": []}})
    monkeypatch.setattr(assurance_router, "execute_approved_scenario", lambda *_: {"verdict": "not_reproduced", "category": "contract_satisfied", "events": [{"event": "conclude", "detail": "not_reproduced"}]})
    operation = _registered_operation()
    scenario = DBRepository.register_v3_assurance_scenario({
        "name": "Deterministic bounded flow", "feature": "Reports", "environment": "staging",
        "authorized_role": "analyst", "target_origin": "https://example.com", "scenario_type": "api_contract",
        "reference_id": operation["operation_id"], "max_actions": 1, "is_approved": True,
    })
    job_id = _intake()["incident"]["jobs"][0]["job_id"]
    result = orchestrate_job(job_id, V3OrchestrateRequest(scenario_id=scenario["scenario_id"]))
    assert result["job"]["state"] == "completed"
    assert result["job"]["verdict"] == "not_reproduced"
    assert result["attempt"]["attempt_number"] == 1
    assert result["evidence_manifest"]["storage_location"].startswith("v3://verification-jobs/")


def test_bounded_retry_preserves_first_attempt_and_uses_same_scenario(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setattr(orchestration_service, "validate_target_url", lambda target: target)
    monkeypatch.setattr(assurance_router, "plan_assurance_job", lambda *_: {"ready": True, "verdict": "pending", "category": "", "decision": {"events": []}})
    outcomes = iter([
        {"verdict": "blocked", "category": "target_timeout", "events": [{"event": "conclude", "detail": "blocked"}]},
        {"verdict": "not_reproduced", "category": "contract_satisfied", "events": [{"event": "conclude", "detail": "not_reproduced"}]},
    ])
    monkeypatch.setattr(assurance_router, "execute_approved_scenario", lambda *_: next(outcomes))
    operation = _registered_operation()
    scenario = DBRepository.register_v3_assurance_scenario({
        "name": "Retryable bounded flow", "feature": "Reports", "environment": "staging",
        "authorized_role": "analyst", "target_origin": "https://example.com", "scenario_type": "api_contract",
        "reference_id": operation["operation_id"], "max_actions": 1, "is_approved": True,
    })
    job_id = _intake(retry_limit=1)["incident"]["jobs"][0]["job_id"]
    first = orchestrate_job(job_id, V3OrchestrateRequest(scenario_id=scenario["scenario_id"]))
    assert first["job"]["state"] == "blocked"
    second = retry_orchestration_job(job_id, V3RetryRequest())
    assert second["job"]["state"] == "completed"
    assert second["attempt"]["attempt_number"] == 2
    restored = DBRepository.get_v3_job(job_id)
    incident = DBRepository.get_v3_incident(restored["incident_id"])
    assert len(restored["attempts"]) == 2
    assert len(restored["evidence_manifests"]) == 2
    assert incident["status"] == "resolved"
    assert incident["last_outcome"] == "not_reproduced"


def test_duplicate_intake_reuses_one_incident_and_one_job(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    token = uuid4().hex
    request = V3IncidentIntakeRequest(
        correlation_id=f"v3-phase6-{token}", grouping_key="reports:staging:phase6",
        summary="The release report did not load.", affected_feature="Reports",
        expected_behavior="The report opens.", actual_behavior="The report returned an error.",
        target="https://example.com/reports", authorized_role="analyst", retry_limit=1,
    )
    first_response = Response()
    second_response = Response()
    first = intake_incident(request, first_response)
    second = intake_incident(request, second_response)
    assert first["status"] == "created"
    assert second["status"] == "existing"
    assert first["incident"]["incident_id"] == second["incident"]["incident_id"]
    assert len(second["incident"]["jobs"]) == 1


def test_assignment_and_schedule_are_persisted_without_starting_a_scheduler(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    operation = _registered_operation()
    scenario = DBRepository.register_v3_assurance_scenario({
        "name": "Report monitor", "feature": "Reports", "environment": "staging",
        "authorized_role": "analyst", "target_origin": "https://example.com", "scenario_type": "api_contract",
        "reference_id": operation["operation_id"], "max_actions": 2, "is_approved": True,
    })
    create_assignment_rule(AssignmentRuleRequest(feature="Reports", owner_team="Reporting Engineering"))
    incident = _intake()["incident"]
    assigned = DBRepository.auto_assign_v3_incident(incident["incident_id"])
    assert assigned["owner_team"] == "Reporting Engineering"
    job_id = incident["jobs"][0]["job_id"]
    DBRepository.transition_v3_job(job_id, "queued")
    DBRepository.transition_v3_job(job_id, "running")
    DBRepository.transition_v3_job(job_id, "completed", "reproduced", "fixture_failure")
    saved = create_monitoring_schedule(MonitoringScheduleRequest(
        name="Reports monitor", scenario_id=scenario["scenario_id"], target="https://example.com/reports",
        grouping_key="reports:staging:daily", interval_minutes=30,
    ))["schedule"]
    assert saved["last_triggered_at"] is None
    assert any(item["schedule_id"] == saved["schedule_id"] for item in DBRepository.list_v3_monitoring_schedules())
    manual = DBRepository.trigger_v3_monitoring_schedule(
        saved["schedule_id"], ManualMonitoringTriggerRequest().model_dump(),
    )
    assert manual["grouped_into_existing_incident"] is True
    assert len(manual["incident"]["jobs"]) == 2
    assert manual["schedule"]["last_triggered_at"] is not None


def test_assurance_desk_is_feature_flag_hidden_in_frontend() -> None:
    source = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'id="btn-nav-assurance"' in source
    assert "version.v3_assurance_enabled === true" in source
    assert "schedule records are saved definitions only" in source
    assert "assurance-incident-detail" in source
    assert "/v3/monitoring-schedules/${encodeURIComponent(scheduleId)}/trigger" in source
    assert 'id="assurance-intake-form"' in source
    assert 'id="assurance-scenario-select"' in source
    assert 'id="btn-assurance-run"' in source
    assert 'id="btn-assurance-retry"' in source
    assert 'id="btn-assurance-assign"' in source
    assert "/v3/incidents/intake" in source
    assert 'retry ? "retry" : "orchestrate"' in source
