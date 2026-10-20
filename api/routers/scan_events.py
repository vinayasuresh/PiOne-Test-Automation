"""WebSocket endpoint for live scan status updates scoped by run_id or scan_id."""

import asyncio
from queue import Empty
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.services.scan_event_bus import discard_channel, get_channel, get_latest_telemetry

router = APIRouter()


@router.websocket("/ws/scan-events")
async def scan_events(
    websocket: WebSocket,
    scan_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Stream events from the matching in-process scan worker.
    
    Accepts either scan_id or run_id query parameters.
    """
    effective_id = scan_id or run_id
    if not effective_id:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    channel = get_channel(effective_id)

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
            await websocket.send_json(event)
            if event.get("type") in {"scan_completed", "scan_failed"}:
                discard_channel(effective_id)
                return
    except WebSocketDisconnect:
        return
