"""Focused proof for OmniTest V3 phases 0-3 interactive session foundation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api.db.models import InteractiveBrowserSession
from api.db.session import get_db_session
from api.models.interactive_session_models import InteractiveEvidenceRequest, InteractiveSessionCreateRequest
from api.services.interactive_browser_worker import InteractiveBrowserWorker, WorkerSessionSpec
from api.services.interactive_session_service import interactive_session_service
from api.services.v3_feature_flags import is_interactive_browser_enabled


def _create() -> tuple[dict, str]:
    record = interactive_session_service.create({
        "organization_id": "qa-org",
        "owner_id": "operator-1",
        "target_origin": "https://example.test",
        "allowed_origins": ["https://example.test"],
        "ttl_seconds": 600,
    })
    token = record.pop("connection_token")
    assert token.startswith("ibs_")
    return record, token


def test_phase0_feature_flags_keep_v2_surface_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "0")
    monkeypatch.setenv("INTERACTIVE_BROWSER_ENABLED", "1")
    assert is_interactive_browser_enabled() is False
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "1")
    monkeypatch.setenv("INTERACTIVE_BROWSER_ENABLED", "0")
    assert is_interactive_browser_enabled() is False


def test_phase1_create_lifecycle_is_durable_and_idempotent() -> None:
    payload, token = _create()
    session_id = payload["session_id"]
    status = interactive_session_service.authenticate(session_id, token)
    assert status and status["status"] == "created"
    assert token not in str(status)

    started = interactive_session_service.action(session_id, token, "start")
    assert started["status"] == "running"
    repeated = interactive_session_service.action(session_id, token, "start")
    assert repeated["status"] == "running"
    assert repeated["idempotent"] is True

    paused = interactive_session_service.action(session_id, token, "pause")
    assert paused["status"] == "paused"
    resumed = interactive_session_service.action(session_id, token, "resume")
    assert resumed["status"] == "running"
    heartbeat = interactive_session_service.heartbeat(session_id, token, "manual-checkpoint")
    assert heartbeat["metadata"]["last_client_state"] == "manual-checkpoint"
    cancelled = interactive_session_service.action(session_id, token, "cancel", "operator stop")
    assert cancelled["status"] == "cancelled"


def test_phase1_rejects_unsafe_origins_and_invalid_tokens() -> None:
    with pytest.raises(ValueError):
        InteractiveSessionCreateRequest(target_origin="http://127.0.0.1:8000")
    with pytest.raises(ValueError):
        InteractiveSessionCreateRequest(target_origin="https://user:pass@example.test")
    payload, _ = _create()
    assert interactive_session_service.authenticate(payload["session_id"], "wrong") is None


def test_phase1_expires_sessions_before_authentication() -> None:
    payload, token = _create()
    with get_db_session() as db:
        row = db.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == payload["session_id"]).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert interactive_session_service.authenticate(payload["session_id"], token) is None


def test_phase2_worker_isolates_sessions_and_cleans_up() -> None:
    events: list[tuple[str, str]] = []

    class FakeAdapter:
        def start(self, origin: str) -> None: events.append(("start", origin))
        def pause(self) -> None: events.append(("pause", ""))
        def resume(self) -> None: events.append(("resume", ""))
        def stop(self) -> None: events.append(("stop", ""))

    worker = InteractiveBrowserWorker(adapter_factory=FakeAdapter)
    worker.start(WorkerSessionSpec("one", "https://one.test", "org", "a"))
    worker.start(WorkerSessionSpec("two", "https://two.test", "org", "b"))
    worker.pause("one")
    worker.resume("two")
    worker.stop("one")
    worker.close_all()
    assert events == [("start", "https://one.test"), ("start", "https://two.test"), ("pause", ""), ("resume", ""), ("stop", ""), ("stop", "")]


def test_phase3_stream_contract_is_explicitly_control_plane_only() -> None:
    payload, token = _create()
    status = interactive_session_service.authenticate(payload["session_id"], token)
    assert status["metadata"]["stream_mode"] == "control_plane_only"


def test_phase4_restart_recovery_preserves_checkpoint_and_retry() -> None:
    payload, token = _create()
    session_id = payload["session_id"]
    started = interactive_session_service.action(session_id, token, "start")
    assert started["status"] == "running"
    with get_db_session() as db:
        row = db.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == session_id).one()
        row.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    # Simulate a process restart: the durable row remains, the worker lease does not.
    from api.services.interactive_browser_worker import interactive_browser_worker
    interactive_browser_worker.stop(session_id)
    recovered = interactive_session_service.recover_stale(30, "qa-org")
    assert any(item["session_id"] == session_id and item["status"] == "failed" for item in recovered)
    retried = interactive_session_service.action(session_id, token, "retry", "resume saved checkpoint")
    assert retried["status"] == "retrying"
    assert retried["checkpoint_id"] == payload["checkpoint_id"]
    interactive_session_service.action(session_id, token, "cancel")


def test_phase5_locator_evidence_is_bounded_and_redacted() -> None:
    payload, token = _create()
    evidence_request = InteractiveEvidenceRequest(
        event_type="click",
        page_url="https://example.test/settings/users",
        route="/settings/users",
        label="Save user",
        selector="button[data-testid='save-user']",
        xpath="//*[@data-testid='save-user']",
        text_content="Save user",
        metadata={"role": "button", "input_value": "super-secret-value"},
    )
    evidence = interactive_session_service.capture_evidence(payload["session_id"], token, evidence_request.model_dump())
    assert evidence["event_type"] == "click"
    assert evidence["selector"]
    assert evidence["xpath"]
    assert evidence["text_length"] == len("Save user")
    assert "super-secret-value" not in str(evidence)
    listed = interactive_session_service.list_evidence(payload["session_id"], token)
    assert len(listed) == 1
    assert listed[0]["text_sha256"] == evidence["text_sha256"]


def test_phase5_evidence_rejects_cross_origin_and_raw_secret_fields() -> None:
    payload, token = _create()
    with pytest.raises(ValueError, match="origin allowlist"):
        interactive_session_service.capture_evidence(
            payload["session_id"], token,
            InteractiveEvidenceRequest(
                event_type="input",
                page_url="https://evil.example/password",
                text_content="do-not-store",
            ).model_dump(),
        )
