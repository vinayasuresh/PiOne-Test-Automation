# OmniTest V3 Phases 0–3 Execution Proof

Date: 2026-11-04
Branch: `v3-pre-tara-foundation`
Scope: additive pre-Tara interactive browser foundation; existing V2 scan and execution flow remains unchanged.

## Release boundary

The capability is disabled unless both flags are explicitly enabled:

```text
V3_ASSURANCE_ENABLED=true
INTERACTIVE_BROWSER_ENABLED=true
```

The worker does not launch Chromium unless the operator also sets:

```text
INTERACTIVE_BROWSER_LAUNCH=true
```

This prevents an accidental browser or customer-site session in a normal V2 deployment. The worker never exposes CDP, VNC, a debugging port, or arbitrary page-evaluation endpoints.

## Phase 0 — V2 protection and opt-in boundary

Implemented:

- V3 and interactive browser flags are evaluated at request time.
- Interactive routes return not-found while the flags are disabled.
- Existing V2 routes and models were not replaced.
- The V2 compatibility suite remains the release gate for this additive work.

Evidence:

- `automation_framework/tests/test_interactive_session_phases.py::test_phase0_feature_flags_keep_v2_surface_hidden`
- `automation_framework/tests/test_v3_feature_flags.py`

Status: **implemented and unit verified**.

## Phase 1 — Durable interactive-session lifecycle

Implemented:

- `interactive_browser_sessions` persistence model and Alembic migration.
- Create, authenticate, heartbeat, and lifecycle transitions.
- Opaque `ibs_...` token is returned once; only its SHA-256 hash is persisted.
- Expiry is enforced on read and authentication.
- Start is idempotent; pause, resume, continue, retry, complete, and cancel are bounded transitions.
- Organization and owner fields remain attached to every session.
- Sensitive values are redacted from response metadata and logs.

Status: **implemented and unit verified**.

## Phase 2 — Isolated browser-worker boundary

Implemented:

- One adapter/context per session with a concurrency-safe registry.
- Explicit cleanup on stop and application shutdown.
- Optional lazy Playwright Chromium adapter (`PlaywrightBrowserAdapter`).
- The default worker is no-op until `INTERACTIVE_BROWSER_LAUNCH=true`.
- Headless mode and navigation timeout are deployment-configurable.

The current adapter performs bounded navigation only. It does not expose page scripts or an operator-facing media stream. A production worker queue and media transport are follow-up work, not silently claimed as complete.

Status: **foundation implemented; real Chromium launch requires a host with browser runtime and is not proven in this environment**.

## Phase 3 — Authenticated control channel

Implemented:

- HTTP status/action/heartbeat endpoints under `/v3/interactive-sessions`.
- Authenticated WebSocket lifecycle channel under `/v3/interactive-sessions/ws/{session_id}`.
- Invalid/expired tokens are rejected; control events are bounded to lifecycle operations and heartbeats.
- The first event explicitly reports `stream_mode=control_plane_only`.

Not implemented yet:

- User-visible remote Chromium video/input streaming.
- Browser-side click capture, selector/XPath evidence capture, or cross-device interaction.
- Durable multi-worker queueing and restart-safe browser ownership beyond the persisted session record.

Status: **control-plane implemented and unit verified; media/remote-interaction proof is outstanding**.

## Verification record

The focused phase suite is:

```text
python -m pytest -q automation_framework/tests/test_interactive_session_phases.py
```

It covers flag isolation, token non-leakage, expiry/auth boundaries, lifecycle idempotency, worker isolation/cleanup, and control-channel contract. A rendered-browser pass is not claimed when the browser bridge is unavailable or port 8000 cannot bind.

Latest evidence from this checkout:

```text
70 passed, 7 deselected in 2.00s   (all test_v3_*.py + interactive phase suite)
6 passed in 0.29s                  (interactive phase suite alone, after final fix)
compileall: PASS
alembic upgrade head on fresh SQLite: PASS (through i9b0c1d2e3f4)
router smoke with V3 flags enabled: PASS (create → start → authenticated read)
flag-gate smoke with V3 disabled: PASS (404)
token storage smoke: PASS (hash only; raw token absent)
```

## Next gate before Tara

1. Run with both flags enabled on a host where port 8000 and Chromium are available.
2. Create a session against an authorized staging origin and verify the actual Playwright adapter starts and closes.
3. Add a bounded input/event transport that records route, element, selector, and checkpoint evidence without exposing CDP.
4. Prove restart/retry/duplicate behavior through the rendered UI.
5. Run the complete V2 regression suite with V3 disabled.
6. Only then begin signed Tara webhook intake and result callbacks.

## Evidence boundary

This document distinguishes implemented code and unit evidence from unproven live-browser behavior. It must not be used as a claim that remote interactive Chromium is production-ready.
