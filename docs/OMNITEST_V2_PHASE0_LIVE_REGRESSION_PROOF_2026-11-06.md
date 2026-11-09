# OmniTest V2 Phase 0 Regression Proof

Date: 2026-11-06
Branch: `v3-pre-tara-foundation`
Source revision tested: `e94520c` (working-tree changes included)
Purpose: verify that the V2 application flow remains available with V3 and the new remote interactive-browser capability disabled.

## Configuration and boundaries

- `V3_ASSURANCE_ENABLED=false`
- `INTERACTIVE_BROWSER_ENABLED=false`
- `INTERACTIVE_BROWSER_LAUNCH=false`
- Live rendered run used a disposable local SQLite database and the built-in **Mock Demo** target.
- App already running on port 8000 was left untouched. The isolated proof server used port 8001 and was stopped after the run.
- The Mock Demo run is evidence of workflow/UI wiring; it is not evidence that a third-party or customer application works.

## Results

### Rendered V2 workflow

- First-page API Testing option hidden while V3 is disabled; V3 Assurance navigation hidden.
- Module discovery: 3 modules (Product Inventory, Shopping Cart, Checkout Flow).
- Crawl: completed, 3/3 modules, 5 routes, 47 discovered elements.
- Analyze: route/control evidence rendered.
- Deterministic planning: 3 grounded test cases, 18 steps.
- Execution: 18 passed, 0 failed, 0 skipped. UI correctly labels this Mock Demo result as offline/simulated.
- Report: displayed the same route, element, case, and execution totals.
- Baseline: saved, then comparison reported 3 unchanged routes and no newly passing/failing routes.
- Saved Runs: the completed run was listed and reopened with its evidence.
- Checkpoint and 10-sheet Excel controls each reported that a download started. The downloaded files were not separately opened and inspected in this browser proof.

### Automated regression

- Default suite with the three flags above set false: **262 passed, 84 deselected** in 11.44s. The deselection is the repository's explicit `not browser` default marker.
- Browser-marked suite: **83 passed**; one security-panel fixture initially failed because it interacted with a control inside a collapsed `<details>` element. The fixture was corrected to open the disclosure first, and its rendered-browser retest passed (**1 passed** in 6.40s).
- Focused V2 lifecycle/compatibility slice: **49 passed, 16 deselected**.
- `git diff --check`: passed (Git emitted line-ending conversion warnings only).

## Fix made

`automation_framework/tests/test_security_review.py` now opens the collapsed Security Assessment disclosure before clicking its authorization checkbox. The product UI behavior was correct; this was a browser-test setup defect. No V2 runtime behavior was changed for this fix.

The V3-disabled frontend was also made fail-closed: the API Testing option is hidden unless `/version` confirms `v3_assurance_enabled=true`; persisted API mode falls back to the existing V2 `both` mode when the flag is off or unavailable.

## Phase status

**V2 compatibility gate: PASS for this local run.** This proves the V2 UI/workflow and marked browser regressions with V3 disabled, including the V2 manual-learning test coverage present in the browser suite.

**Governance Phase 0 in the master tracker: NOT COMPLETE.** GOV-01 through GOV-04 still require named product/security/DBA/pilot-owner decisions and approvals. This run cannot approve those decisions on their behalf. PostgreSQL `Omni_Test` is treated only as a local candidate; the connectivity check below does not decide that it is the OmniTest platform database.

A separate, owner-authorized local connectivity check was performed against PostgreSQL 18 at `127.0.0.1:5432`, database `Omni_Test`, using a read-only transaction and the supplied local test role. Authentication succeeded; the role saw zero tables in the `public` schema. This confirms connection/authentication only. It does not establish the database's purpose, schema ownership, suitability for OmniTest persistence, or a production deployment choice. No credential was written to a file or committed.

**Not proven here:** V3 API Testing rendered workflow against a live API, dashboard/API/DB three-way rendered comparison, database schema/data cross-read, full downloadable-file contents, Tara/helpdesk integration, CI-host release gate, or customer-site behavior. Do not describe this result as V3/Tara production readiness.
