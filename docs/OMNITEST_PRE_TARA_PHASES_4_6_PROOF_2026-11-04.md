# OmniTest pre-Tara phases 4–6 proof — 2026-11-04

## Scope

This increment continues the V3 pre-Tara foundation without changing the V2
scan, crawl, generation, execution, report, checkpoint, or regression flow.
The three implemented slices are:

1. **Recovery-safe interactive worker lifecycle**
2. **Bounded manual interaction evidence**
3. **A focused pre-Tara release gate**

All three remain additive and require both `V3_ASSURANCE_ENABLED=true` and
`INTERACTIVE_BROWSER_ENABLED=true` for the interactive API surface.

## Phase 4 — recovery-safe worker lifecycle

Implemented:

- Durable session rows now retain the checkpoint, lease timestamps, worker ID,
  phase, and recovery reason.
- `recover_stale_interactive_sessions` marks an abandoned `starting`, `running`,
  or `retrying` session as `failed` with `recovery_required`; it never touches
  terminal sessions.
- Retry after recovery starts a fresh isolated worker when the old in-process
  worker is gone, while retaining the same checkpoint and evidence history.
- An explicit authenticated `POST /v3/interactive-sessions/{id}/recover`
  provides a safe operator recovery action and returns `next_action: retry`
  only for a retryable failed session; terminal sessions return `next_action:
  none`.
- Repeated starts remain idempotent and worker cleanup removes the active lease.

Evidence:

- `test_phase4_restart_recovery_preserves_checkpoint_and_retry` passed.
- `test_release_gate_recovery_is_truthful_and_retryable` passed.

## Phase 5 — bounded interaction evidence

Implemented:

- `POST /v3/interactive-sessions/{id}/evidence` and the control-channel
  `capture_evidence` event accept only bounded `click`, `input`, `select`,
  `navigation`, or `assertion` records.
- Evidence is origin-checked against the session allowlist and stores route,
  label, CSS selector, XPath, source, and a SHA-256 text fingerprint.
- Raw typed values, page text, cookies, authorization data, HTML, and arbitrary
  JavaScript are not persisted. Metadata keys containing secret/value markers
  are dropped before persistence.
- `GET /v3/interactive-sessions/{id}/evidence` returns the redacted ledger in
  chronological order.
- A cross-origin page URL is rejected before persistence.

Evidence:

- `test_phase5_locator_evidence_is_bounded_and_redacted` passed.
- `test_phase5_evidence_rejects_cross_origin_and_raw_secret_fields` passed.
- `test_release_gate_cross_origin_evidence_is_blocked` passed.

## Phase 6 — focused release gate

The new gate proves the pre-Tara contracts at the service boundary:

- V3 and interactive flags are explicitly opt-in; default-off remains true.
- Session lifecycle and recovery are durable and retryable.
- Evidence has session lineage and contains no raw input values.
- Cross-origin evidence is blocked.
- The full V3 focused slice remains green.

Commands and results:

| Check | Result | Evidence type |
|---|---:|---|
| `python -m compileall -q api automation_framework/tests` | PASS | source/test |
| interactive phase suite | 9 passed | unit/service |
| interactive release gate | 5 passed | unit/service |
| V3 suite + interactive suites | 78 passed, 7 deselected | unit/service |
| fresh Alembic SQLite migration through `j0a1b2c3d4e5` | PASS | migration |
| `git diff --check` | PASS (legacy CRLF warnings only) | source |

## Honest limits

- This host does not provide a usable rendered-browser control channel in this
  run and port 8000 was not available, so no browser screenshot or real
  Chromium/media-stream proof is claimed here.
- The Playwright adapter is still opt-in and control-plane-only. It launches a
  private context when explicitly enabled; it does not expose CDP, VNC, or
  arbitrary page evaluation.
- Multi-process durable queues, tenant/RBAC authorization, and Tara webhooks
  remain later release work. Do not start Tara integration until the rendered
  browser gate is completed on a socket-capable runner.

## Next gate

Run the V3-enabled rendered-browser flow on a socket-capable runner and capture
the interaction evidence, recovery, and report-download screenshots. Then run
the V2 regression with V3 disabled before any Tara connector work.
