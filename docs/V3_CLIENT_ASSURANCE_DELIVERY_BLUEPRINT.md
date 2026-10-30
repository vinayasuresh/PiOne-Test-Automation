# PiOne Testmate V3 — Client Assurance Delivery Blueprint

**Owner:** Business Analysis Lead, Development Lead and Delivery Manager
**Created:** 30 October 2026
**Target:** Monday, 2 November 2026
**Status:** Delivery contract — scope must be signed off before implementation starts

## 1. Executive decision

PiOne Testmate V3 will become an **evidence-first incident verification and
continuous assurance platform**. It connects customer support, automated
verification and engineering ownership:

```text
Customer → Tara → ticket → Testmate verification job → evidence verdict
         → helpdesk owner → fix → controlled retest → customer update

Schedule/deployment/device signal → monitoring job ────────────────┘
```

The product does not promise that AI can understand every complaint or fix every
defect without people. It promises that an authorized complaint or monitoring
signal is turned into a controlled test, a reproducible outcome and useful
evidence for the right owner.

## 2. Monday definition of done

An enterprise-scale V3 release cannot responsibly be completed by Monday. The
Monday objective is a **demo-ready V3 vertical slice**, not a fake full release.
It must show one truthful, end-to-end customer incident from Tara intake to a
verified Testmate result and an assigned helpdesk record.

### In scope for Monday

- A documented integration contract for Tara, Testmate and helpdesk.
- A durable `verification_job` record linked to a Testmate run and a ticket.
- API endpoints or mocked adapters that accept a structured incident, create a
  queued job and return a correlation ID.
- One controlled functional scenario selected from a pre-approved scenario
  library, executed by the existing Testmate engine.
- Persisted evidence: tested URL, role, expected behaviour, actual behaviour,
  verdict, timestamps, screenshots/report links and failure category.
- Ticket update payload with one of: `reproduced`, `not_reproduced`, `blocked`,
  `inconclusive`.
- A basic Helpdesk / Incident Queue page, or a working mock page, showing the
  ticket, verification state, assigned team, evidence links and recommended next
  action.
- One scheduled health-check definition that can be manually triggered in the
  demo. Do not present it as a production scheduler until durable scheduling is
  implemented.
- Demonstration script, acceptance checklist and known-limitations statement.

### Explicitly out of scope for Monday

- Full multi-tenant SaaS, RBAC, SSO and audit-retention policy.
- Autonomous free-form exploration of production customer applications.
- A real physical-IoT device command/control test platform.
- Complete Jira, ServiceNow, Teams or email production connectors.
- Claims of automatic root-cause analysis, self-healing, full OWASP coverage or
  guaranteed issue reproduction.

These remain V3 backlog items, not hidden gaps.

## 3. Product flow and user experience

### A. Customer support incident flow

1. A customer explains a problem to Tara.
2. Tara asks the minimum useful diagnostic questions: affected feature, user role,
   expected result, actual result, time, environment and harmless reproduction
   steps.
3. Tara searches approved knowledge and troubleshooting material.
4. If unresolved, Tara creates a ticket immediately and posts the structured
   incident to Testmate. A test failure must never prevent support from creating a
   ticket.
5. Testmate validates authorization, target scope, scenario availability and
   test-account availability.
6. The planner chooses a versioned, approved scenario or produces a bounded plan
   grounded only in saved route and element evidence.
7. A worker executes the allowed browser/API checks and stores evidence.
8. Testmate sends the verdict and evidence reference to the helpdesk.
9. Helpdesk assignment rules select the responsible team. Engineers fix the issue.
10. The same scenario is re-run and the ticket records the verified recovery.

### B. Continuous assurance flow

1. A product owner selects a critical journey such as login, device dashboard,
   report download or ticket creation.
2. A schedule, deployment event or approved IoT telemetry rule creates a
   verification job.
3. Testmate runs the versioned scenario in the approved environment.
4. Repeated failures are grouped into one incident; retries use back-off and
   maintenance windows.
5. Send one actionable alert with evidence, then one recovery notice when the
   scenario passes again.

Never deep-crawl every customer application continuously. Discovery belongs in
onboarding or controlled re-discovery; monitoring should use approved, focused
journeys.

## 4. Architecture blueprint

```text
                 ┌───────────────────────────────┐
                 │ Tara / CAT Sentinel            │
                 │ Q&A, intent extraction, consent│
                 └───────────────┬───────────────┘
                                 │ Incident API
┌──────────────┐        ┌────────▼─────────┐       ┌──────────────────┐
│ Scheduler /  ├───────►│ Assurance         │──────►│ Helpdesk          │
│ deployment / │        │ Orchestrator      │       │ dashboard/ticket  │
│ IoT signal   │        │ policy + queue    │       │ assignment        │
└──────────────┘        └────────┬─────────┘       └──────────────────┘
                                 │ durable job
                     ┌───────────▼───────────┐
                     │ Isolated Testmate      │
                     │ Playwright/API worker  │
                     └───────┬────────┬──────┘
                             │        │
                     ┌───────▼───┐ ┌──▼─────────────────┐
                     │ Postgres  │ │ Evidence storage    │
                     │ runs/jobs │ │ screenshots/reports │
                     └───────────┘ └────────────────────┘
```

### Required data records

| Record | Minimum fields | Why it exists |
|---|---|---|
| `support_incident` | ticket ID, tenant, reporter role, sanitized summary, affected feature, priority | Preserves Tara's support context. |
| `verification_job` | job ID, incident ID, scenario version, target, mode, state, retry policy, correlation ID | Durable orchestration and deduplication. |
| `verification_attempt` | job ID, worker ID, start/end, outcome, failure category, event sequence | Explains retries and interruptions. |
| `evidence_manifest` | artifact ID, run ID, source step, checksum, storage location, retention expiry | Makes reports and screenshots defensible. |
| `assignment_rule` | feature/service, owning team, escalation window | Routes work deterministically. |

Existing `ScanRun`, saved-run, checkpoint and execution-result data should be
linked, not replaced. The V3 job ID must reference the existing Testmate run ID.

## 5. Safety and decision policy

The model can choose only approved tools and actions. It cannot run arbitrary
Python, use raw credentials in prompts, alter production data or issue physical
device commands.

| Situation | System action |
|---|---|
| Known approved scenario | Execute automatically within scope. |
| User complaint lacks details | Tara asks questions; ticket remains open. |
| Required test account unavailable | Mark `blocked`; never claim product failure. |
| Target or network unavailable | Mark infrastructure/target outage separately. |
| Sensitive/destructive operation | Park for human authorization. |
| Result conflicts with user report | Mark `not_reproduced under tested conditions`; retain the ticket. |
| Repeated scheduled failure | Group as one incident; alert once, then send recovery. |

For IoT, start with portal journeys and read-only telemetry freshness rules. A
dashboard failure, device-gateway failure and physical-device failure are different
layers. Physical commands require a customer-approved laboratory and a separate
safety policy.

## 6. Delivery phases in the correct order

### Phase 0 — Friday: scope and contracts

**Goal:** Prevent demo work from becoming an untestable feature pile.

- Select one target app, one seeded/known defect and one approved test role.
- Agree the incident JSON contract and four verdicts.
- Choose one helpdesk destination: internal mock queue first, or one approved
  connector.
- Define the single critical scenario and expected evidence.
- Add a feature flag so V2 workflow remains unchanged.

**Exit:** a written request/response example exists and the demo data is safe.

### Phase 1 — Saturday: durable incident-to-run vertical slice

**Goal:** create and run a verifiable job without losing V2 behaviour.

- Add database migration/models for incident, job, attempt and evidence reference.
- Add idempotent `POST /integrations/incidents` and `GET /verification-jobs/{id}`.
- Map the job to an existing scan/generate/execute pipeline with correlation IDs.
- Persist terminal outcome atomically with its latest evidence references.
- Add unit tests for duplicate delivery, invalid scope and worker interruption.

**Exit:** one API request leads to one—and only one—durable Testmate run.

### Phase 2 — Sunday morning: planner, policy and evidence

**Goal:** make the automated conclusion trustworthy.

- Use a scenario library first; LLM planning is bounded fallback, never the only
  source of truth.
- Add explicit authorization and test-account checks.
- Produce the four verdicts plus failure category.
- Attach screenshot/report/trace manifest references to the job.
- Add a compact event timeline: queued, running, evidence captured, completed.

**Exit:** a reviewer can determine what was tested and why the verdict occurred.

### Phase 3 — Sunday afternoon: helpdesk experience and monitoring demo

**Goal:** show operational value to a client.

- Build the incident queue/details view using persisted job records.
- Add deterministic assignment rule and manual reassignment.
- Add a manually triggered scheduled-check prototype and failure grouping key.
- Add notification payload mock/log; do not send real external messages without
  approved recipients and credentials.

**Exit:** a user can follow a ticket from Tara context to evidence and owner.

### Phase 4 — Monday: release gate and client rehearsal

**Goal:** prove the vertical slice, freeze it and present it honestly.

- Run happy path, target outage, expired authentication, duplicate request,
  unsupported complaint and evidence-link failure tests.
- Complete one rendered-browser demonstration.
- Record all limitations and create backlog items for deferred V3 phases.
- Tag the demo build only when acceptance evidence is attached.

**Exit:** no open showstopper in the chosen demo path; all failures have clear
operator messaging and do not create false application defects.

## 7. Monday acceptance criteria

The work is accepted only when all of these are demonstrably true:

1. One structured Tara-style incident creates one ticket and one Testmate job.
2. Replaying the same request does not create a duplicate execution.
3. The job state survives a browser/worker/API interruption and records its
   terminal status honestly.
4. The job references a Testmate run, scenario version, target, role and evidence.
5. The helpdesk view identifies a responsible team and recommended next step.
6. A `blocked` or `inconclusive` run does not show a green functional pass.
7. A failed screenshot/report link is visible as an evidence failure.
8. V2 functional, security-mode and current report flows pass their focused
   regression tests unchanged.

## 8. Backlog after Monday

1. Replace process-local scan control with queue-backed worker control and
   persistent event replay.
2. Production scheduler, deployment webhooks, alert suppression and recovery
   notifications.
3. Teams/Jira/ServiceNow connectors and customer-configurable assignment rules.
4. Tenant isolation, RBAC, secret vault, audit log and retention policy.
5. API/telemetry adapters, role matrix, visual diff and approved ZAP/Burp adapters.
6. Locator-healing candidates with approval trail, impact analysis and model-cost
   observability.
7. Device-lab and physical-IoT test protocols.

## 9. Client demonstration language

> A customer describes an issue to Tara. Tara asks the right support questions and
> creates a ticket if it cannot resolve the issue. Testmate then verifies the
> reported journey using an approved test account and produces evidence. The
> responsible team receives a reproducible result, not just a complaint. We can
> run the same journey on a schedule to detect failures early and verify recovery
> after a fix.

Do not say “AI fixes all bugs,” “we test everything continuously,” or “we prove a
root cause from one screenshot.” The credible claim is faster evidence-backed
triage and repeatable verification.

## 10. Refined Antigravity job task

Use the following task as the implementation brief:

```text
Implement ONLY the Monday V3 client-assurance vertical slice described in
docs/V3_CLIENT_ASSURANCE_DELIVERY_BLUEPRINT.md. PiOne Testmate is an existing
FastAPI + Playwright + vanilla frontend V2 product. Preserve all working V2 flows,
run IDs, report formats, assessment modes and safety controls. Gate every V3 UI and
endpoint behind an explicit feature flag.

First inspect the current ScanRun, checkpoint, execution, artifact and telemetry
models/services. Then implement a durable support_incident, verification_job,
verification_attempt and evidence_manifest linkage with migrations. Do not rewrite
the existing crawler or replace its data model.

Create an idempotent incident-ingest endpoint and a job-status endpoint. Accept a
sanitized Tara-style incident containing tenant/project, ticket reference, affected
feature, expected/actual behaviour, authorized target, permitted role and scenario
reference. Duplicate correlation IDs must return the original job rather than
starting another test.

Use only a pre-approved scenario library for the Monday demo. Reuse the existing
Testmate pipeline to create a linked run, execute it and persist one of four
verdicts: reproduced, not_reproduced, blocked or inconclusive. Store all evidence
through a manifest reference. Never put passwords, cookies or raw user conversation
text in LLM prompts, logs or exports.

Add a compact Helpdesk Incident Queue/Details view that displays: ticket, status,
scenario, run ID, verdict, owner, evidence links and next action. Use deterministic
assignment rules. Add a manually triggered health-check definition only; do not add
an unbounded production scheduler or send external messages.

Implement focused tests for happy path, duplicate delivery, invalid authorization,
target outage, expired session, worker interruption and broken evidence link. Run
existing V2 focused regression tests. Report pass, fail and blocked evidence
accurately. Do not claim physical-IoT testing, full autonomous exploration, root
cause analysis or production integrations that are not implemented.
```
