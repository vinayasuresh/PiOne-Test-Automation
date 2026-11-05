"""Feature-gated interactive browser control plane for OmniTest V3.

The HTTP/WebSocket surface carries lifecycle and user-input acknowledgements.
It never exposes a public CDP, VNC, or arbitrary page-execution endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from api.models.interactive_session_models import (
    InteractiveEvidenceRequest,
    InteractiveSessionActionRequest,
    InteractiveSessionCreateRequest,
    InteractiveSessionHeartbeatRequest,
)
from api.services.interactive_session_service import interactive_session_service
from api.services.v3_feature_flags import is_interactive_browser_enabled


router = APIRouter(prefix="/v3/interactive-sessions", tags=["V3 interactive browser"])


def _require_interactive() -> None:
    if not is_interactive_browser_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interactive browser sessions are disabled")


def _token(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def _auth_or_401(session_id: str, authorization: str | None, session_token: str | None) -> dict:
    current = interactive_session_service.authenticate(session_id, _token(session_token) or _token(authorization))
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired interactive session token")
    return current


@router.post("", status_code=status.HTTP_201_CREATED)
def create_session(request: InteractiveSessionCreateRequest) -> dict:
    _require_interactive()
    record = interactive_session_service.create(request.model_dump())
    # The opaque token is returned exactly once from this response.  It is not
    # present in any persisted object, status response, log, or websocket event.
    return {"status": "created", "session": record}


@router.get("/{session_id}")
def get_session(
    session_id: str,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    _require_interactive()
    record = _auth_or_401(session_id, authorization, session_token)
    return {"status": "ok", "session": record}


@router.post("/{session_id}/actions")
def action_session(
    session_id: str,
    request: InteractiveSessionActionRequest,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    _require_interactive()
    token = _token(session_token) or _token(authorization)
    _auth_or_401(session_id, authorization, session_token)
    try:
        result = interactive_session_service.action(session_id, token or "", request.action, request.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ImportError, OSError, RuntimeError) as exc:
        # A browser worker may be unavailable (for example Playwright is not
        # installed on this host).  Keep the durable checkpoint truthful and
        # return a retryable response instead of an opaque 500.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interactive browser worker is unavailable; retry from the saved checkpoint",
        ) from exc
    return {"status": "ok", "session": result}


@router.post("/{session_id}/heartbeat")
def heartbeat_session(
    session_id: str,
    request: InteractiveSessionHeartbeatRequest,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    _require_interactive()
    token = _token(session_token) or _token(authorization)
    _auth_or_401(session_id, authorization, session_token)
    try:
        result = interactive_session_service.heartbeat(session_id, token or "", request.client_state)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "ok", "session": result}


@router.post("/{session_id}/recover")
def recover_session(
    session_id: str,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    """Stop a lost worker and expose a checkpoint-preserving retry state."""
    _require_interactive()
    token = _token(session_token) or _token(authorization)
    _auth_or_401(session_id, authorization, session_token)
    try:
        result = interactive_session_service.recover_session(session_id, token or "")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    next_action = "retry" if result.get("status") == "failed" else "none"
    response_status = "recovery_required" if next_action == "retry" else "terminal"
    return {"status": response_status, "session": result, "next_action": next_action}


@router.post("/{session_id}/evidence", status_code=status.HTTP_201_CREATED)
def capture_evidence(
    session_id: str,
    request: InteractiveEvidenceRequest,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    _require_interactive()
    token = _token(session_token) or _token(authorization)
    _auth_or_401(session_id, authorization, session_token)
    try:
        evidence = interactive_session_service.capture_evidence(session_id, token or "", request.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"status": "captured", "evidence": evidence}


@router.get("/{session_id}/evidence")
def list_evidence(
    session_id: str,
    authorization: str | None = Header(default=None),
    session_token: str | None = Header(default=None, alias="X-Interactive-Session-Token"),
) -> dict:
    _require_interactive()
    token = _token(session_token) or _token(authorization)
    _auth_or_401(session_id, authorization, session_token)
    try:
        evidence = interactive_session_service.list_evidence(session_id, token or "")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "ok", "session_id": session_id, "evidence": evidence, "count": len(evidence)}


@router.websocket("/ws/{session_id}")
async def session_socket(
    websocket: WebSocket,
    session_id: str,
    session_token: str | None = Query(default=None),
) -> None:
    if not is_interactive_browser_enabled():
        await websocket.close(code=4404, reason="Interactive browser sessions are disabled")
        return
    current = interactive_session_service.authenticate(session_id, session_token)
    if not current:
        await websocket.close(code=4401, reason="Invalid or expired session token")
        return
    await websocket.accept()
    await websocket.send_json({
        "event": "session_state",
        "session_id": session_id,
        "status": current["status"],
        "phase": current["phase"],
        "stream_mode": "control_plane_only",
        "message": "Authenticated control channel ready; media streaming is not enabled in this build.",
    })
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "input_rejected", "reason": "message must be JSON"})
                continue
            event = str(payload.get("event", "")).strip().lower()
            if event == "heartbeat":
                result = interactive_session_service.heartbeat(session_id, session_token, "websocket")
                await websocket.send_json({"event": "heartbeat_ack", "session": result})
            elif event == "capture_evidence":
                try:
                    evidence_payload = InteractiveEvidenceRequest.model_validate(payload.get("evidence") or {})
                    evidence = interactive_session_service.capture_evidence(
                        session_id, session_token, evidence_payload.model_dump()
                    )
                    await websocket.send_json({"event": "evidence_captured", "evidence": evidence})
                except (PermissionError, ValueError) as exc:
                    await websocket.send_json({"event": "input_rejected", "reason": str(exc)})
            elif event in {"pause", "resume", "continue", "retry", "complete", "cancel"}:
                result = interactive_session_service.action(session_id, session_token, event)
                await websocket.send_json({"event": "session_state", "session": result})
            else:
                await websocket.send_json({"event": "input_rejected", "reason": "unsupported bounded control event"})
    except WebSocketDisconnect:
        return
