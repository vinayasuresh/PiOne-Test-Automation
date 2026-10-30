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

- [ ] Start from a clean branch and record `git status`.
- [ ] Run focused V2 tests for scan lifecycle, checkpoint recovery, generation,
  execution, exports and assessment-mode isolation.
- [ ] Map existing `ScanRun`, execution, checkpoint and artifact records that
  V3 must reference.
- [ ] Add a single `V3_ASSURANCE_ENABLED` feature flag, defaulting off.
- [ ] Document the existing V2 routes/UI contract that must not change.

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

- [ ] Add migrations and models for `support_incident`, `verification_job`,
  `verification_attempt`, `evidence_manifest` and `assignment_rule`.
- [ ] Link records to existing run IDs, execution IDs, checkpoint IDs and
  artifact IDs rather than duplicating V2 state.
- [ ] Enforce idempotency through a unique correlation ID and grouping key.
- [ ] Add repository transactions, lifecycle validation and redaction tests.
- [ ] Add incident/job API contracts behind the feature flag.

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

- [ ] Support approved OpenAPI or Postman contract registration.
- [ ] Register operations, approved auth fixtures and safe response rules.
- [ ] Validate schema, required fields, status code, auth boundary and latency.
- [ ] Persist a redacted `api_observation` linked to job/run/scenario/evidence.
- [ ] Add safe negative cases only for authorised staging environments.
- [ ] Export API observations in reports without exposing credentials.

### Exit criteria

- One selected endpoint produces a truthful pass/fail/blocked result.
- Request metadata, response validation, latency and evidence are visible and
  redacted.

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

- [ ] Define a data assertion scenario with route, UI selector, source API/read
  adapter, filters, timezone, freshness window and tolerance.
- [ ] Capture the visible KPI/table/chart value and the exact filter context.
- [ ] Read the approved API/read-only source.
- [ ] Normalize nulls, currency, percentages, rounding and time buckets.
- [ ] Classify stale data, missing data, filter mismatch, rounding mismatch and
  visual/source mismatch.
- [ ] Save a screenshot plus source comparison evidence.

### Exit criteria

- A dashboard metric comparison detects a deliberate mismatch and explains it
  with the UI value, source value, filters, tolerance and evidence.

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

## Phase 4 - Incident orchestration and controlled agent loop

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

## Phase 5 - Helpdesk and continuous-check prototype

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

## Phase 6 - V3 release hardening and client rehearsal

### Tasks

- [ ] Test happy paths across UI, API and dashboard-data assertions.
- [ ] Test duplicate incident, expired session, provider fallback, API timeout,
  evidence failure, worker interruption and stale data.
- [ ] Verify a passed UI check cannot hide a failed source-data assertion.
- [ ] Run focused V2 regression with V3 flag off and on.
- [ ] Run CI, `git diff --check`, migration tests and one browser workflow.
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
