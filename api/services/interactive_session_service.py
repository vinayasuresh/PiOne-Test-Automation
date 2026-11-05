"""Secure lifecycle orchestration for guided browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

from api.db.repository import DBRepository
from api.services.interactive_browser_worker import WorkerSessionSpec, interactive_browser_worker
from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


_ACTIONS = {
    "start": "starting",
    "pause": "paused",
    "resume": "running",
    "continue": "running",
    "retry": "retrying",
    "complete": "completed",
    "cancel": "cancelled",
}


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("target origin must not contain a path, query, or fragment")
    return validate_target_url(value, is_mock=True).rstrip("/")


def _safe_origins(target: str, values: list[str]) -> list[str]:
    origins = list(dict.fromkeys([target, *(_safe_origin(item) for item in values)]))
    return origins


class InteractiveSessionService:
    """Creates and authenticates bounded, restart-visible session records."""

    @staticmethod
    def create(payload: dict) -> dict:
        target = _safe_origin(payload["target_origin"])
        allowed = _safe_origins(target, payload.get("allowed_origins") or [])
        raw_token = "ibs_" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        session_id = "ibs_" + secrets.token_hex(16)
        expires_at = now + timedelta(seconds=int(payload.get("ttl_seconds", 1800)))
        record = DBRepository.create_interactive_session({
            "session_id": session_id,
            "run_id": payload.get("run_id"),
            "organization_id": payload.get("organization_id", "default"),
            "owner_id": payload.get("owner_id", "operator"),
            "target_origin": target,
            "allowed_origins": allowed,
            "checkpoint_id": payload.get("checkpoint_id"),
            "module_id": payload.get("module_id"),
            "route": payload.get("route"),
            "worker_id": "local-worker",
            "expires_at": expires_at,
            "session_token_hash": _hash_token(raw_token),
            "metadata": {"auth_mode": "opaque_token", "stream_mode": "control_plane_only"},
        })
        record["stream_mode"] = "control_plane_only"
        record["connection_token"] = raw_token
        return record

    @staticmethod
    def authenticate(session_id: str, raw_token: str | None) -> dict | None:
        if not raw_token or len(raw_token) > 256:
            return None
        candidate = DBRepository.authenticate_interactive_session(session_id, _hash_token(raw_token))
        if not candidate:
            return None
        if candidate["status"] == "expired":
            return None
        candidate["stream_mode"] = "control_plane_only"
        return candidate

    @staticmethod
    def action(session_id: str, raw_token: str, action: str, reason: str = "") -> dict:
        current = InteractiveSessionService.authenticate(session_id, raw_token)
        if not current:
            raise PermissionError("Invalid or expired interactive session token")
        target_status = _ACTIONS[action]
        if action == "start" and current["status"] in {"starting", "running"}:
            current["stream_mode"] = "control_plane_only"
            current["action"] = action
            current["idempotent"] = True
            return current
        # Start is the only action that allocates a worker.  Repeated start,
        # continue, and resume calls are idempotent at the persistence layer.
        if action == "start":
            interactive_browser_worker.start(WorkerSessionSpec(
                session_id=session_id,
                target_origin=current["target_origin"],
                organization_id=current["organization_id"],
                owner_id=current["owner_id"],
            ))
        elif action in {"pause"}:
            interactive_browser_worker.pause(session_id)
        elif action in {"resume", "continue", "retry"}:
            spec = WorkerSessionSpec(
                session_id=session_id,
                target_origin=current["target_origin"],
                organization_id=current["organization_id"],
                owner_id=current["owner_id"],
            )
            if not interactive_browser_worker.is_active(session_id):
                # A restart-safe retry creates a fresh isolated worker while
                # retaining the durable checkpoint and evidence ledger.
                interactive_browser_worker.ensure_started(spec)
            else:
                interactive_browser_worker.resume(session_id)
        elif action in {"complete", "cancel"}:
            interactive_browser_worker.stop(session_id)
        try:
            result = DBRepository.transition_interactive_session(session_id, target_status, reason)
            if action == "start":
                result = DBRepository.transition_interactive_session(session_id, "running", "worker control plane ready")
        except ValueError:
            # A duplicate lifecycle request is safe and returns the current
            # durable state instead of starting a second browser operation.
            latest = DBRepository.get_interactive_session(session_id)
            if latest and latest["status"] == target_status:
                result = latest
            else:
                raise
        result["stream_mode"] = "control_plane_only"
        result["action"] = action
        result["idempotent"] = current["status"] == target_status
        return result

    @staticmethod
    def heartbeat(session_id: str, raw_token: str, client_state: str) -> dict:
        current = InteractiveSessionService.authenticate(session_id, raw_token)
        if not current:
            raise PermissionError("Invalid or expired interactive session token")
        result = DBRepository.touch_interactive_session(session_id, client_state)
        result["stream_mode"] = "control_plane_only"
        return result

    @staticmethod
    def recover_stale(stale_seconds: int = 120, organization_id: str | None = None) -> list[dict]:
        """Recover sessions whose worker lease expired after a process restart."""
        bounded = max(30, min(int(stale_seconds), 86400))
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=bounded)
        return DBRepository.recover_stale_interactive_sessions(cutoff, organization_id)

    @staticmethod
    def recover_session(session_id: str, raw_token: str) -> dict:
        if not InteractiveSessionService.authenticate(session_id, raw_token):
            raise PermissionError("Invalid or expired interactive session token")
        interactive_browser_worker.stop(session_id)
        return DBRepository.mark_interactive_session_recovery_required(session_id)

    @staticmethod
    def _page_is_allowed(page_url: str, session: dict) -> bool:
        parsed = urlparse(page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return origin in set(session.get("allowed_origins") or [])

    @staticmethod
    def capture_evidence(session_id: str, raw_token: str, payload: dict) -> dict:
        session = InteractiveSessionService.authenticate(session_id, raw_token)
        if not session:
            raise PermissionError("Invalid or expired interactive session token")
        page_url = str(payload.get("page_url", "")).strip()
        if not InteractiveSessionService._page_is_allowed(page_url, session):
            raise ValueError("evidence page URL is outside the session origin allowlist")
        text_content = str(payload.get("text_content", ""))
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict) or len(metadata) > 30:
            raise ValueError("evidence metadata is invalid")
        # Keep only bounded locator metadata.  Raw typed values and page text
        # are represented by a digest and length, never persisted.
        text_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest() if text_content else ""
        secret_markers = ("secret", "password", "token", "cookie", "authorization", "input_value", "raw_value", "value")
        safe_meta = {
            str(key)[:80]: str(value)[:300]
            for key, value in metadata.items()
            if not any(marker in str(key).lower() for marker in secret_markers)
        }
        return DBRepository.add_interactive_evidence({
            "evidence_id": "ibe_" + uuid4().hex,
            "session_id": session_id,
            "organization_id": session["organization_id"],
            "event_type": payload["event_type"],
            "page_url": page_url,
            "route": str(payload.get("route") or "")[:1024],
            "label": str(payload.get("label") or "")[:255],
            "selector": str(payload.get("selector") or "")[:2048],
            "xpath": str(payload.get("xpath") or "")[:2048],
            "source": payload.get("source", "manual_interaction"),
            "text_sha256": text_hash,
            "text_length": len(text_content),
            "metadata": redact_sensitive_data(safe_meta),
        })

    @staticmethod
    def list_evidence(session_id: str, raw_token: str, limit: int = 200) -> list[dict]:
        if not InteractiveSessionService.authenticate(session_id, raw_token):
            raise PermissionError("Invalid or expired interactive session token")
        return DBRepository.list_interactive_evidence(session_id, limit)


interactive_session_service = InteractiveSessionService()
