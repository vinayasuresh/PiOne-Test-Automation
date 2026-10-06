"""WebSocket endpoint for optional live scan status updates."""

import asyncio

from queue import Empty

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.services.scan_event_bus import discard_channel, get_channel

router = APIRouter()


@router.websocket("/ws/scan-events")
async def scan_events(websocket: WebSocket, scan_id: str) -> None:
    """Stream events from the matching in-process scan worker."""
    await websocket.accept()
    channel = get_channel(scan_id)
    try:
        while True:
            try:
                event = await asyncio.to_thread(channel.get, True, 20)
            except Empty:
                await websocket.send_json({"type": "heartbeat", "scan_id": scan_id})
                continue
            await websocket.send_json(event)
            if event.get("type") in {"scan_completed", "scan_failed"}:
                discard_channel(scan_id)
                return
    except WebSocketDisconnect:
        return
