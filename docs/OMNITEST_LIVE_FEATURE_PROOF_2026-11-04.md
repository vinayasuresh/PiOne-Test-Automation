# OmniTest V3 Live Feature Proof — 2026-11-04

## Scope

This is an evidence-bound live validation of the currently running PiOne OmniTest instance at `http://127.0.0.1:8000/`. The test used a safe, read-only OpenAPI contract and did not use credentials or mutation requests.

## Browser evidence

The rendered application showed:

- **Assessment Flow** selector with `API Testing (OpenAPI / Swagger / Postman)`.
- **Contract Ingestion & Source Order Normalization** panel.
- Paste-contract input (`api-ingest-paste`).
- Strict target-base-URL input (`api-base-url-input`).
- Target environment selector (Staging, Test/QA, Production read-only strict).
- **Database Cross-Read Validation (3-Way Consistency)** panel.
- Approved-query selector, API value, dashboard UI value, tolerance, and `Validate 3-Way Parity` control.
- Header badge `DB: sqlite` and `browser: chromium ready`.

## Safe test input

The browser was supplied a read-only OpenAPI 3 contract for `GET /todos/1` against `https://jsonplaceholder.typicode.com`. No bearer token, password, API key, upload, mutation, or destructive request was used.

## Result

**Blocked in the live process:** clicking **Ingest & Parse Operations** returned the visible message:

> `Ingestion failed: V3 assurance is disabled`

This is a feature-flag/configuration state, not a contract-parser failure. The current process on port 8000 is running with V3 disabled, so live API ingestion, API execution, report generation, and database parity cannot be truthfully marked passed in this process.

## Automated/source evidence

- Focused V3 API workflow/contract suite: **55 passed, 7 deselected**.
- Durable API-plan checkpoint test: **passed**; a persisted plan was loaded
  after clearing the in-process plan store and restored by the same plan ID.
- Python compilation (`compileall`): **passed**.
- Resume smoke test using the same plan ID: **passed** (`completed`, one operation).
- Full repository suite: **not certified** on this Windows host because the test runner hit the known local-socket permission failure; no green claim is made.

## Release interpretation

### Verified

1. V3 controls are present in the actual rendered UI.
2. The API mode can be selected without changing the V2 stepper.
3. The V3 feature flag correctly prevents execution when disabled.
4. API contract parsing, safe request rules, resume identity, and report logic have focused automated coverage.

### Not live-proven in this run

1. API contract ingestion with V3 enabled.
2. API operation execution and truthful pass/fail/blocked result in the browser.
3. 10-sheet report download from the rendered UI.
4. Database ↔ API ↔ UI parity execution; no approved query was available in the live screen.
5. Full restart/retry browser recovery and end-to-end V3 release gate.

## Required next action

Start a test instance with `V3_ASSURANCE_ENABLED=true` on a host where local sockets are permitted, then repeat this exact browser flow. Keep port 8000/V2 unchanged; use a separate test port or environment for the opt-in V3 proof. Only after the rendered flow, exports, and recovery evidence pass should V3 be presented as live-certified.
