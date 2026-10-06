"""Thread-safe event queues shared by scan workers and WebSocket clients."""

from __future__ import annotations

from queue import Queue
from threading import Lock
from threading import Timer
from typing import Any


_lock = Lock()
_channels: dict[str, Queue[dict[str, Any]]] = {}
_MAX_EVENTS_PER_SCAN = 2048


def get_channel(scan_id: str) -> Queue[dict[str, Any]]:
    with _lock:
        return _channels.setdefault(scan_id, Queue(maxsize=_MAX_EVENTS_PER_SCAN))


def publish(scan_id: str, event: dict[str, Any]) -> None:
    channel = get_channel(scan_id)
    try:
        channel.put_nowait(event)
    except Exception:
        # Keep the newest progress and discard one oldest queued event if full.
        try:
            channel.get_nowait()
            channel.put_nowait(event)
        except Exception:
            return
    if event.get("type") in {"scan_completed", "scan_failed"}:
        timer = Timer(60, discard_channel, args=(scan_id,))
        timer.daemon = True
        timer.start()


def discard_channel(scan_id: str) -> None:
    with _lock:
        _channels.pop(scan_id, None)
