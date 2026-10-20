"""scan_control_service.py: In-memory coordination of live scan control signals.

Provides thread-safe pause, resume, stop (end-scan-and-save), and interactive
module review decisions (continue, retry, end) bound to a specific run_id.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class _ScanControl:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.paused = False
        self.stop_requested = False
        self.pending_decision: Optional[str] = None
        self._resume_event = threading.Event()
        self._resume_event.set()

    def pause(self) -> None:
        self.paused = True
        self._resume_event.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume_event.set()

    def stop(self) -> None:
        self.stop_requested = True
        self.pending_decision = "end"
        self._resume_event.set()

    def wait_if_paused(self, timeout: float = 0.5) -> bool:
        """Wait while paused. Returns True if resumed, False if timeout expired while still paused."""
        if not self.paused:
            return True
        return self._resume_event.wait(timeout=timeout)


_LOCK = threading.Lock()
_CONTROLS: dict[str, _ScanControl] = {}


def get_scan_control(run_id: str) -> _ScanControl:
    with _LOCK:
        if run_id not in _CONTROLS:
            _CONTROLS[run_id] = _ScanControl(run_id)
        return _CONTROLS[run_id]


def pause_scan(run_id: str) -> None:
    ctrl = get_scan_control(run_id)
    ctrl.pause()


def resume_scan(run_id: str) -> None:
    ctrl = get_scan_control(run_id)
    ctrl.resume()


def is_scan_paused(run_id: str) -> bool:
    ctrl = get_scan_control(run_id)
    return ctrl.paused


def stop_scan(run_id: str) -> None:
    ctrl = get_scan_control(run_id)
    ctrl.stop()


def is_scan_stop_requested(run_id: str) -> bool:
    ctrl = get_scan_control(run_id)
    return ctrl.stop_requested


def set_user_decision(run_id: str, decision: str) -> None:
    ctrl = get_scan_control(run_id)
    ctrl.pending_decision = decision.strip().lower()
    if ctrl.pending_decision == "end":
        ctrl.stop()
    elif ctrl.paused:
        ctrl.resume()


def pop_user_decision(run_id: str) -> Optional[str]:
    ctrl = get_scan_control(run_id)
    decision = ctrl.pending_decision
    ctrl.pending_decision = None
    return decision


def clear_scan_control(run_id: str) -> None:
    with _LOCK:
        _CONTROLS.pop(run_id, None)
