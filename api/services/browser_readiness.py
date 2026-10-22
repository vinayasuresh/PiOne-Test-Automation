"""Playwright Chromium readiness verification for PiOne Testmate.

Provides a fast, deterministic check to confirm that the matching
Playwright Chromium executable is present on the host system.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BROWSER_INSTALL_HINT = r"Run .venv\Scripts\python.exe -m playwright install chromium"

_CACHED_STATUS: dict[str, Any] | None = None


def check_browser_readiness(force_refresh: bool = False) -> dict[str, Any]:
    """Verify that the pinned Playwright Chromium executable is installed and runnable."""
    global _CACHED_STATUS
    if _CACHED_STATUS is not None and not force_refresh:
        exec_path = _CACHED_STATUS.get("executable_path")
        if _CACHED_STATUS.get("ready") and exec_path and os.path.exists(exec_path):
            return dict(_CACHED_STATUS)

    # 1. Preferred method: ask Playwright directly
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exec_path = str(p.chromium.executable_path)
            if exec_path and os.path.exists(exec_path):
                _CACHED_STATUS = {
                    "ready": True,
                    "error": None,
                    "executable_path": exec_path,
                }
                return dict(_CACHED_STATUS)
            _CACHED_STATUS = {
                "ready": False,
                "error": f"Chromium executable is missing at '{exec_path}'. {BROWSER_INSTALL_HINT}",
                "executable_path": exec_path,
            }
            return dict(_CACHED_STATUS)
    except Exception as exc:
        # 2. Filesystem fallback if playwright python wrapper fails to launch driver
        try:
            configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
            browser_root = (
                Path(configured_root)
                if configured_root and configured_root != "0"
                else Path.home() / "AppData" / "Local" / "ms-playwright"
            )
            candidates = list(browser_root.glob("chromium-*/chrome-win*/chrome.exe"))
            if candidates and candidates[0].exists():
                _CACHED_STATUS = {
                    "ready": True,
                    "error": None,
                    "executable_path": str(candidates[0]),
                }
                return dict(_CACHED_STATUS)
        except Exception:
            pass

        _CACHED_STATUS = {
            "ready": False,
            "error": f"Playwright browser check failed: {exc}. {BROWSER_INSTALL_HINT}",
            "executable_path": None,
        }
        return dict(_CACHED_STATUS)
