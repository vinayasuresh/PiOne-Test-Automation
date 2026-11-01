"""Phase-1 contract tests for the feature-flagged V3 assurance core."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from api.models.v3_assurance_models import (
    V3AttemptRequest,
    V3EvidenceManifestRequest,
    V3IncidentIntakeRequest,
    V3JobTransitionRequest,
)
from api.routers.v3_assurance import (
    add_attempt,
    add_evidence_manifest,
    get_job,
    intake_incident,
    transition_job,
)


def _payload() -> dict:
    suffix = uuid4().hex
    return {
        "correlation_id": f"tara-{suffix}",
        "ticket_reference": f"INC-{suffix[:8]}",
        "grouping_key": "reports:staging:monthly-download",
        "summary": "Monthly report download failed for an authorized user.",
        "affected_feature": "Monthly report download",
        "reporter_role": "standard_user",
        "expected_behavior": "A valid XLSX download is returned.",
        "actual_behavior": "password=do-not-persist and the page showed an error",
        "priority": "High",
        "scenario_version": "reports-download-v1",
        "target": "https://staging.example.test/reports",
        "authorized_role": "standard_user",
        "mode": "functional_only",
        "retry_limit": 1,
        "links": {},
    }


def test_v3_assurance_api_is_hidden_until_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("V3_ASSURANCE_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        intake_incident(V3IncidentIntakeRequest(**_payload()), Response())
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "V3 assurance is disabled"


def test_v3_intake_is_idempotent_and_redacts_sensitive_content(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    payload = _payload()
    response = Response()
    created_body = intake_incident(V3IncidentIntakeRequest(**payload), response)
    assert response.status_code == 201
    assert created_body["created"] is True
    incident = created_body["incident"]
    assert len(incident["jobs"]) == 1
    assert "do-not-persist" not in incident["actual_behavior"]
    assert "[REDACTED]" in incident["actual_behavior"]

    replay_response = Response()
    replay_body = intake_incident(V3IncidentIntakeRequest(**payload), replay_response)
    assert replay_response.status_code == 200
    assert replay_body["status"] == "existing"
    assert replay_body["incident"]["incident_id"] == incident["incident_id"]
    assert len(replay_body["incident"]["jobs"]) == 1


def test_v3_job_requires_legal_state_transitions_and_tracks_attempts(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "1")
    intake = intake_incident(V3IncidentIntakeRequest(**_payload()), Response())
    job_id = intake["incident"]["jobs"][0]["job_id"]

    with pytest.raises(HTTPException) as exc_info:
        transition_job(job_id, V3JobTransitionRequest(state="completed", verdict="reproduced"))
    assert exc_info.value.status_code == 409
    assert "Illegal" in exc_info.value.detail

    queued = transition_job(job_id, V3JobTransitionRequest(state="queued"))
    assert queued["status"] == "ok"
    attempt = add_attempt(
        job_id,
        V3AttemptRequest(worker_id="worker-password=not-stored", state="running", event_sequence=[{"message": "token=not-stored"}]),
    )
    assert attempt["attempt"]["attempt_number"] == 1
    assert "not-stored" not in str(attempt)

    running = transition_job(job_id, V3JobTransitionRequest(state="running"))
    assert running["status"] == "ok"
    completed = transition_job(job_id, V3JobTransitionRequest(state="completed", verdict="not_reproduced"))
    assert completed["job"]["verdict"] == "not_reproduced"


def test_v3_evidence_manifest_is_linked_to_its_job(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "yes")
    intake = intake_incident(V3IncidentIntakeRequest(**_payload()), Response())
    job_id = intake["incident"]["jobs"][0]["job_id"]
    evidence = add_evidence_manifest(
        job_id,
        V3EvidenceManifestRequest(
            source_step="TC_001_S03",
            source_route="/reports",
            checksum="sha256:fixture",
            content_type="image/png",
            storage_location="reports/evidence/screenshot.png?token=not-stored",
        ),
    )
    assert "not-stored" not in evidence["evidence_manifest"]["storage_location"]
    job = get_job(job_id)
    assert job["status"] == "ok"
    assert len(job["job"]["evidence_manifests"]) == 1
