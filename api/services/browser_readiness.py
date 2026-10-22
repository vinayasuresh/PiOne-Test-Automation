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

    # Resolve the executable without starting Playwright's sync driver. On
    # Windows that driver startup can block on an asyncio socketpair and would
    # make /health hang before the application is usable.
    configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    roots = []
    if configured_root and configured_root != "0":
        roots.append(Path(configured_root))
    else:
        roots.extend([
            Path.home() / "AppData" / "Local" / "ms-playwright",
            Path.home() / ".cache" / "ms-playwright",
        ])
    patterns = ["chromium-*/chrome-win*/chrome.exe", "chromium-*/chrome-linux/chrome", "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"]
    for root in roots:
        try:
            for pattern in patterns:
                candidates = sorted(root.glob(pattern))
                if candidates and candidates[0].is_file():
                    _CACHED_STATUS = {"ready": True, "error": None, "executable_path": str(candidates[0])}
                    return dict(_CACHED_STATUS)
        except OSError:
            continue

    # Fallback for non-standard Playwright installations. This path is only
    # reached when no installed browser executable can be found directly.
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
            pass
        except Exception:
            pass

        _CACHED_STATUS = {
            "ready": False,
            "error": f"Playwright browser check failed: {exc}. {BROWSER_INSTALL_HINT}",
            "executable_path": None,
        }
        return dict(_CACHED_STATUS)
