# OmniTest V3 Pre-Tara Verification — 2026-11-04

## Scope

This report records the implementation and verification work completed before
Tara/helpdesk integration. V2 remains the default release path; V3 assurance
is still opt-in through `V3_ASSURANCE_ENABLED`.

## Implemented in this pass

- API plan execution state is now persisted in the additive
  `api_test_plan_states` table.
- Running state is saved before the first API request.
- Each operation checkpoint is saved after completion, including redacted
  request metadata and assertion results.
- Interrupted plans are persisted and can be restored by plan ID after a
  process restart.
- Progress and Excel-report routes fall back to durable plan state when the
  in-memory executor store is empty.
- Resume validates that the persisted plan belongs to the requested contract.
- V2 routes and state transitions were not changed.

## Automated evidence

| Check | Result | Evidence |
|---|---|---|
| Python compilation | PASS | `python -m compileall -q api automation_framework` |
| V3/API/dashboard/database/orchestration suite | PASS | `55 passed, 7 deselected` |
| API workflow suite | PASS | `16 passed` |
| Durable checkpoint smoke | PASS | `DBRepository.save_v3_api_plan` → reload → `restore_api_plan` |
| Fresh SQLite migration chain | PASS | `python -m alembic upgrade head` on an empty SQLite database, including `h8a9b0c1d2e3f` |
| V3 default-off protection | PASS | Existing feature-flag tests remain green |
| Diff hygiene | PASS | `git diff --check` produced no whitespace errors |
| Full repository pytest | BLOCKED | Windows runner progressed through 57 tests, then hit the known async socket/proactor restriction; no release-pass claim |
| Dependency consistency | BLOCKED | `pip check`: pre-existing `botocore 1.34.0` requires `urllib3<2.1`, environment has `urllib3 2.6.3` |

## Browser evidence and blocker

The rendered UI previously confirmed the API Testing mode, contract ingestion
controls, and DB ↔ API ↔ UI validation controls. With the live process running
V3 disabled, ingestion correctly displayed `V3 assurance is disabled`.

An opt-in V3 process was attempted on `127.0.0.1:8000` for rendered proof, but
this Windows host did not expose a working listener during the attempt. The
browser download, live API execution, database parity, and restart recovery
therefore remain **not browser-proven** in this environment. This is recorded
as a release-gate blocker, not reported as a passing live test.

## Verified versus remaining

### Verified

- V3 contract normalization and safety tests.
- Safe read-only API execution classifications.
- Mutation gating and redaction tests.
- Durable API plan state and resume identity at repository/service level.
- Existing V2 feature-gate behavior remains protected by the focused suites.

### Remaining before Tara

1. Run V3 enabled on a socket-capable host and capture the rendered workflow.
2. Download and reopen the 10-sheet report in the browser.
3. Prove dashboard/API mismatch and database/API/UI parity with browser evidence.
4. Exercise restart, retry, cancellation, and duplicate-request behavior in a
   rendered/API integration run.
5. Run the full V2 regression and CI evidence collection on the CI runner.

The repository's existing local SQLite file is still at the initial Alembic
revision because the application historically initializes tables with
`Base.metadata.create_all`. The new migration is validated on a fresh
database; production rollout must apply Alembic migrations before switching
the database-backed V3 plan recovery on.

Tara integration must not be presented as complete until these gates are
green and their evidence paths are recorded.
