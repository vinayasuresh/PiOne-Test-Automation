"""WebSocket endpoint for live scan status updates scoped by run_id or scan_id."""

import asyncio
from queue import Empty
import re
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.services.scan_event_bus import discard_channel, get_channel, get_latest_telemetry

router = APIRouter()
_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@router.websocket("/ws/scan-events")
async def scan_events(
    websocket: WebSocket,
    scan_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Stream events from the matching in-process scan worker.
    
    Accepts either scan_id or run_id query parameters. Enforces strict identifier validation.
    """
    effective_id = (scan_id or run_id or "").strip()
    if not effective_id or not _ID_REGEX.fullmatch(effective_id) or ".." in effective_id or "/" in effective_id or "\\" in effective_id:
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()
    channel = get_channel(effective_id)
    expected_ids = {effective_id}
    if scan_id:
        expected_ids.add(scan_id)
    if run_id:
        expected_ids.add(run_id)

    # If reconnecting, push the latest known telemetry state immediately
    snapshot = get_latest_telemetry(effective_id)
    if snapshot:
        try:
            await websocket.send_json(snapshot)
        except Exception:
            pass

    try:
        while True:
            try:
                event = await asyncio.to_thread(channel.get, True, 20)
            except Empty:
                await websocket.send_json({"type": "heartbeat", "scan_id": effective_id, "run_id": effective_id})
                continue
            event_run = str(event.get("run_id") or "").strip()
            event_scan = str(event.get("scan_id") or "").strip()
            if event_run and event_run not in expected_ids and event_scan and event_scan not in expected_ids:
                continue
            await websocket.send_json(event)
            if event.get("type") in {"scan_completed", "scan_stopped", "scan_failed"}:
                return
    except WebSocketDisconnect:
        return
