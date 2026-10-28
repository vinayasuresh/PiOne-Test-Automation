# PiOne Testmate V2 Live Audit and Remediation Plan

**Audit date:** 26 October 2026
**Scope:** current local repository, rendered browser workflow, focused automated tests, source contracts, report/export lifecycle, and bounded security-test design.
**Decision:** do not describe V2 as production-ready or fully end-to-end proven. It is suitable for an internal demo only after the P0 defects below are fixed and re-tested.

## Executive conclusion

PiOne Testmate has a credible QA foundation. It has browser-scanner architecture, structured element evidence, selector and XPath capture, action safety classification, deterministic grounded planning, checkpoint/run concepts, execution records, downloadable artifacts, and baseline/replay concepts.

However, its core workflow is not globally consistent today. The Mock Demo flow is blocked before it can reach the mock service, browser E2E coverage is excluded from the default test command, and larger test groups do not reliably emit a terminal pytest summary during this audit. These defects mean the system cannot honestly claim that every screen, button, report, saved run, checkpoint, or recovery path is working.

## Evidence collected in this audit

### Rendered browser proof

The local UI was reset, switched to **Mock Demo**, discovered modules, and started Crawl Progress.

The UI said **Mock Demo Mode Active**, but the crawl failed with:

```text
Value error, Cannot resolve hostname 'example.test'
```

The Crawl Progress screen then showed all of the following together:

- a Running status pill;
- a CRAWL_INTERRUPTED error modal;
- 0 of 0 modules, routes, targets, actions, and parking-lot items;
- Retry Crawl and export controls;
- no durable run state for the generated mock run ID.

This is a reproduced product defect, not a hypothetical concern.

### Root cause

The frontend marks Mock Demo with `mock: !state.isLive`, but it still sends the target URL selected in the UI. The Mock Demo target is `https://example.test`. The `ScanRequest.url` field validator calls `validate_target_url()` before the router reaches the `if request.mock` branch. Since `example.test` is intentionally not resolvable on this host, the request receives a 422 validation error. No mock response, mock export, run context, telemetry, checkpoint, report, or saved-run record can be created.

### Automated evidence with final summaries

The following focused tests completed with final pytest summaries:

| Area | Command scope | Result |
|---|---|---:|
| Requested testcase count | grounded cap and expansion tests | 2 passed |
| Planning and uploaded testcase service | `test_test_planning_service.py` | 13 passed |
| Ten-sheet Excel export contract | focused run-context export test | 1 passed |
| Persistence redaction and SQLite fallback | two focused tests | 2 passed |
| Syntax and whitespace | Python compile plus `git diff --check` | passed |

### Evidence that is not a pass

| Area | Observation | Meaning |
|---|---|---|
| Default test command | 174 of 238 tests collected; 64 browser tests deselected by `-m "not browser"` | a normal green run does not prove browser workflow behaviour |
| Browser E2E attempt | direct marked browser test attempt did not finish with a final summary during the audit window | do not claim E2E passed |
| Wider persistence/security groups | emitted passing dots but did not reliably return terminal summaries in the audit window | test-runner lifecycle requires diagnosis |
| Public-host SSRF tests | safe public host tests failed when DNS was unavailable | test isolation is weak; do not weaken SSRF guardrails to make them pass |

## Core defects to fix

### P0  Mock Demo is rejected before mock handling

**Impact:** new users, demos, training, and smoke tests fail before a run exists. The UI makes a false promise that Mock Demo is offline and deterministic.

**Correct fix:** make mock requests independent of external DNS. Either use a safe, explicitly recognized synthetic mock origin before generic URL validation, or introduce a mock-aware model validator that validates scheme/format but skips DNS only when `mock` is true. Do not relax SSRF validation for live requests.

**Acceptance proof:** Scan Application -> Mock Demo -> Discover Modules -> Crawl -> Analyze -> Generate Tests -> Execute -> Report completes offline, creates a durable run context, checkpoint, JSON/YAML/Excel exports, and Saved Run. No DNS call occurs.

### P0  Browser workflow is not a release gate

**Impact:** the exact behaviours users see are unproven by the normal test command.

**Correct fix:** retain a fast unit suite, but add a mandatory browser-E2E CI job using the local fixture. It must cover both Mock Demo and a controlled live-like fixture; a failed/timeout job must return a final machine-readable report and fail the release gate.

**Acceptance proof:** CI publishes a completed result for browser tests, screenshots/traces on failure, and never reports success without a terminal pytest summary.

### P0  Test process lifecycle is unreliable

**Impact:** dots without a final summary can hide hanging servers, browsers, background tasks, threads, or database handles. That is not a pass.

**Correct fix:** identify non-daemon threads/tasks and fixture teardown leaks. Ensure Playwright, fixture HTTP servers, WebSocket clients, background scan jobs, executor pools, and SQLite connections are closed in `finally`/fixture teardown.

**Acceptance proof:** each pytest group exits with code 0 and a final summary in a clean process; repeat each group twice to prove no resource leak.

### P1  Status, telemetry, and recovery must have one source of truth

**Impact:** the UI can show Running, CRAWL_INTERRUPTED, empty counters, and retry controls together. Users cannot know whether a run is resumable or what was persisted.

**Correct fix:** make persisted `RunContext` the authority. UI status pills, CTA visibility, counters, active route, retry/resume availability, export state, Analyze unlock, and Report availability must derive from the same versioned state snapshot. Telemetry may enrich progress but may not create a competing lifecycle state.

**Acceptance proof:** each terminal state has one visible next action:

- `scanned`: **View Analysis**;
- `partial_scanned`: **Resume Crawl** and **Save/Export Partial Run**;
- `paused/auth expired`: **Refresh Session**, then **Resume Crawl**;
- `failed`: **Retry from Checkpoint** or **Start New Scan**;
- `executed`: **View Report** and **Save Baseline**.

### P1  Checkpoint, Saved Run, baseline, and regression semantics are unclear

**Impact:** users do not know what is stored, where it is stored, or which version replay uses.

**Correct fix:** add an artifact manifest to Saved Runs and Report. Show run ID, revision, target, created/updated timestamp, checkpoint version, selected modules, test-plan version, execution ID, baseline ID, storage type, and download links. A checkpoint is a versioned recovery snapshot of a run; a Saved Run is the durable parent lifecycle record; a baseline is an approved immutable comparison snapshot; replay executes a chosen baseline/plan against the target.

**Acceptance proof:** a user can create a checkpoint, reload the browser, open Saved Runs, see the same checkpoint version, resume it, save a baseline, run a replay, and identify both run revisions in the comparison.

### P1  Security-test reliability and positioning

**Impact:** live DNS dependence causes false-negative tests, while broad DAST claims would overstate the feature.

**Correct fix:** mock DNS in unit tests for known public hosts; retain live DNS in separately labeled integration tests. Keep SSRF guardrails strict. Clearly label current capability as bounded passive/security review, not full active DAST. Active XSS/SQLi testing must remain explicit, authorized, scope-bound, rate-limited, and restricted to approved non-production targets.

**Acceptance proof:** unit tests pass offline with deterministic DNS stubs; integration tests separately report live-network status; product copy distinguishes passive review from active DAST.

## V2 completion definition

V2 is complete only when all of these are true:

1. Mock Demo works completely offline and creates the same evidence shape and lifecycle artifacts as a fixture-backed live run.
2. Every state transition is persisted, idempotent, and visible after a refresh.
3. Browser E2E is mandatory in CI and returns terminal summaries, traces, and failure screenshots.
4. Planner context counts match Analyze evidence counts for the same run revision.
5. Requested testcase count produces the requested grounded catalogue when enough safe evidence exists, otherwise it gives a visible, specific shortfall reason.
6. Uploaded Excel testcases execute line by line and export the user-selected result format without losing source-row lineage.
7. Report, checkpoint, Saved Run, baseline, and replay show linked artifacts and a single understandable lifecycle.
8. Security Review makes no unverified XSS, SQLi, or OWASP coverage claim.

## Recommended implementation order

1. Fix mock-aware validation and add its browser-to-report regression test.
2. Stabilize teardown/background-job lifecycle and restore final summaries for all suites.
3. Implement canonical run-state/recovery CTA rules.
4. Build the artifact manifest and visible saved-run lineage.
5. Add mandatory browser CI and report artifacts.
6. Finish planner-count, Excel-row-lineage, and report/replay acceptance coverage.

## Claim language for the next demo

Use this: **PiOne Testmate captures browser-observed UI evidence and turns it into traceable, safer functional test plans.**

Avoid this until the gates above pass: **fully autonomous QA**, **complete OWASP coverage**, **full DAST**, **100 percent functional coverage**, or **production ready for any application**.
