"""Thread-safe event queues shared by scan workers and WebSocket clients.

Guarantees:
- Channel isolation: events published to run A never leak to run B.
- Automatic secret and credential redaction on all published events.
- Bound payload sizes to prevent memory exhaustion.
- Automatic eviction of stale or abandoned channels.
"""

from __future__ import annotations

import json
from queue import Queue
import re
from threading import Lock, Timer
import time
from typing import Any

from automation_framework.utils.redactor import redact_sensitive_data

_lock = Lock()
_channels: dict[str, Queue[dict[str, Any]]] = {}
_aliases: dict[str, str] = {}
_latest_telemetry: dict[str, dict[str, Any]] = {}
_last_activity: dict[str, float] = {}
_MAX_EVENTS_PER_SCAN = 2048
_MAX_FIELD_LENGTH = 8192
_MAX_EVENT_BYTES = 32 * 1024  # 32 KB per event


def _bound_event_payload(data: Any) -> Any:
    """Enforce size caps on string fields within events."""
    if isinstance(data, str):
        if len(data) > _MAX_FIELD_LENGTH:
            return data[:_MAX_FIELD_LENGTH] + "...[TRUNCATED]"
        return data
    if isinstance(data, dict):
        return {k: _bound_event_payload(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_bound_event_payload(item) for item in data[:500]]
    return data


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
        _last_activity[canon] = time.time()
        return _channels.setdefault(canon, Queue(maxsize=_MAX_EVENTS_PER_SCAN))


def get_latest_telemetry(scan_id: str) -> dict[str, Any] | None:
    canon = _canonical_id(scan_id)
    with _lock:
        return _latest_telemetry.get(canon)


def publish(scan_id: str, event: dict[str, Any]) -> None:
    canon = _canonical_id(scan_id)
    channel = get_channel(canon)

    # Redact credentials and enforce bounded size
    safe_event = redact_sensitive_data(event)
    bounded_event = _bound_event_payload(safe_event)

    if "timestamp" not in bounded_event:
        bounded_event["timestamp"] = time.time()

    # Track latest telemetry snapshot for reconnecting clients
    with _lock:
        _latest_telemetry[canon] = bounded_event
        _last_activity[canon] = time.time()

    try:
        channel.put_nowait(bounded_event)
    except Exception:
        # Keep the newest progress and discard one oldest queued event if full.
        try:
            channel.get_nowait()
            channel.put_nowait(bounded_event)
        except Exception:
            return

    if bounded_event.get("type") in {"scan_completed", "scan_failed"}:
        timer = Timer(60, discard_channel, args=(canon,))
        timer.daemon = True
        timer.start()


def discard_channel(scan_id: str) -> None:
    canon = _canonical_id(scan_id)
    with _lock:
        _channels.pop(canon, None)
        _latest_telemetry.pop(canon, None)
        _last_activity.pop(canon, None)
        # Clean up aliases pointing to this channel
        to_del = [k for k, v in _aliases.items() if v == canon or k == canon]
        for k in to_del:
            _aliases.pop(k, None)


def cleanup_stale_channels(max_idle_seconds: float = 900) -> int:
    """Evict channels that have had no subscriber or publisher activity."""
    now = time.time()
    stale: list[str] = []
    with _lock:
        for cid, last_seen in _last_activity.items():
            if now - last_seen > max_idle_seconds:
                stale.append(cid)
    for cid in stale:
        discard_channel(cid)
    return len(stale)
