"""Thread-safe event queues shared by scan workers and WebSocket clients."""

from __future__ import annotations

from queue import Queue
from threading import Lock, Timer
import time
from typing import Any


_lock = Lock()
_channels: dict[str, Queue[dict[str, Any]]] = {}
_aliases: dict[str, str] = {}
_latest_telemetry: dict[str, dict[str, Any]] = {}
_MAX_EVENTS_PER_SCAN = 2048


def register_alias(alias_id: str, target_id: str) -> None:
    """Map an alias (e.g. run_id) to a canonical target channel (e.g. scan_id)."""
    if not alias_id or not target_id:
        return
    with _lock:
        _aliases[alias_id] = target_id


def _canonical_id(channel_id: str) -> str:
    return _aliases.get(channel_id, channel_id)


def get_channel(scan_id: str) -> Queue[dict[str, Any]]:
    canon = _canonical_id(scan_id)
    with _lock:
        return _channels.setdefault(canon, Queue(maxsize=_MAX_EVENTS_PER_SCAN))


def get_latest_telemetry(scan_id: str) -> dict[str, Any] | None:
    canon = _canonical_id(scan_id)
    with _lock:
        return _latest_telemetry.get(canon)


def publish(scan_id: str, event: dict[str, Any]) -> None:
    canon = _canonical_id(scan_id)
    channel = get_channel(canon)

    if "timestamp" not in event:
        event["timestamp"] = time.time()

    # Track latest telemetry snapshot for reconnecting clients
    with _lock:
        _latest_telemetry[canon] = event

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
        timer = Timer(60, discard_channel, args=(canon,))
        timer.daemon = True
        timer.start()


def discard_channel(scan_id: str) -> None:
    canon = _canonical_id(scan_id)
    with _lock:
        _channels.pop(canon, None)
        _latest_telemetry.pop(canon, None)
        # Clean up aliases pointing to this channel
        to_del = [k for k, v in _aliases.items() if v == canon or k == canon]
        for k in to_del:
            _aliases.pop(k, None)
