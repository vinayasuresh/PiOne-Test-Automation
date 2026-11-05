"""Release-gate checks for the pre-Tara interactive assurance foundation.

These checks intentionally exercise the service boundary without pretending
that a local rendered browser or remote media transport exists on every host.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api.db.models import InteractiveBrowserSession
from api.db.session import get_db_session
from api.models.interactive_session_models import InteractiveEvidenceRequest
from api.services.interactive_browser_worker import interactive_browser_worker
from api.services.interactive_session_service import interactive_session_service
from api.services.v3_feature_flags import is_interactive_browser_enabled, is_v3_assurance_enabled


def _session() -> tuple[dict, str]:
    record = interactive_session_service.create({
        "organization_id": "release-gate-org",
        "owner_id": "release-gate-operator",
        "target_origin": "https://release-gate.example",
        "allowed_origins": ["https://release-gate.example"],
        "checkpoint_id": "checkpoint-release-1",
        "ttl_seconds": 600,
    })
    token = record.pop("connection_token")
    return record, token


def test_release_gate_v3_is_explicitly_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V3_ASSURANCE_ENABLED", raising=False)
    monkeypatch.delenv("INTERACTIVE_BROWSER_ENABLED", raising=False)
    assert is_v3_assurance_enabled() is False
    assert is_interactive_browser_enabled() is False
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("INTERACTIVE_BROWSER_ENABLED", "true")
    assert is_interactive_browser_enabled() is True


def test_release_gate_evidence_lineage_has_no_raw_input() -> None:
    record, token = _session()
    interactive_session_service.action(record["session_id"], token, "start")
    evidence = interactive_session_service.capture_evidence(
        record["session_id"], token,
        InteractiveEvidenceRequest(
            event_type="input",
            page_url="https://release-gate.example/account",
            route="/account",
            label="Email",
            selector="input[name='email']",
            xpath="//input[@name='email']",
            text_content="customer@example.invalid",
        ).model_dump(),
    )
    assert evidence["session_id"] == record["session_id"]
    assert evidence["text_sha256"]
    assert "customer@example.invalid" not in str(evidence)
    interactive_session_service.action(record["session_id"], token, "cancel")


def test_release_gate_recovery_is_truthful_and_retryable() -> None:
    record, token = _session()
    sid = record["session_id"]
    interactive_session_service.action(sid, token, "start")
    with get_db_session() as db:
        row = db.query(InteractiveBrowserSession).filter(InteractiveBrowserSession.id == sid).one()
        row.last_activity_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    interactive_browser_worker.stop(sid)
    recovered = interactive_session_service.recover_stale(60, "release-gate-org")
    assert recovered and recovered[0]["status"] == "failed"
    retry = interactive_session_service.action(sid, token, "retry", "worker restart")
    assert retry["status"] == "retrying"
    assert retry["metadata"]["recovery_reason"] == "worker_lease_expired"
    interactive_session_service.action(sid, token, "cancel")


def test_release_gate_cross_origin_evidence_is_blocked() -> None:
    record, token = _session()
    with pytest.raises(ValueError):
        interactive_session_service.capture_evidence(
            record["session_id"], token,
            InteractiveEvidenceRequest(
                event_type="click",
                page_url="https://unapproved.example/",
                selector="button",
            ).model_dump(),
        )


def test_release_gate_terminal_recovery_does_not_offer_retry() -> None:
    record, token = _session()
    sid = record["session_id"]
    interactive_session_service.action(sid, token, "start")
    interactive_session_service.action(sid, token, "complete", "verified")
    terminal = interactive_session_service.recover_session(sid, token)
    assert terminal["status"] == "completed"
