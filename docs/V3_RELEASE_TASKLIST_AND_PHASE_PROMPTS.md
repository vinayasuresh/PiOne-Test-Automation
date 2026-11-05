# PiOne Testmate V3 Release Task List and Phase Prompts

## Purpose

This is the execution order for PiOne Testmate V3. The immediate release is a
small, demonstrable Client Assurance vertical slice: complaint or manual trigger
to a durable incident, approved verification, evidence, assignment and retest.

The first priority is **API contract testing and dashboard-data validation**.
They must be implemented before broader agent autonomy, external connectors or
IoT command testing.

## Non-negotiable rules for every phase

- Preserve the V2 scan -> modules -> crawl -> analyse -> generate -> execute ->
  report workflow.
- Use additive V3 models, routes and UI only; gate V3 surfaces behind an
  explicit feature flag.
- Never store secrets, raw Tara transcripts, cookies, authorization headers or
  sensitive API response fields in evidence, logs or exports.
- Agent actions must use registered scenarios and validated tools. No arbitrary
  generated Python, shell, SQL, mutation request or physical-device command.
- A test outcome is only `reproduced`, `not_reproduced`, `blocked` or
  `inconclusive`; infrastructure or authentication faults must not be reported
  as customer-product defects.
- Do not claim a phase complete without its tests and exit criteria.

## Phase 0 - V2 protection and architecture freeze

### Tasks

- [x] Start from a clean branch and record `git status`.
- [x] Run focused V2 tests for scan lifecycle, checkpoint recovery, generation,
  execution, exports and assessment-mode isolation.
- [x] Map existing `ScanRun`, execution, checkpoint and artifact records that
  V3 must reference.
- [x] Add a single `V3_ASSURANCE_ENABLED` feature flag, defaulting off.
- [x] Document the existing V2 routes/UI contract that must not change.

### Phase 0 evidence (2026-11-03)

- The working tree was inspected before this phase; the only pre-existing
  untracked item was the generated status PDF under `output/`, and it was not
  included in the phase commit.
- Focused non-browser V2 persistence checks passed: checkpoint save/resume,
  interrupted scan recovery, regression-suite duplicate protection, baseline
  comparison, DB status/fallback and credential redaction (`7 passed`).
- V3 feature-boundary tests passed (`3 passed`): the flag is disabled when
  absent, accepts only explicit true values, and exposes only additive version
  metadata.
- The compatibility contract is recorded in `docs/V3_V2_COMPATIBILITY_MAP.md`.
  The existing V2 scan, module, crawl, analyse, generation, execution and
  report flow remains the system of record.
- Browser-marked release-gate tests were not counted as Phase 0 proof in this
  Windows run because the local browser/async test harness did not complete
  within the diagnostic timeout. They remain a Phase 6 rendered-browser gate.

### Exit criteria

- V2 focused tests pass with the flag disabled.
- No V3 button, route or background work is visible when the flag is disabled.

### Implementation prompt

```text
Work only on PiOne Testmate V3 Phase 0. Preserve all V2 behaviour and do not
rewrite the existing eight-step workflow. First inspect the current FastAPI
routers, database models, repository layer, run context/checkpoint services,
execution artifacts and frontend state handling. Add one explicit V3 assurance
feature flag that defaults to disabled. Create an evidence-backed compatibility
map showing which V2 records V3 will reference. Add focused tests proving that
with the flag disabled, no V3 route/UI/background job changes the V2 flow.
Run the relevant tests and git diff --check. Do not build external integrations
or autonomous exploration in this phase.
```

## Phase 1 - Durable V3 assurance records

### Tasks

- [x] Add migrations and models for `support_incident`, `verification_job`,
  `verification_attempt`, `evidence_manifest` and `assignment_rule`.
- [x] Link records to existing run IDs, execution IDs, checkpoint IDs and
  artifact IDs rather than duplicating V2 state.
- [x] Enforce idempotency through a unique correlation ID and grouping key.
- [x] Add repository transactions, lifecycle validation and redaction tests.
- [x] Add incident/job API contracts behind the feature flag.

### Phase 1 evidence (2026-11-02)

- `support_incidents`, `verification_jobs`, `verification_attempts`,
  `evidence_manifests` and `assignment_rules` are additive V3 tables with an
  Alembic migration.
- `POST /v3/incidents/intake` is feature-flagged, sanitized, and idempotent by
  `correlation_id`; it creates one durable incident and one job only.
- Jobs enforce legal transitions and retain attempt/evidence history without
  starting or altering an existing V2 run.

### Exit criteria

- Replaying the same correlation ID returns the same incident/job without a
  duplicate run.
- A job tracks its state, attempt history, verdict and evidence manifest.

### Implementation prompt

```text
Implement PiOne Testmate V3 Phase 1 only. Add feature-flagged, additive database
models and migrations for support incidents, verification jobs, attempts,
evidence manifests and assignment rules. Link them to the existing V2 ScanRun,
checkpoint, execution and artifact records. Design an idempotent incident intake
contract keyed by correlation_id. Add strict state transitions and transaction
boundaries. Redact secrets and sensitive response data before persistence. Write
unit and API tests for duplicate delivery, invalid transitions, transaction
rollback and redaction. Do not change the V2 user flow or replace existing tables.
```

## Phase 2 - API contract testing (Priority 0)

### Tasks

- [x] Support approved OpenAPI or Postman contract registration.
- [x] Register operations, approved auth fixtures and safe response rules.
- [x] Validate schema, required fields, status code, auth boundary and latency.
- [x] Persist a redacted `api_observation` linked to job/run/scenario/evidence.
- [x] Add safe negative cases only for authorised staging environments.
- [x] Export API observations in reports without exposing credentials.

### Phase 2 evidence (2026-11-02)

- `api_contracts`, `api_operations` and `api_observations` are additive V3
  tables with an Alembic migration. The submitted source document is used only
  for normalization and is never persisted.
- `POST /v3/api-contracts` accepts an approved OpenAPI or Postman document;
  it registers only same-origin operations. Write methods are visible as
  disabled records and cannot be executed by this phase.
- `POST /v3/api-operations/{id}/validate` validates exactly one registered,
  approved, read-only operation. It records truthful `pass`, `fail`, or
  `blocked` results for status, required fields, authorization boundary,
  timeout and latency conditions.
- Unauthenticated probes are allowed only when explicitly configured on a
  staging/test contract. Requests never accept raw credentials, arbitrary
  target URLs, bodies, fuzzing inputs or mutation methods.
- `GET /v3/api-contracts/{id}/observations/export` returns the redacted
  observation ledger; fixture references are identifiers only, never secrets.

### Exit criteria

- One selected endpoint produces a truthful pass/fail/blocked result.
- Request metadata, response validation, latency and evidence are visible and
  redacted.

### Phase 2 final matrix proof (2026-11-03)

- `python -m pytest automation_framework/tests/test_v3_api_contract_phase2.py
  automation_framework/tests/test_v3_api_contract_phase7.py -q` passed **17
  tests**. The matrix covers OpenAPI/Postman normalization, nested JSON Schema
  and local `$ref` handling, required fields, status codes, latency, timeout,
  authentication boundaries, traversal/parameter guards, mutation blocking,
  redaction, persistence and observation export.
- The combined V3 regression slice also passed **31 tests** across API contract,
  dashboard-data and orchestration coverage (`7 deselected` browser-marked
  cases). No browser proof is claimed by this phase; the rendered-browser gate
  remains Phase 5 work.
- The generated `output/` directory was intentionally left untracked and is
  not part of the release commit.

### Implementation prompt

```text
Implement PiOne Testmate V3 Phase 2: API contract validation. Build a
feature-flagged registry for approved OpenAPI/Postman operations and approved
test authentication fixtures. Implement safe, deterministic checks for status
codes, schema, required fields, response-time thresholds and authorised role
boundaries. Persist each observation with run ID, job ID, operation ID, scenario
version, redacted request metadata, redacted assertion result and evidence link.
Do not enable fuzzing, destructive requests, arbitrary URLs or raw secret export.
Add tests for pass, schema mismatch, authorization failure, timeout and redaction.
Keep V2 unchanged.
```

## Phase 3 - Dashboard-data validation (Priority 0)

### Tasks

- [x] Define a data assertion scenario with route, UI selector, source API/read
  adapter, filters, timezone, freshness window and tolerance.
- [x] Capture a supplied visible KPI/table/chart value and its exact filter context.
- [x] Read the approved API/read-only source.
- [x] Normalize nulls, currency, percentages and rounding.
- [x] Classify stale data, missing data, filter mismatch, rounding mismatch and
  visual/source mismatch.
- [x] Persist source comparison evidence and optionally link an existing V2
  evidence manifest. Automatic browser screenshot capture remains a later
  browser-agent integration, not a claim of this phase.

### Exit criteria

- A dashboard metric comparison detects a deliberate mismatch and explains it
  with the UI value, source value, filters, tolerance and evidence.

### Phase 3 verification evidence (2026-11-03)

- `python -m pytest automation_framework/tests/test_v3_dashboard_data_phase3.py
  automation_framework/tests/test_v3_dashboard_data_phase8.py -q` passed **6
  service and persistence tests**; **7 browser-marked tests were deselected**.
- Verified matching values, currency and percentage normalization, rounding
  tolerance, stale data, missing source data, filter mismatch, visual/source
  mismatch, unsafe source-operation blocking, redacted observation persistence,
  and timezone normalization.
- The service-level exit criteria are met. Rendered-browser capture and live
  dashboard proof remain explicitly unverified and belong to Phase 5 release
  proof; this phase does not claim them as complete.

### Implementation prompt

```text
Implement PiOne Testmate V3 Phase 3: dashboard-data validation. Add a
feature-flagged data assertion engine that compares a dashboard UI value with an
approved API or read-only adapter result under identical filter, date-range,
timezone and freshness conditions. Persist selector, route, filter context,
expected value, actual UI value, source value, tolerance, normalization method,
classification and screenshot/source evidence. Start read-only; do not permit
arbitrary SQL or production writes. Add tests for matching values, rounding
tolerance, stale data, missing data and filter mismatch. Preserve V2 behaviour.
```

## Revised Phase 4 - API/database cross-read validation (Priority 0)

### Phase 4 implementation evidence (2026-11-03)

- Added `api/services/v3_database_assertion_service.py` with an approved-query
  adapter for test/staging databases. It accepts server-owned query definitions
  only, permits one `SELECT`/CTE read, binds declared parameters, caps evidence
  at 500 rows, and redacts sensitive values before returning evidence.
- Mutation statements, multi-statements, PRAGMA/admin commands, production
  environments, unknown parameters and missing parameters return `blocked`.
- Added API/database comparison classification for exact, tolerance-based and
  mismatched values without converting a source outage into a product failure.
- Added the `database_query_definitions` model and Alembic migration
  `f6a7b8c9d0e1_v3_database_query_definitions.py`. Registration is explicitly
  approved, test/staging-only, and public metadata never returns SQL.
- Added feature-gated routes for query registration, listing, lookup and one
  bounded read/compare operation under `/v3/database-queries`.
- Dashboard assertion scenarios can now reference an approved database query
  definition, preserving the API → dashboard → database lineage without
  changing existing API-only scenarios.
- Bound database definitions are now executed during scenario evaluation with
  transient declared parameters; API↔database comparison and the query ID are
  included in the persisted comparison lineage. Empty, unavailable and
  ambiguous database results remain blocked rather than becoming UI failures.
- Database driver errors are normalized before evidence export so SQL text,
  schema names and bound values are not echoed in API responses. Tolerance
  values are finite, non-negative decimals; invalid values are blocked.
- Proof: `python -m pytest
  automation_framework/tests/test_v3_database_assertion_phase4.py
  automation_framework/tests/test_v3_api_contract_phase2.py
  automation_framework/tests/test_v3_dashboard_data_phase3.py
  automation_framework/tests/test_v3_orchestration_phase45.py -q` passed
  **31 tests**. Deterministic Phase-7 security checks also passed (10 tests).

### Remaining Phase 4 wiring

- Add a rendered-browser flow proving registration, read, mismatch and blocked
  outcomes; service/API tests do not count as browser proof.
- Run the rendered-browser/dashboard proof in Phase 5; no browser proof is
  claimed by this service-level implementation.
- Production authorization for V3 routes is still a Phase-8 gate. The V3
  feature flag is disabled by default, but enabling it behind an internet
  boundary without tenant/RBAC authentication is not release-safe.

## Supporting implementation - Incident orchestration and controlled agent loop

### Current implementation status (uncommitted; validated 2026-11-03)

- V3 accepts a strictly validated, redacted incident intake with a correlation
  ID and creates exactly one durable incident/job pair for duplicate delivery.
- Only a pre-registered, approved API-contract or data-assertion scenario can
  be bound. Role, target-origin, security-mode and scope checks run before an
  attempt is created.
- The bounded loop records observe, policy-check, execute, verify and conclude
  events; it persists an attempt and evidence manifest for both policy blocks
  and terminal executions.
- Retry is limited by the job's recorded `retry_limit`, uses the same approved
  scenario, and retains all earlier attempts/evidence. It is not a generic
  browser-agent retry.
- Important boundary: this slice references V2 run/execution/checkpoint IDs
  when supplied, but it does not launch a V2 browser crawl. Browser-driven
  reproduction remains a later integration gate, not a completed claim.

### Tasks

- [ ] Accept Tara's sanitized structured incident request.
- [ ] Select a registered scenario by feature, environment, role and target.
- [ ] Validate target scope, role, authorization, test data and policy.
- [ ] Link exactly one verification job to the existing Testmate engine.
- [ ] Implement a bounded loop: observe -> approved action -> execute -> verify
  -> record -> conclude.
- [ ] Make retry/recovery idempotent and preserve attempts/evidence.
- [ ] Return truthful terminal outcomes and technical categories.

### Exit criteria

- A sample Tara incident triggers one approved scenario and produces linked V2
  evidence without a duplicate run.
- Auth expiry, outage, missing data and unsupported request become blocked or
  inconclusive.

### Implementation prompt

```text
Implement PiOne Testmate V3 Phase 4: Tara incident orchestration and bounded
agent verification. Accept only a sanitized structured incident request with a
correlation ID, target, role, expected/actual behaviour and registered scenario
reference. Validate scope and policy before creating an idempotent verification
job linked to the existing V2 lifecycle. The planner may select only registered
scenarios and validated UI/API/data tools. Record every attempt, action,
assertion, outcome and evidence manifest. Return reproduced, not_reproduced,
blocked or inconclusive; never label an expired session or tunnel outage as an
application defect. Add tests for duplicate intake, target denial, missing role,
auth expiry, target outage and worker interruption. Do not remove manual mode.
```

## Supporting implementation - Helpdesk and continuous-check prototype

### Current implementation status (uncommitted; validated 2026-11-03)

- The feature-flagged Assurance Desk provides an incident queue, click-through
  incident detail, expected/actual evidence context, owner, next action and
  recorded verification timeline.
- Assignment rules are deterministic, manual assignment remains available,
  and reproduced outcomes invoke the rules without claiming AI ownership.
- Saved monitoring definitions are metadata only. An operator must explicitly
  confirm before a definition creates one reviewable verification job; no
  scheduler, timer or unattended execution is started.
- A repeated manual trigger groups into the current reproduced/open incident
  for the same monitoring key. A later `not_reproduced` result resolves it.

### Tasks

- [ ] Build a feature-flagged incident queue and details view.
- [ ] Show incident summary, owner, priority, timeline, tested conditions,
  expected/actual result, UI/API/data evidence and next action.
- [ ] Implement deterministic assignment rules and manual reassignment.
- [ ] Add a manual trigger for one saved critical journey.
- [ ] Persist schedule metadata only; do not claim a production scheduler.
- [ ] Group repeated failures and show recovery when a later check passes.

### Exit criteria

- An operator understands an incident without raw logs.
- A repeated failed check groups into one incident and a later pass records
  recovery.

### Implementation prompt

```text
Implement PiOne Testmate V3 Phase 5: a feature-flagged internal Helpdesk view
and controlled continuous-check prototype. Build incident queue/details screens
using the durable V3 APIs. Show owner, priority, state timeline, Tara summary,
expected/actual behaviour, tested conditions, UI/API/dashboard evidence and
next action. Implement deterministic assignment rules plus manual reassignment.
Allow an operator to manually trigger one saved critical journey and persist a
schedule definition, but do not present it as a production scheduler. Group
repeated failures by scenario, target and environment; record recovery on a
subsequent pass. Add UI/API tests and preserve V2 screens and navigation.
```

## Supporting implementation - V3 release hardening and client rehearsal

### Validation evidence to date (uncommitted; 2026-11-03)

- `29 passed`: focused V3 durable-assurance, API-contract, dashboard-data,
  feature-flag, orchestration/helpdesk, retry and V2 execution-recovery tests.
- `13 passed`: focused V2 test-planning/module-selection coverage.
- `7 passed`: V2 checkpoint, resumable-run, regression-suite, baseline and
  credential-redaction persistence coverage.
- `16 passed`: V2 URL/auth/session/refresh/export lifecycle coverage.
- Fresh SQLite migration chain upgraded successfully through revision
  `d4e5f6a7b8c9`; `assurance_scenarios`, `monitoring_schedules`, incident and
  job tables were verified.
- Browser executable readiness was directly verified as ready. The real
  application was then rendered at `http://127.0.0.1:8000`, and the bounded
  incident-to-resolution workflow, duplicate intake protection, and restart
  recovery were verified. The complete default suite passed outside the
  restricted local socket environment; see the release-proof evidence below.

### Tasks

- [x] Test the bounded Assurance Desk happy path across rendered UI and API.
- [x] Test duplicate incident intake and durable restart recovery.
- [ ] Complete the expanded dashboard-data matrix plus expired session, provider fallback, API timeout,
  evidence failure, worker interruption and stale data.
- [ ] Verify a passed UI check cannot hide a failed source-data assertion.
- [x] Run focused V2 regression and the default V2 + V3 suite.
- [x] Run CI-equivalent checks, `git diff --check`, migration coverage and one real browser workflow.
- [ ] Prepare a seeded-defect demo and document limitations honestly.

### Exit criteria

- The selected demo path has no showstopper.
- Every terminal result has linked evidence and the V2 flow remains intact.

### Implementation prompt

```text
Execute PiOne Testmate V3 Phase 6 release verification. Do not add new product
features. Test the feature-flagged vertical slice end to end: seeded Tara
incident -> durable ticket/job -> API and dashboard-data validation -> evidence
-> assignment -> controlled retest/recovery. Exercise duplicate delivery,
authorization denial, expired session, provider fallback, API timeout, stale
data, broken artifact link and interrupted worker. Confirm a UI pass cannot mask
a source-data failure. Re-run focused V2 functional and security-mode checks
with V3 disabled and enabled. Run unit/integration tests, one browser workflow,
CI-equivalent checks and git diff --check. Produce a release evidence report
listing passed checks, failures, known limitations and an explicit go/no-go.
```

## Post-Monday roadmap

### Phase 6 release-proof evidence (2026-11-03)

- Rendered the real application at `http://127.0.0.1:8000` with the current source and V3 feature flag enabled.
- Created incident `23d2c4f797bc47feba770adb55a073f6` in the Assurance Desk, selected an approved bounded API scenario, and executed job `9402a6122a47443887f5b679a98fe3f7`.
- The rendered detail showed `reproduced`, `unexpected_status_code`, owner `Reporting Engineering`, one attempt, and one evidence manifest. Retry remained disabled because the result was terminal and reproducible.
- Duplicate intake replay returned `existing` and retained one incident with one job (`6dfa48858d7b46088b59ec4a70acd166`).
- After stopping and restarting the API, both incidents and jobs were recovered from SQLite with their outcome, owner, attempt, and evidence links intact.
- Targeted orchestration tests: `8 passed`. Default CI-equivalent suite: `216 passed, 74 deselected` by the configured non-browser marker policy. Live `/health` reported `browser_ready: true`.
- The restricted Windows test environment could not create Starlette's local socket pair; running the same suite outside that restriction passed. This was an environment boundary, not an application assertion failure.

Phase 6 is release-proof complete for the bounded vertical slice. The next functional priority is API testing and dashboard-data validation, followed by Tara/helpdesk connectors and production scheduling.

### Pre-Tara foundation slices implemented (2026-11-04)

The next three implementation slices are now present in the feature-gated V3
working tree. They extend the existing interactive-learning flow without
changing the V2 browser workflow:

- **Recovery-safe worker lifecycle:** stale interactive sessions can be marked
  `recovery_required`, retain their last checkpoint, and be restarted through a
  controlled retry/resume action. Terminal sessions are not reopened.
- **Bounded interaction evidence:** manual clicks, inputs, selections,
  navigation and assertions can be recorded against an allowed target origin
  with route, CSS selector, XPath, source and page metadata. Input text is
  stored only as a SHA-256/length summary; secrets, raw values and page HTML are
  never persisted.
- **Focused release gate:** feature-flag, evidence-lineage, recovery and
  cross-origin rejection checks are covered by deterministic tests.

Verification completed for this slice:

- `compileall`: passed.
- Interactive phase tests: `9 passed`.
- Focused release-gate tests: `4 passed`.
- V3-focused suite: `77 passed, 7 deselected`.
- Fresh SQLite Alembic upgrade through the new evidence migration: passed.
- `git diff --check`: passed.

The rendered-browser proof is still an explicit follow-up gate because this
Windows host currently cannot expose the local app socket to the browser
bridge. No browser pass is claimed from these deterministic service tests. A
socket-capable environment must still verify the visible multi-module flow,
retry/resume interaction, evidence display and V2 regression with V3 disabled
before Tara integration begins.

### V3.1 - Coverage Mesh

- [ ] Excel row-by-row execution with no lost input rows.
- [ ] UI/API/security evidence in one unified finding model.
- [ ] Role/session matrix, visual diffs and traces.
- [ ] Authorised ZAP baseline/API profile; optional customer-owned Burp adapter.

### V3.2 - Learning Loop

- [ ] Evidence-ranked locator healing with human approval.
- [ ] Failure clustering and ownership suggestions.
- [ ] Model/prompt versioning, cost telemetry and deterministic replay.
- [ ] Test-impact analysis and evidence-based coverage score.

### V3.3 - Team and Scale

- [ ] RBAC, tenant/project isolation and encrypted secret storage.
- [ ] Distributed workers, concurrency limits, cancellation and quotas.
- [ ] Production schedules, webhooks and approved Jira/TestRail/GitHub/Teams
  connectors.
- [ ] Retention, deletion, audit policy and signed report manifests.
