#!/usr/bin/env python3
"""run_release_gate.py: Mandatory release-gate execution script for PiOne Testmate V2.

Runs browser-backed service-integration release gates, verifying:
- Playwright Chromium executable readiness
- Full Mock Demo workflow (offline, zero DNS resolution)
- Controlled fixture live-like scan
- Interactive checkpoint capture and continue/retry/end actions
- Refresh/reopen recovery
- Analyze evidence visibility
- Planning context and testcase count shortfall explanation
- Execution and report navigation
- Checkpoint, Saved Run, baseline, and regression replay
- Excel testcase upload lineage and source format exports
- Full downloadable artifact manifest

Returns exit code 0 on all tests passing, or non-zero on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def check_runtime_readiness() -> bool:
    """Verify the runtime can create the async primitives required by the gates.

    ``TestClient`` and Playwright create an asyncio event loop.  On Windows that
    requires ``socket.socketpair()``.  A blocked socket-pair call used to make
    the release script appear to hang for up to five minutes, which is neither
    a useful failure nor release evidence.  Probe it in a bounded child process
    before starting the real suite so operators get one actionable error.
    """
    print("=" * 70)
    print("STEP 1: Pre-Flight Environment & Browser Readiness Check")
    print("=" * 70)
    try:
        from api.services.browser_readiness import BROWSER_INSTALL_HINT, check_browser_readiness
        status = check_browser_readiness()
        if not status.get("ready"):
            print("\n[ERROR] Playwright browser runtime is not ready:")
            print(f"  {status.get('error')}")
            print(f"  Action required: {BROWSER_INSTALL_HINT}\n")
            return False
        print(f"[OK] Chromium executable found at: {status.get('executable_path')}")
        print(f"[OK] Python virtualenv: {sys.executable}\n")
        return True
    except Exception as exc:
        print(f"\n[ERROR] Failed to check browser readiness: {exc}\n")
        return False


def check_async_runtime() -> bool:
    """Fail fast if the host cannot create a local socket pair for asyncio."""
    print("=" * 70)
    print("STEP 1.5: Async Runtime Capability Check")
    print("=" * 70)
    probe = (
        "import socket; "
        "left, right = socket.socketpair(); "
        "left.close(); right.close(); "
        "print('socketpair: OK')"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            "[ERROR] Python socket.socketpair() did not complete within 8 seconds.\n"
            "        The V2 gates require asyncio and cannot be certified on this host.\n"
            "        Close stale Python/test processes, restart the terminal or IDE, then retry.\n"
            "        If it persists, run the gate in the intended Windows host or CI runner.\n"
        )
        return False

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown socketpair failure").strip()
        print(
            "[ERROR] Python socket.socketpair() is unavailable.\n"
            f"        Detail: {detail}\n"
            "        Fix the local runtime before treating any V2 gate result as release evidence.\n"
        )
        return False

    print("[OK] Async socket-pair capability is available.\n")
    return True


def run_release_gate_tests() -> int:
    """Execute the release-gate suite via pytest with explicit bounded timeouts."""
    print("=" * 70)
    print("STEP 2: Executing Browser-Backed Release Gate Test Suite")
    print("=" * 70)

    test_file = PROJECT_ROOT / "automation_framework" / "tests" / "test_browser_release_gate.py"
    if not test_file.exists():
        print(f"[ERROR] Test file not found: {test_file}")
        return 1

    # Override pytest.ini addopts to include browser tests
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v",
        "--override-ini=addopts=",
        "--timeout=90",
        "--timeout-method=thread",
        "--tb=short",
    ]

    print(f"Command: {' '.join(cmd)}\n")
    start_time = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=300)
        elapsed = time.time() - start_time
        print("\n" + "=" * 70)
        print(f"STEP 3: Release Gate Execution Summary (Duration: {elapsed:.2f}s)")
        print("=" * 70)
        if proc.returncode == 0:
            print("\n[PASSED] ALL RELEASE GATE CHECKS VERIFIED SUCCESSFULLY.")
            print("  - Gate 1: Mock Demo Complete Offline Workflow [PASS]")
            print("  - Gate 2: Controlled Fixture Live Scan & Module Selection [PASS]")
            print("  - Gate 3: Interactive Mode, Checkpoints, Pause & Resume [PASS]")
            print("  - Gate 4: Refresh / Reopen Recovery from /run/{id}/state [PASS]")
            print("  - Gate 5: Canonical Route Preservation with Query & Fragments [PASS]")
            print("  - Gate 6: Planning Shortfall Explanation & Evidence Revision Lock [PASS]")
            print("  - Gate 7: Baseline Freeze, Compare, and Regression Replay [PASS]")
            print("  - Gate 8: Excel Testcase Upload Lineage & Source Format Export [PASS]")
        else:
            print(f"\n[FAILED] Release gate failed with exit code {proc.returncode}.\n")
        return proc.returncode
    except subprocess.TimeoutExpired:
        print("\n[TIMEOUT] Release gate execution exceeded 300 seconds timeout limit.\n")
        return 124
    except Exception as exc:
        print(f"\n[ERROR] Execution failed: {exc}\n")
        return 1


def main() -> int:
    if not check_runtime_readiness():
        return 1
    if not check_async_runtime():
        return 1
    return run_release_gate_tests()


if __name__ == "__main__":
    sys.exit(main())
