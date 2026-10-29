# Antigravity V3 Execution Prompt

Copy the prompt below into Antigravity when implementation work begins.

```text
Act as a principal product engineer, QA automation lead and application-security
architect. You are working on PiOne Testmate, an existing Python/FastAPI,
Playwright and vanilla frontend repository. Preserve the working scan-to-report flow
unless a change is required by the acceptance criteria. Do not rewrite the project
from scratch and do not silently remove existing features.

PRODUCT NORTH STAR
==================
Build the PiOne Testmate V3 Assurance Graph: one durable evidence graph that links
the target, session, module, route/state, element/API observation, test case, step,
execution result, screenshot/trace, security finding, remediation and regression
decision.

The product is not “an AI that guesses tests.” AI may suggest plans, but Playwright,
scope rules, run state and persisted evidence remain authoritative. The product is
not a replacement for ZAP or Burp. Integrate specialist scanners where they provide
deeper DAST coverage, then normalize their evidence into the Assurance Graph.

EXPLAIN IT LIKE A FIVE-YEAR-OLD
================================
Imagine a careful robot visiting a toy house. First it draws a map of the rooms and
doors it really saw. Then it writes a checklist using only those doors. It tries the
checklist and saves a picture of each try. A security guard checks for weak locks.
Next day the robot compares yesterday’s map with today’s map and points to the exact
door that changed. The robot may ask a smart helper for ideas, but the map and the
pictures decide what really happened.

NON-NEGOTIABLE SAFETY RULES
===========================
1. Preserve the current user flow and run IDs.
2. Functional-only mode must not start or display an active security workflow.
3. Security-only mode must not silently enter functional planning or execution.
4. Keep SSRF, same-origin and destructive-action protections enabled.
5. Never run active or intrusive security checks against public targets in tests.
6. Use local fixtures, Mock Demo or explicitly authorized staging targets.
7. Never report a passive observation as a confirmed exploit.
8. Keep secrets, cookies, storage state and raw credentials out of prompts/exports.
9. Every worker operation must be idempotent or explicitly rejected as duplicate.
10. If a requirement cannot be proven, report “not tested” with the reason.

PHASE 0 — INSPECT BEFORE EDITING
===============================
- Read README.md, CHANGES.md and docs/SECURITY_REVIEW.md.
- Inspect the current run-context, crawl, planning, execution, export, regression
  and security-review services before proposing changes.
- Inspect existing tests and run-state/event contracts.
- Produce a short findings list before editing.
- Do not change unrelated frontend styling or rename public endpoints casually.

PHASE 1 — PROOF CORE
====================
Implement or verify the following:

A. Run state machine
- Explicit legal states: created, scanning, scanned, planning, planned, executing,
  completed, partial, failed, cancelled.
- Reject illegal transitions with an actionable error.
- Persist state and event sequence atomically.
- Recover a run after API/browser/worker restart without duplicate steps.

B. Telemetry contract
- Every event has run_id, sequence, timestamp, phase, severity and human message.
- Reconnect replays events after the last acknowledged sequence.
- Heartbeat and terminal events are persisted, not only sent over WebSocket/SSE.
- UI cannot show completed while the persisted state is still running.

C. Evidence manifest
- Every screenshot, trace, workbook and report has run_id, source step/route,
  created_at, content type and checksum.
- Broken artifact links must be visible as failures, never silently empty.
- The report, download endpoint and screenshot viewer must use the same manifest.

D. Mode isolation
- Add integration tests for functional_only, security_only, vapt_only and both.
- Verify the selected mode survives crawl, Analyze, planning and New Scan reset.
- Hide/disable irrelevant actions instead of leaving misleading controls active.

PHASE 2 — COVERAGE MESH
=======================
A. Excel lineage
- Preserve every uploaded workbook row and testcase ID.
- Execute rows/cases line-by-line with per-row and per-step status.
- Record skip reason, failure category, locator, route, screenshot and trace path.
- Export either the native Testmate workbook or a source-shaped workbook chosen by
  the user, with no dropped input rows.
- Add tests for blank cells, duplicate IDs, formulas, multiple sheets and malformed
  rows.

B. API evidence
- Import OpenAPI/Postman definitions when supplied.
- Attach API requests/responses to the same route/element/test graph.
- Generate safe contract/negative tests without sending destructive payloads by
  default.

C. Role/session matrix
- Support named authorized roles and session fixtures.
- Compare access to the same route/resource across roles.
- Record evidence for anonymous success, denied access and inconsistent exposure.

D. External DAST adapters
- Add a clean adapter interface for ZAP baseline, API and authorized full profiles.
- Add an optional Burp import/adapter path for customers using Burp projects.
- Enforce target scope, rate, timeout and authorization metadata before dispatch.
- Normalize imported findings into title, severity, CVSS, OWASP, CWE, component,
  evidence, impact, likelihood, remediation, source_engine and verification_state.
- Never mark an imported finding verified without source evidence or human approval.

PHASE 3 — LEARNING LOOP
=======================
- Add locator-healing candidates ranked by grounded evidence.
- Require approval before changing a locator or generated test.
- Record old locator, proposed locator, supporting attributes and outcome.
- Cluster failures into selector drift, auth expiry, target outage, product defect,
  environment defect and test defect.
- Version prompts/models and record provider latency, failures, cost and fallback.
- Add impact-based reruns using changed route/element/API evidence.

PHASE 4 — TEAM SCALE
====================
- Add RBAC, tenant/project isolation, encrypted secret storage and audit logs.
- Add a queue with concurrency limits, cancellation and worker heartbeats.
- Add scheduled runs, CI gates, webhooks and SARIF/JUnit output.
- Add retention and deletion policies for screenshots, traces, secrets and reports.
- Add containerized browser/device runners only after the local proof core is stable.

SECURITY TESTING BOUNDARY
=========================
Current bounded checks may include headers, cookies, mixed content, exposure
signals, API inventory, auth boundary and error disclosure. Do not claim that these
prove SQL injection, XSS, SSRF, IDOR, JWT flaws, business-logic abuse or complete
OWASP Top 10 coverage. For those categories, use authorized staging targets and the
ZAP/Burp adapter or an approved specialist tool. Active/intrusive testing requires
an explicit confirmation, rate and scope record.

TEST MATRIX
===========
At minimum, add or update tests for:
- Mock Demo full workflow from scan through report.
- SauceDemo smoke flow with a small stable case count.
- Gemini success, Gemini timeout and deterministic fallback.
- Ollama success and Ollama unavailable fallback.
- Expired authentication with in-place refresh.
- Telemetry disconnect/reconnect with event replay.
- Worker restart during crawl, plan and execution.
- Functional-only mode with no security controls visible or invoked.
- Security-only mode with no functional planner invocation.
- Uploaded Excel with multiple sheets, duplicate IDs, blank rows and failures.
- Screenshot/report/download links and checksum manifest.
- Baseline save, compare and regression replay.
- Scope violation, private IP, redirect and destructive-action parking.
- ZAP/Burp adapter authorization, scope and normalization using mocked adapters.

QUALITY GATES
=============
- Run focused unit tests first, then the complete Windows-safe suite.
- Run browser tests against Mock Demo and a controlled demo target.
- Run py_compile and JavaScript syntax checks.
- Use git diff --check.
- Do not claim full E2E success if a browser, network or provider dependency was
  unavailable; report the exact boundary.
- Keep a machine-readable acceptance summary with pass/fail/blocked and evidence.

DELIVERABLES
============
1. Small, reviewable code changes preserving the existing flow.
2. Updated README, CHANGES and docs/SECURITY_REVIEW.md.
3. V3 phase status with completed, in-progress and not-implemented items.
4. Test evidence and known limitations.
5. A senior-demo script that explains the product without claiming impossible
   security coverage.
6. A final message listing changed files, tests run, failures/blocks and the next
   smallest safe step.

When unsure, choose the smallest reversible change that increases evidence,
reliability or safety. Do not add a shiny feature that makes the demo look correct
while the persisted run state remains wrong.
```

## Why this prompt is strict

It forces Antigravity to inspect the real repository first, preserve the current flow,
separate functional and security modes, and prove state through tests. The “five-year-
old” section is not decoration: it keeps the product idea simple enough that a senior
reviewer can repeat it accurately.
