# OmniTest / PiOne Testmate — Live Functionality Audit

**Date:** 2026-11-04
**Branch:** `v3-pre-tara-foundation`
**Scope:** V2 workflow, V3 assurance foundation, API contracts, dashboard/data lineage, database cross-read controls, security boundaries, artifacts, saved runs, regression/baseline behavior.

## Executive result

The implementation is a strong V2 baseline with a feature-flagged V3 foundation. The deterministic/unit-level behavior is currently healthy, but this run is **not a release certification** because the host cannot sustain the live async/browser gate. The release gate stops before browser execution with:

> `Python socket.socketpair() did not complete within 8 seconds.`

The local API was healthy earlier in this session (`/health` returned HTTP 200 with `browser_ready: true`), but the process subsequently became unreachable with Windows `WinError 10013`. The rendered-browser controller is also disabled in the current Work settings. Therefore live UI claims below are marked as unverified unless supported by an automated test or source inspection.

## Evidence ledger

| ID | Area | Check | Result | Evidence / limitation |
|---|---|---|---|---|
| E-01 | Build | Python compilation of `api` and `automation_framework` | PASS | `python -m compileall -q api automation_framework` |
| E-02 | Dependencies | Dependency consistency | PASS | `python -m pip check` reported no broken requirements |
| E-03 | V3 | API contract, dashboard, orchestration and database assertion suites | PASS | 32 tests passed |
| E-04 | Security | Deterministic SSRF, redirect, isolation, redaction and unsafe-content subset | PASS | 10 passed, 9 deselected |
| E-05 | Core components | Action safety, boundary guards, crawler components and route-health counts | PASS | 26 tests passed |
| E-06 | Migration | Fresh database migration to current head | PASS | Prior verified migration reached `g7b8c9d0e1f2 (head)` |
| E-07 | Release gate | Browser readiness and async runtime | BLOCKED | `socket.socketpair()` timed out after 8 seconds |
| E-08 | Browser control | Rendered UI interaction | BLOCKED | Local browser use disabled in Work settings |
| E-09 | Live API | `/health`, `/version`, `/system/db-status`, workflow routes | PARTIAL | `/health` was previously HTTP 200; later restart/connect attempts hit WinError 10013 |
| E-10 | Full suite | All collected tests | NOT CERTIFIED | 316 collected; full/non-browser run did not complete on this host |

## What the product does

### V2 functional workflow

1. **Scan Application** accepts a target and optional authentication.
2. **Choose Modules** lets the user select discovered routes/modules.
3. **Manual Review / Interactive Learning** records user actions and locator evidence.
4. **Crawl Progress** reports routes, elements, safe actions, telemetry and checkpoints.
5. **Analyze** presents route health, element registry, parking-lot actions and optional security review.
6. **Generate Tests** creates grounded cases from scanned controls, uploaded requirements and provider output; deterministic fallback remains available.
7. **Execute** runs selected cases with screenshots and failure categories.
8. **Report** exposes JSON/YAML/Excel/Markdown, execution results, screenshots, checkpoints and regression-baseline controls.

### V3 assurance foundation

The code contains feature-gated assurance routes for:

- durable incidents, jobs, attempts and evidence manifests;
- idempotent intake and correlation IDs;
- approved API/OpenAPI/Postman contract registration;
- read-only status, schema, required-field, auth, timeout and latency checks;
- dashboard value versus API validation;
- approved read-only database query definitions and API/database comparisons;
- assignment rules and manual schedule definitions;
- bounded retry and lifecycle transitions.

V3 remains additive and hidden unless explicitly enabled. This protects the working V2 flow.

## Feature-by-feature status

| Feature | Current assessment |
|---|---|
| Scan, module selection and crawl state | Implemented; automated lifecycle coverage present; live browser proof blocked |
| Interactive learning checkpoints | Implemented in code/tests; rendered multi-module browser journey not proven in this run |
| Telemetry and reconnect state | Contracts and tests present; live stream proof blocked by host runtime |
| Test generation and provider fallback | Implemented and covered by unit tests; live Gemini/Ollama run not performed here |
| Requested testcase count and grounded planning | Implemented in planner tests; live UI count proof not performed |
| Test execution and failure categorization | Implemented; execution/recovery tests present; full rendered execution unverified |
| Excel/report exports | Export routes and lineage tests exist; downloaded workbook was not re-opened in this run |
| Saved runs/checkpoints | Persistence and resume tests exist; browser save/reload/recovery journey unverified |
| Regression suites | CRUD/duplicate behavior covered in tests; browser execution proof unverified |
| Baselines and comparisons | Persistence/compare tests exist; browser diff proof unverified |
| Artifacts/screenshots | Safe download and cross-run isolation tests exist; live screenshot opening unverified |
| OmniTest Assurance Desk | Feature-gated UI/routes exist; V3 disabled by default and live desk flow unverified |
| API contract testing | Phase 2 implementation and focused proof pass |
| Dashboard-to-API validation | Service and focused proof pass; browser-visible mismatch proof unverified |
| API/database cross-read | Approved-query controls and focused proof pass; real configured database run unverified |
| Tara/helpdesk integration | Not implemented as a live connector; do not present as complete |
| Continuous scheduler/workers | Definitions exist, but unattended production scheduling is not complete |

## Security review status

Automated checks cover SSRF/private-network rejection, unsafe redirects, cross-run artifact isolation, path traversal, secret/token redaction, provider response limits, websocket isolation and unsafe frontend content. These are valuable safeguards, but they are not a complete penetration test. No production VAPT was performed in this audit.

## Release blockers

1. **Host async runtime / socket restriction** prevents the release gate from reaching browser execution.
2. **Browser control is unavailable** in the current Work session.
3. **Full non-browser suite did not complete**, so no blanket “all tests passed” claim is justified.
4. **Live export/recovery/regression/Assurance Desk flows require one rendered-browser run** on a healthy host.

## Recommended demo flow

Use a staging/demo target and explain the product in this order:

1. “OmniTest discovers the application and builds a grounded map of routes and controls.”
2. Select a module and show crawl telemetry and checkpoint persistence.
3. Use Manual Review to teach one control; show the page URL and selector evidence.
4. Generate a small grounded test plan and point out that actions are constrained to discovered UI evidence.
5. Execute the plan and show pass/fail, failure category and screenshot evidence.
6. Open the report and demonstrate JSON/YAML/Excel plus execution results.
7. Show Saved Runs, checkpoint resume, regression replay and baseline comparison.
8. For V3, state accurately: “The assurance foundation now supports approved API and dashboard/database checks behind a feature flag; Tara integration follows the release gate.”

## Next action

Run the release gate on GitHub Actions/Linux or a clean host where `socket.socketpair()` and Playwright can run. Then complete one rendered workflow covering export, checkpoint recovery, regression replay, baseline comparison and (if enabled) the Assurance Desk. Only after that should the V3 documents be marked release-proven and Tara integration begin.
