# OmniTest V3 Pre-Tara Completion Task

## Mission

Finish and release-prove the OmniTest V3 assurance foundation before any Tara/chatbot integration. Preserve the existing V2 workflow exactly when `V3_ASSURANCE_ENABLED` is disabled. Do not redesign, bypass, or weaken V2 behavior to make V3 tests pass.

## Non-negotiable rules

1. Work only on the V3 branch; do not change the V2 release contract.
2. Keep V3 explicitly feature-gated and default-off.
3. Never use real customer credentials, production mutations, unrestricted SQL, or destructive API calls.
4. Treat `pass`, `fail`, `blocked`, and `inconclusive` as different outcomes.
5. A unit test is not browser proof. A service implementation is not release proof.
6. Do not claim completion without an evidence ledger containing commands, timestamps, run IDs, screenshots, and artifact paths.
7. Do not commit until all required gates pass or a documented blocker is accepted by the project owner.

## Phase 0 — Baseline and V2 protection

### Tasks

- Confirm branch, clean working tree policy, and current commit.
- Inventory all V3 files, routes, feature flags, database tables, reports, and UI controls.
- Run the V2 workflow with V3 disabled.
- Verify V2 scan, interactive checkpoint, execution, retry/resume, saved run, regression replay, artifacts, JSON/YAML/Excel exports, and report navigation.
- Capture a baseline test report before changing V3 configuration.

### Exit gate

V2 behavior passes with `V3_ASSURANCE_ENABLED` unset or false. Any V2 regression stops the task.

## Phase 1 — API contract ingestion

### Inputs

Use local deterministic fixtures first, then safe public read-only fixtures where network access is available:

- OpenAPI 3.0 JSON
- OpenAPI 3.1 YAML
- Swagger 2.0 JSON
- Postman Collection v2.1
- malformed document
- missing `paths`
- invalid server URL
- URL with credentials
- private/loopback/cloud-metadata URL

### Verify

- Stable source-order operation parsing.
- Base URL and server resolution.
- SSRF protection for DNS, private IPv4/IPv6, loopback, metadata, redirects, and embedded credentials.
- Duplicate operation IDs and duplicate routes.
- Unsupported media types and malformed schemas.
- Clear user-facing validation errors.
- No secret values in DOM, URL, logs, reports, or database.

### Exit gate

Each fixture returns a truthful structured result and the rendered UI shows the same status and operation list.

## Phase 2 — Safe API execution

### Verify

- Explicit user override → contract example → schema default → deterministic safe synthetic value precedence.
- Required path, query, header, cookie, and body parameters.
- Type, enum, format, nullable, array, nested-object, and boundary values.
- Authentication modes: bearer, API key, basic, OAuth metadata without persisting secrets.
- Status-code assertions.
- Required-field assertions.
- JSON Schema Draft 7 assertions.
- Header and latency assertions.
- Timeout, DNS failure, TLS failure, 401, 403, 404, 409, 429, 500, malformed JSON, and partial response handling.
- Mutation methods disabled by default.
- Mutation execution requires staging/test environment plus exact confirmation phrase and feature flag.
- Per-operation checkpoint, retry, resume, cancellation, and idempotency.
- One request ID cannot create duplicate execution.

### Exit gate

At least one operation is proven as pass, fail, blocked, and inconclusive using deterministic fixtures. Every result has evidence and a correlation ID.

## Phase 3 — API report and lineage

### Verify

- Generate the required 10-sheet Excel report.
- Confirm operation order, request parameters, execution results, assertions, failures, authorization checks, evidence, run metadata, and coverage gaps.
- Verify uploaded/derived inputs are traceable to the run.
- Verify secrets are absent from every cell and report metadata.
- Re-download the same report and confirm deterministic run identity.
- Confirm browser download completion and file readability.

### Exit gate

The rendered UI download succeeds and the workbook contains all required sheets with truthful values.

## Phase 4 — Dashboard/API validation

### Verify

Create a deterministic dashboard fixture with a matching and intentionally mismatched API response. Validate:

- KPI values
- table rows and totals
- chart series and labels
- filters
- date ranges
- timezone conversion
- currency and percentage normalization
- rounding tolerance
- stale data
- missing data
- filter mismatch

### Exit gate

The system identifies a deliberate mismatch, classifies it correctly, and shows the dashboard/API evidence. A matching case must not be reported as a defect.

## Phase 5 — Database cross-read validation

### Rules

- Read-only test/staging database only.
- Approved query definitions only.
- Allow only `SELECT` or `WITH ... SELECT`.
- Reject semicolons, comments, PRAGMA, ATTACH, INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, VACUUM, and arbitrary SQL.
- Use secret references, never plaintext credentials.
- Redact connection details, tokens, and sensitive row values.

### Verify

- Database row counts, aggregates, status, freshness, timestamps, and filtered values.
- Database ↔ API ↔ UI parity.
- Classification: perfect match, rounding/currency normalization, dashboard rendering defect, API transformation error, stale data, filter mismatch, timezone conversion error.
- Query timeout and connection failure behavior.
- Tenant/environment boundary enforcement.

### Exit gate

One matching and one intentionally inconsistent three-way fixture are proven with evidence and correct classification.

## Phase 6 — Recovery, reliability, and security

### Verify

- Restart API during ingestion.
- Restart worker during execution.
- Resume from checkpoint.
- Retry after a transient failure.
- Duplicate webhook/job submission.
- Concurrent requests for the same plan.
- Expired session and re-authentication.
- Lost browser telemetry.
- Long-running execution beyond the old short timeout.
- Cancellation and cleanup.
- Artifact retention and orphan cleanup.
- Tenant isolation and authorization checks.
- Log redaction and report redaction.
- Rate limiting and bounded retries.
- Prompt/page-content injection resistance: untrusted target text must never become an instruction to bypass policy.

Use safe static security checks, dependency audit, targeted SSRF tests, secret-leak scans, and non-destructive DAST only against approved local fixtures. Do not run destructive VAPT against public systems.

### Exit gate

Recovery is restart-safe, duplicate-safe, bounded, observable, and produces no misleading success state.

## Phase 7 — Rendered-browser release proof

Run the actual browser at the configured test port with V3 explicitly enabled. Capture screenshots for:

1. Assessment mode selection.
2. Contract ingestion and operation list.
3. Safe API execution result.
4. Failure/blocked classification.
5. Excel report download confirmation.
6. Dashboard/API mismatch evidence.
7. Database cross-read result.
8. Retry/resume recovery.
9. V2 flow with V3 disabled.

The proof report must include URL, run ID, environment, test input hash, expected result, actual result, screenshot path, artifact path, and whether the evidence is browser, API, unit, or source-only.

## Phase 8 — CI and release gate

- Run focused V3 tests.
- Run V2 regression with V3 disabled.
- Run compile/type/lint checks.
- Run dependency and secret scans.
- Run the complete suite on a host where local async sockets work.
- Upload JUnit XML, logs, screenshots, downloaded reports, and evidence ledger as CI artifacts.
- Fail the pipeline on any unclassified error, missing artifact, secret leak, or false-success state.

## Required final deliverables

1. `docs/OMNITEST_V3_PRE_TARA_PROOF_<date>.md`
2. Test evidence ledger.
3. JUnit XML and CI log artifact paths.
4. Browser screenshots.
5. Sample 10-sheet Excel report.
6. API fixture set.
7. Database parity fixture set.
8. Updated V3 release notes.
9. A clear list of verified, blocked, and not-started items.

## Tara entry criteria

Do not start Tara integration until:

- V2 regression passes with V3 disabled.
- API ingestion and execution are browser-proven with V3 enabled.
- Pass/fail/blocked/inconclusive outcomes are truthful.
- Excel report download is browser-proven.
- Dashboard/API and DB/API/UI validation are browser-proven.
- Retry, restart, resume, cancellation, and duplicate protection pass.
- No secrets appear in logs, reports, URLs, screenshots, or database.
- CI produces complete evidence on a socket-capable runner.

## Agent execution prompt

> Act as the senior backend engineer, frontend engineer, QA lead, security architect, and release manager for Pi One OmniTest. Execute this document phase by phase. Start by inspecting the current repository and identifying what is truly implemented. Do not assume that a passing unit test, a visible button, or a source file proves the feature works. Use deterministic local fixtures, safe read-only API fixtures, the rendered browser, API calls, SQLite/test database reads, Playwright evidence, pytest, compileall, dependency scanning, secret scanning, and CI artifact collection. Preserve V2 exactly when V3 is disabled. Enable V3 only in a separate test process for live proof. For every scenario record expected result, actual result, classification, run ID, correlation ID, evidence type, screenshot/artifact path, and blocker. Fix only confirmed defects, then rerun the affected tests and the regression suite. Never bypass SSRF, auth, mutation, SQL, redaction, or tenant boundaries to make a test green. Stop after each phase, report the gate result, and do not commit until all exit gates and Tara entry criteria pass. If a host limitation prevents proof, document it precisely and move testing to a socket-capable runner rather than claiming success. Only after the pre-Tara release gate is green should you implement signed Tara webhooks, incident mapping, scenario authorization, one-ticket-one-job idempotency, result callbacks, and status synchronization.
