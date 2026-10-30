# PiOne Testmate V3 — Client Assurance Release Blueprint

**Owner:** Business Analysis Lead, Development Lead and Delivery Manager
**Release target:** Monday, 2 November 2026
**Delivery rule:** Ship one evidence-backed vertical slice. Do not label an unfinished enterprise platform as complete.

## 1. Product decision

PiOne Testmate V3 is an **evidence-first assurance platform**. It turns a customer complaint, scheduled journey, deployment event or approved IoT signal into an authorized verification job, an honest verdict and evidence for the right owner.

```text
Tara / monitoring signal → ticket + verification job → policy and scenario
                                               ↓
Helpdesk queue ← evidence + verdict ← Testmate UI/API/data worker
                                               ↓
                         owner fixes → controlled retest → customer update
```

The promise is **faster, repeatable evidence-backed triage**. It is not “AI fixes all bugs,” continuous deep crawling, or proof of root cause from one screenshot.

## 2. Monday release scope

### Must ship — priority 0

1. **API contract validation**
   - Import or register an approved OpenAPI/Postman contract.
   - Run safe checks: schema, required fields, status code, auth boundary and response-time threshold.
   - Link each API observation to a run, route/business feature, testcase and evidence artifact.
   - Redact credentials, cookies, authorization headers and sensitive response fields.

2. **Dashboard-data validation**
   - Compare a dashboard metric/chart/table value with an approved API or read-only query adapter result under the same filters, timezone and freshness tolerance.
   - Persist expected value, actual UI value, source value, selector, source reference, tolerance and screenshot.
   - Classify stale data, missing data, filter mismatch, rounding mismatch and visual value mismatch.
   - Start read-only; production writes and arbitrary SQL are out of scope.

3. **Tara incident to verified ticket flow**
   - Accept a sanitized structured incident from Tara.
   - Create one durable ticket and idempotent verification job.
   - Run a pre-approved UI/API/dashboard scenario using an approved role.
   - Return `reproduced`, `not_reproduced`, `blocked`, or `inconclusive`.
   - Show an internal Helpdesk Incident Queue with owner, evidence and next action.

4. **One controlled continuous-check prototype**
   - Manually trigger a saved critical journey. Persist a schedule definition but do not claim a production scheduler.
   - Group repeated failures by scenario/target/environment and record recovery when a later run passes.

### Must preserve

- V2 scan → module → crawl → analyse → generate → execute → report flow.
- Current run IDs, reports, checkpoints, regression baselines and assessment-mode isolation.
- SSRF, same-origin and destructive-action protections.
- Existing manual-interaction mode. Agent mode must not silently pass a human checkpoint.

### Deferred after Monday

- Multi-tenant SaaS, SSO, full RBAC, production secret vault and retention policy.
- Free-form autonomous exploration of customer production environments.
- Physical IoT command/control testing; begin only with portal and read-only telemetry validation.
- Production Teams, Jira, ServiceNow or email connectors.
- Full DAST, claimed OWASP coverage, automatic root-cause analysis or self-healing without approval.

## 3. Functional definition of done

| Capability | Demonstrable result |
|---|---|
| API contract test | A selected endpoint yields a pass/fail with redacted request metadata, response validation and evidence link. |
| Dashboard-data test | A dashboard figure/table cell is compared with its declared source under the same filters and tolerance. |
| Incident intake | Replaying a Tara correlation ID returns the original ticket/job and starts no duplicate run. |
| Verification | A job links to Testmate run ID, scenario version, role, target, verdict, category and evidence manifest. |
| Helpdesk | Operator can see ticket, timeline, owner, tested conditions, evidence and next action. |
| Honest failure | Auth expiry, target outage, unsupported complaint and missing test data become `blocked` or `inconclusive`, never a green pass. |
| V2 protection | Focused V2 regression checks pass without a changed user flow. |

## 4. Required data model and contracts

Additive V3 records must link to existing `ScanRun`, checkpoint, execution-result and artifact records; do not replace them.

| Record | Required fields |
|---|---|
| `support_incident` | ticket ID, correlation ID, sanitized summary, affected feature, reporter role, expected/actual behaviour, priority |
| `verification_job` | incident ID, Testmate run ID, scenario version, target, role, mode, state, retry policy, grouping key |
| `verification_attempt` | job ID, attempt number, worker ID, start/end, terminal outcome, failure category, event sequence |
| `api_observation` | job/run ID, operation ID, redacted request metadata, response status, schema assertion, latency, result |
| `data_assertion` | dashboard route, UI selector, source type/reference, filter context, timezone, expected/actual, tolerance, result |
| `evidence_manifest` | artifact ID, run ID, source step, checksum, content type, storage location, retention expiry |
| `assignment_rule` | product/feature/service, owner team, severity threshold, escalation window |

### Minimum Tara incident request

```json
{
  "correlation_id": "tara-incident-unique-id",
  "ticket_reference": "CAT-123",
  "affected_feature": "Monthly report download",
  "expected_behavior": "A valid XLSX file downloads",
  "actual_behavior": "Download button returns an error",
  "authorized_target": "https://staging.example.com",
  "permitted_role": "standard_user",
  "scenario_reference": "reports.download.v1"
}
```

Never persist raw conversation transcripts, passwords, cookies or access tokens in this request, prompts, report exports or logs.

## 5. Agent policy

The agent loop is **observe → select approved action → execute → verify → record → conclude**.

- The planner may choose only from a registered scenario library and validated browser/API/data tools.
- No arbitrary generated Python, shell commands, SQL or physical-device commands.
- Each test needs expected behaviour and a verifiable oracle; an LLM opinion is not an oracle.
- Browser/API/data assertions run under declared identity, scope, environment and timeout.
- Ambiguity means `inconclusive`; unavailable dependencies mean `blocked`.
- Model failure falls back to a deterministic plan only where the saved scenario supports it.

## 6. API and dashboard-data design

### API testing ladder

1. Register OpenAPI/Postman operations and an approved auth fixture.
2. Validate request/response schema, required fields and status codes.
3. Add business assertions using approved expected values or fixtures.
4. Compare UI network observations against declared operations.
5. Add safe negative tests only for authorized staging: invalid format and unauthorized-role boundary.

Do not send fuzzing payloads, mutation requests or destructive calls by default.

### Dashboard-data validation ladder

1. Capture route, filter state, date range, timezone and UI selectors.
2. Obtain a trusted source from an approved API or read-only query adapter.
3. Normalize nulls, currencies, percentages, rounding and time buckets.
4. Compare value and freshness using scenario-defined tolerance.
5. Save screenshot, API/query evidence and the comparison result.

Example: “Total assets” on `/assets` must match `GET /api/assets/summary` for the same tenant, active filters and reporting timestamp. A difference is a **data-integrity finding**, not merely a UI failure.

## 7. Delivery schedule to Monday

### Friday — architecture freeze and API/data foundation

- Inspect existing models, repository layer, run state, router, exports and frontend flow.
- Add migrations/models and a feature flag for V3 Assurance.
- Finalize incident, job, API observation and data assertion contracts.
- Implement OpenAPI/Postman registration, one safe endpoint test and one dashboard metric comparison.

**Exit:** One API test and one dashboard comparison run locally with persisted evidence.

### Delivery ownership and checkpoints

| Workstream | Accountable role | Friday output | Monday proof |
|---|---|---|---|
| Product and business analysis | BA Lead | Scenario catalogue, acceptance oracle, severity and ownership mapping | Demo narrative matches shipped behaviour. |
| API and data assurance | Backend/API Lead | Approved contract registry and source-data adapter | Contract/data mismatch produces an explainable result. |
| Browser and evidence | QA Automation Lead | UI selector/filter capture and evidence manifest linkage | UI, API and source-data evidence agree or the difference is visible. |
| Incident and helpdesk UX | Frontend Lead | Feature-flagged queue/details workflow | Operator can assign, investigate and retest one incident. |
| Reliability and release | DevOps/QA Lead | Test matrix and demo environment readiness | Release gate records pass, fail and blocked evidence. |

The Delivery Manager runs two short gates each day: **midday integration check** and **end-of-day evidence check**. Work that lacks a verifiable acceptance result moves to the post-Monday backlog; it does not enter the demo path.

### Saturday — durable orchestration and Testmate linkage

- Implement idempotent incident ingestion and job-status endpoints.
- Link one verification job to the existing Testmate lifecycle without rewriting V2.
- Persist attempt history, verdict and evidence manifest atomically.
- Implement authorization, role, target-scope, scenario and test-data checks.
- Test duplicate delivery, invalid scope, missing role and target outage.

**Exit:** One incident produces exactly one durable job and linked run.

### Sunday morning — helpdesk and operator experience

- Build incident queue/details behind the V3 feature flag.
- Show Tara summary, expected/actual, state timeline, owner, API/data/UI evidence and next action.
- Add deterministic assignment and manual reassignment.
- Add manual trigger for one saved health-check and failure grouping key.

**Exit:** An operator can understand the result without reading raw logs.

### Sunday afternoon — verification, recovery and hardening

- Exercise UI, API and dashboard-data happy paths.
- Test expired session, provider fallback, API timeout, evidence-link failure, duplicate incident and worker interruption.
- Confirm a passed UI check cannot mask a failed source-data comparison.
- Confirm V2 functional-only/security-only mode behaviour remains unchanged.

**Exit:** All result states are truthful and evidence links work.

### Monday — release gate and client rehearsal

- Run focused unit/integration tests, browser workflow and `git diff --check`.
- Demonstrate a seeded defect: Tara intake → ticket → UI/API/data check → evidence → assignment → fixed retest.
- Record pass/fail/blocked evidence and known limitations.
- Freeze the demo build only if the selected path has no showstopper.

**Exit:** The team can give a credible demo without overstating capability.

## 8. Risks and decisions

| Risk | Decision |
|---|---|
| Deadline encourages feature bloat | Ship one vertical slice; defer integrations and scale layers. |
| UI says completed while data is wrong | Data assertion is a first-class result and blocks a false green outcome. |
| Tara creates duplicate incidents | Correlation-ID idempotency is mandatory. |
| Worker/process restart loses state | Persist jobs and attempts; process-local controls cannot be the source of truth. |
| Sensitive data reaches models/reports | Sanitize at ingress and redact evidence; never pass secrets to LLMs. |
| IoT claim exceeds proof | Demo portal/API/telemetry freshness only; physical commands need a separate safety plan. |

## 9. Refined Antigravity implementation task

```text
Implement the Monday V3 Client Assurance vertical slice in
docs/V3_CLIENT_ASSURANCE_DELIVERY_BLUEPRINT.md. PiOne Testmate is an existing
FastAPI + Playwright + vanilla frontend V2 repository. Preserve V2 behaviour,
run IDs, reports, checkpoint recovery, assessment modes and safety controls.
Gate every V3 endpoint and UI surface behind an explicit feature flag.

Priority order is mandatory:
P0 API contract validation and dashboard-data validation.
P1 Tara incident → durable verification job → existing Testmate execution linkage.
P2 Helpdesk queue/details and manually triggered health-check prototype.
P3 Only focused reliability hardening necessary for the above.

Before editing, inspect README.md, CHANGES.md, database models/repository, scan
router, run context, crawler, execution, artifact/export layers and tests. Provide
a short findings list. Make additive, reviewable changes; do not rewrite the crawler,
replace ScanRun, or redesign unrelated UI.

Implement a safe API-test layer that imports/registers approved OpenAPI or Postman
operations. Validate status, schema, required fields, response timing and declared
business assertions. Redact secrets. Block destructive methods unless an explicit
authorized staging profile permits them.

Implement dashboard-data assertions: capture route, selectors, filters, date range,
timezone and source reference; compare UI values to an approved API or read-only
data adapter using declared normalization and tolerance. Persist expected/actual
values, result and screenshot/evidence. A failed source-data assertion must be
visible as a data-integrity failure and must not be hidden by a passing UI step.

Add support_incident, verification_job, verification_attempt, api_observation,
data_assertion, evidence_manifest and assignment_rule records or compatible
additive equivalents. Link them to existing Testmate run IDs. Create idempotent
incident ingest/status APIs using correlation_id. Accept only sanitized incident
fields; never store raw Tara transcripts, passwords, cookies or tokens.

Use an approved scenario library. The agent may select only validated browser/API/
data tools. It may not execute arbitrary generated code or mutate production data.
Persist exactly one verdict: reproduced, not_reproduced, blocked or inconclusive,
plus failure category and evidence references.

Build a minimal V3 Helpdesk Incident Queue/Details UI showing ticket, owner, state
timeline, scenario version, run ID, expected/actual, API/data/UI evidence, verdict
and next action. Add deterministic assignment and manual reassignment. Add one
manually triggered health-check definition with grouping; do not claim a production
scheduler or external notification connector.

Test: API contract pass/fail and redaction; dashboard match/mismatch/filter/timezone
handling; duplicate incident; invalid target/authorization; unavailable target;
expired session; model fallback; worker interruption; broken artifact; V2 focused
functional/security-mode/report regressions. Run browser proof against an authorized
controlled target. State exactly what passed, failed and was blocked. Do not claim
physical-IoT testing, complete OWASP coverage, autonomous root-cause analysis or
production connectors unless actually delivered and tested.
```

## 10. Client demo line

> Tara understands the complaint and creates a ticket. PiOne Testmate checks the same journey, API contract and dashboard data with an approved test role. The responsible team receives evidence, not a vague complaint. The same saved check can then detect a recurrence and verify the fix.
