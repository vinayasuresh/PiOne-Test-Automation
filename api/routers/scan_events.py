"""WebSocket endpoint for optional live scan status updates."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/scan-events")
async def scan_events(websocket: WebSocket) -> None:
    """Accept status clients even when no scan event producer is active."""
    await websocket.accept()
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return
