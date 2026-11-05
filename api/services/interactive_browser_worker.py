"""Isolated Playwright worker boundary for interactive V3 sessions.

This module deliberately contains no public CDP/VNC endpoint.  A worker owns
one browser context per session and exposes only bounded lifecycle operations;
the media transport can be attached later without changing the V2 crawler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from threading import RLock
from typing import Any, Protocol


class BrowserAdapter(Protocol):
    def start(self, target_origin: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...


class PlaywrightBrowserAdapter:
    """Optional real Chromium adapter for an explicitly enabled worker.

    The V3 control plane is safe-by-default: it does not launch a browser
    unless ``INTERACTIVE_BROWSER_LAUNCH`` is enabled by the deployment.  When
    enabled, this adapter owns one Playwright instance/context/page and never
    exposes CDP, a debugging port, or arbitrary page-evaluation endpoints.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self, target_origin: str) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        headless = os.getenv("INTERACTIVE_BROWSER_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "on"}
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        timeout_ms = int(os.getenv("INTERACTIVE_BROWSER_NAV_TIMEOUT_MS", "30000"))
        self._page.goto(target_origin, wait_until="domcontentloaded", timeout=timeout_ms)

    def pause(self) -> None:
        # Pause is a lifecycle boundary.  It intentionally does not inject
        # JavaScript or pause arbitrary customer page execution.
        return None

    def resume(self) -> None:
        return None

    def stop(self) -> None:
        try:
            if self._context:
                self._context.close()
        finally:
            try:
                if self._browser:
                    self._browser.close()
            finally:
                if self._playwright:
                    self._playwright.stop()
                self._page = self._context = self._browser = self._playwright = None


def default_adapter_factory() -> BrowserAdapter | None:
    """Build a browser only when the operator explicitly opts in."""

    enabled = os.getenv("INTERACTIVE_BROWSER_LAUNCH", "").strip().lower() in {"1", "true", "yes", "on"}
    return PlaywrightBrowserAdapter() if enabled else None


@dataclass
class WorkerSessionSpec:
    session_id: str
    target_origin: str
    organization_id: str
    owner_id: str


@dataclass
class InteractiveBrowserWorker:
    """Small in-process worker registry used by the V3 control plane.

    The registry is intentionally bounded to one adapter per session and is
    safe for concurrent API calls.  Production deployment should place a
    durable queue/worker behind this same interface.
    """

    adapter_factory: Any | None = None
    _adapters: dict[str, BrowserAdapter] = field(default_factory=dict)
    _active_sessions: set[str] = field(default_factory=set)
    _lock: RLock = field(default_factory=RLock)

    def _new_adapter(self) -> BrowserAdapter | None:
        return self.adapter_factory() if self.adapter_factory else None

    def start(self, spec: WorkerSessionSpec) -> str:
        with self._lock:
            if spec.session_id in self._active_sessions:
                return "already_started"
            adapter = self._new_adapter()
            if adapter is not None:
                try:
                    adapter.start(spec.target_origin)
                except Exception:
                    # A failed launch must not leave a partially initialized
                    # browser process behind or make a retry unsafe.
                    try:
                        adapter.stop()
                    except Exception:
                        pass
                    raise
                self._adapters[spec.session_id] = adapter
            self._active_sessions.add(spec.session_id)
            return "started"

    def ensure_started(self, spec: WorkerSessionSpec) -> str:
        """Start a worker after API/process recovery when no adapter exists."""
        return self.start(spec)

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active_sessions

    def pause(self, session_id: str) -> str:
        with self._lock:
            adapter = self._adapters.get(session_id)
            if adapter:
                adapter.pause()
            return "paused"

    def resume(self, session_id: str) -> str:
        with self._lock:
            adapter = self._adapters.get(session_id)
            if adapter:
                adapter.resume()
            return "resumed"

    def stop(self, session_id: str) -> str:
        with self._lock:
            adapter = self._adapters.pop(session_id, None)
            if adapter:
                adapter.stop()
            self._active_sessions.discard(session_id)
            return "stopped"

    def close_all(self) -> None:
        with self._lock:
            for adapter in list(self._adapters.values()):
                try:
                    adapter.stop()
                except Exception:
                    pass
            self._adapters.clear()
            self._active_sessions.clear()


interactive_browser_worker = InteractiveBrowserWorker(adapter_factory=default_adapter_factory)
