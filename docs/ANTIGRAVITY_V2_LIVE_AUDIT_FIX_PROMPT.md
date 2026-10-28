# Antigravity Prompt for PiOne Testmate V2 Live Audit Fixes

Copy the prompt below exactly into Antigravity.

---

Act as a senior staff backend engineer, frontend engineer, SDET, and QA release lead. Work in the existing PiOne Testmate repository. Your task is to make the V2 workflow demonstrably reliable without redesigning, deleting, or bypassing the existing flow.

## Product purpose

PiOne Testmate is an evidence-grounded AI-assisted QA platform. It discovers safe UI controls in a browser, stores selector/XPath/page/context evidence, generates grounded functional tests, executes them, exports results, saves checkpoints/runs, and supports baselines/regression replay. Preserve the existing eight-step workflow:

1. Scan Application
2. Choose Modules
3. Manual Review
4. Crawl Progress
5. Analyze
6. Generate Tests
7. Execute
8. Report

Do not replace the architecture with mock screens. Do not weaken SSRF protection, authentication safety, scope controls, or action safety classification just to make tests pass. Preserve existing working behaviour and make only scoped, evidence-backed changes.

## Non-negotiable P0 defects to fix

### 1. Mock Demo is broken before its mock handler runs

Current reproduced behaviour:

- UI shows Mock Demo Mode Active and says it is offline/deterministic.
- Frontend sends `mock: true` but uses `https://example.test`.
- `ScanRequest.url` validates/resolves DNS before the router reaches `if request.mock`.
- The request fails with `Cannot resolve hostname 'example.test'`.
- Crawl then shows conflicting Running/CRAWL_INTERRUPTED/zero-counter UI and no durable run state.

Implement a mock-aware validation path.

Requirements:

- Generic live target validation must stay exactly as strict as it is for SSRF/IP/DNS safety.
- Only an explicit `mock: true` request may skip external DNS resolution.
- Mock requests must still validate allowed HTTP/HTTPS scheme and reject malformed/unsafe values.
- Use a deterministic synthetic origin or a dedicated safe mock-origin rule; do not make live validation permissive.
- The mock request must create a durable RunContext, checkpoint, scan result, telemetry terminal event, exports, and Saved Run exactly as the workflow expects.
- The full Mock Demo flow must work offline: Discover Modules -> Choose Modules -> Manual Review -> Crawl -> Analyze -> Generate Tests -> Execute -> Report.

Add unit and browser regression tests that prove no DNS request is needed for Mock Demo and that `/run/{run_id}/state` exists after scan completion.

### 2. Browser tests are not a release gate

`pytest.ini` excludes browser tests by default with `-m "not browser"`. Keep a fast default unit suite if necessary, but add a mandatory named browser-E2E command/CI job. It must return a final pytest summary and non-zero exit code on timeout/failure.

The browser gate must cover:

- Mock Demo complete workflow;
- controlled fixture/live-like scan;
- module selection;
- interactive checkpoint capture and continue/retry/end actions;
- refresh/reopen recovery;
- Analyze evidence visibility;
- planning context and testcase count;
- execution and report navigation;
- checkpoint, Saved Run, baseline, and regression replay;
- JSON/YAML/Excel/Markdown downloads or their artifact manifest.

Do not claim a group passed based only on printed dots. Ensure every job exits cleanly and prints a terminal pytest summary.

### 3. Stop test-suite hangs and resource leaks

Several larger suites emit passing dots but do not finish with a final summary. Diagnose and repair teardown/resource lifecycle.

Audit and close, in `finally` blocks or fixtures as appropriate:

- Playwright browser/context/page instances;
- local HTTP fixture servers;
- WebSocket connections/reconnect timers;
- background scan/test-generation/execution tasks;
- executor pools/threads;
- SQLite/database sessions;
- async event loops and pending telemetry tasks.

Add a repeat-run regression test or CI proof that targeted groups run twice in clean processes and each emits a final summary.

## P1 lifecycle and UX correctness

### 4. One server-truth lifecycle model

Persisted RunContext must be the authority for status, counters, current module/route, terminal state, retry/resume availability, Analyze unlock, Report unlock, export readiness, and CTA visibility.

Telemetry is supplementary; it must never create a competing lifecycle state.

Define and test these exact UI contracts:

- `scanned`: show `View Analysis` as the primary next action.
- `partial_scanned`: show `Resume Crawl` and `Save/Export Partial Run`.
- `paused` or auth-expired: show `Refresh Session`; after success, show `Resume Crawl`.
- `failed`: show `Retry from Checkpoint` and `Start New Scan` with failure reason and preserved artifacts.
- `executed`: show `View Report`, `Save Baseline`, and execution artifact links.

No UI may show Running and Failed/Interrupted at the same time unless a clear `recovery pending` state explains the relationship. Counters must derive from the same run revision and be monotonic unless explicitly labeled recalculated.

### 5. Make checkpoint/Saved Run/baseline/replay understandable

Add a visible Artifact Manifest in Saved Runs and Report:

- run ID and human session name;
- target origin and selected modules;
- run revision and timestamp;
- checkpoint version and recovery status;
- scan result artifact links;
- plan version/count/context revision;
- execution ID/status/results;
- baseline ID/version;
- replay source and comparison result.

Semantics must be exact:

- checkpoint = versioned recoverable snapshot within a run;
- Saved Run = durable parent record for the lifecycle;
- baseline = approved immutable comparison snapshot;
- replay = execution against an explicit saved plan/baseline.

Add tests covering save checkpoint -> browser refresh -> Saved Runs -> resume -> plan -> execute -> baseline -> replay -> report.

### 6. Planner count and evidence contract

The requested testcase value is a requested grounded catalogue size, not merely a display preference.

- If sufficient safe grounded evidence exists, generate exactly the requested number of independently executable cases.
- If there is insufficient safe evidence, return fewer only with a visible, specific reason that lists the available routes/actions and exclusions.
- Every planned navigation must preserve the canonical scanner URL, including query strings/fragments when they identify page state.
- Every locator/action/assertion must validate against the same immutable scan revision shown in Analyze.
- Show a pre-generation contract such as `3 routes, 22 targets, 1 parked action, 2 excluded`.
- Block generation when its evidence count/revision differs from the approved scan; provide a clear refresh/review action.

Add tests for query-string routes, duplicate product controls, requested count expansion, shortfall explanation, and stale run rejection.

### 7. Excel testcase upload and result lineage

When a user uploads Excel testcases:

- parse all valid rows in source order;
- preserve source workbook/sheet/row identity;
- execute each selected row/case line by line;
- distinguish invalid rows, skipped rows, failed rows, and passed rows;
- provide a user choice: system report format or Excel with results;
- export results with the original source row plus generated/executed evidence, status, timing, failure category, screenshot/artifact link, and run/execution IDs;
- do not silently collapse upload rows into fewer tests.

Add a fixture workbook with positive, negative, blank, malformed, duplicate, and dependency-ordered rows. Test parse -> plan -> execute -> exported result lineage.

## Security boundaries

Do not market the current bounded review as full DAST or complete OWASP coverage.

- Keep current SSRF guardrails strict.
- Unit tests for safe public hosts must mock DNS deterministically; do not require public DNS in unit tests.
- Keep live DNS checks in separately named integration tests.
- Keep active XSS/SQLi payload testing disabled unless explicit authorization, staging/non-production scope, allowlist, rate budget, and audit logging are present.
- Clearly label passive security review versus authorized active DAST.

## Required implementation approach

1. Inspect current code and existing uncommitted changes first. Do not overwrite unrelated work.
2. Write a concise implementation plan mapping each requirement to backend, frontend, tests, and acceptance evidence.
3. Make small coherent patches. Avoid broad refactors.
4. Add or update tests with every bug fix.
5. Run these checks and capture final summaries:
   - Python compilation;
   - `git diff --check`;
   - focused unit tests for every changed contract;
   - full fast suite;
   - explicit browser-E2E suite;
   - Mock Demo end-to-end browser journey;
   - controlled fixture live-like browser journey;
   - artifact/export inspection.
6. If an environment-only limitation blocks a test, prove the limitation, isolate it, and do not call the related behaviour passed.
7. Do not commit automatically. Present a final change summary, commands/results, remaining risks, and a list of files changed for review.

## Definition of done

The task is done only when all statements below are backed by automated or browser-visible proof:

- Mock Demo is fully offline and completes every workflow stage with persisted state.
- All lifecycle states have one unambiguous next action after a refresh.
- Browser E2E is a mandatory, cleanly terminating release gate.
- Checkpoint/Saved Run/baseline/replay show linked durable evidence.
- Planner counts and Analyze counts refer to the same immutable scan revision.
- Excel upload preserves every source row through execution/export.
- Security wording and tests accurately reflect bounded passive review versus authorized active DAST.
- No existing functional path regresses.

Use the audit file `docs/V2_LIVE_AUDIT_AND_REMEDIATION_2026-10-26.md` as the source of truth for the observed issues. Return evidence, not optimistic statements.
