# PiOne Testmate QA Product Audit

**Date:** 26 October 2026
**Scope:** the real local PiOne Testmate repository, Mock Demo browser journey, scanner/result contracts, export code, test suite, and authorized-security design.
**Verdict:** the product is a promising **evidence-aware QA prototype**, not yet a production-grade AI QA platform. The headline should be *grounded test planning from observed UI evidence*—but only after the evidence is shown, retained, and verified end-to-end.

## What was actually wrong

The concern was valid. The crawler already collected rich `automation_targets`—label, role, selector strategy, XPath, discovery mechanism and action classification—but the UI mostly displayed simple component totals. The report then reconstructed a made-up `button:has-text(...)` selector instead of showing the selector the scanner observed. That made the product look less capable than its data model and, worse, made the evidence misleading.

There was a second contract defect: `api/utils/response_builder.py` returned only the first 25 targets for a route. An e-commerce page could therefore scan 100 controls, while the UI and downstream planner received 25. This is a correctness bug, not merely a presentation limitation.

## Remediations made in this change set

1. **Full registry returned:** scan API responses now keep all collected targets and carry explicit per-route and total target counts.
2. **Evidence registry rendered:** Analyze and Report now show, for every target, route, business/item context, control, primary selector, XPath, and discovery source.
3. **Product-card identity retained:** the browser extraction assigns nearby product/card context (for example `data-sku`, item name and card selector) to controls such as **Add to cart** and **Remove**. Two visually identical buttons on two products can now be distinguished.
4. **Mock mode made honest:** Mock Demo uses the same target schema as live scans and identifies its data as `mock_fixture`; it no longer pretends that component summaries are live evidence.
5. **Terminal telemetry reconciliation:** completed/failure snapshots with persisted scan results now backfill module, route and element counters rather than displaying a completed header with `0 / N` and zero elements.
6. **Export lineage preserved:** crawl export already flattens `automation_targets` into JSON/YAML/Excel, including selector, XPath, discovery mechanism, safety classification and execution results when available. The mock scan router now feeds that canonical schema instead of fabricated component-only records.

## Browser evidence from this audit

The Mock Demo flow was exercised through Scan Application → Choose Modules → Manual Review → Crawl Progress → Analyze.

- Analyze showed **5 routes** and **47 targets** after the corrected flow.
- The evidence registry contained actual records such as `/dashboard`, `Export Report`, `testid [data-testid="mock-export-report"]`, XPath `//*[@data-testid="mock-export-report"]`, and source `mock_fixture`.
- This proves the new UI consumes the target registry. It does **not** prove an arbitrary live production site has been fully extracted. Live browser extraction remains a separate acceptance gate.

## Verification completed in this audit

- `pytest automation_framework/tests/test_saucedemo_and_route_health.py::test_route_health_classification_and_aggregation automation_framework/tests/test_saucedemo_and_route_health.py::test_mock_demo_preserves_registry_evidence_shape automation_framework/tests/test_test_planning_service.py -q` → **12 passed**.
- `pytest automation_framework/tests/test_saucedemo_and_route_health.py::test_export_artifacts_include_route_health_and_execution_sheets automation_framework/tests/test_saucedemo_and_route_health.py::test_mock_demo_preserves_registry_evidence_shape -q -m browser` → **2 passed**.
- Python compilation passed for every edited backend module; all four inline JavaScript blocks compile with Node; `git diff --check` has no whitespace error.
- The broad non-browser suite was started but did not finish within the bounded audit window. It must be completed in CI before release; this document does not count it as passing.

## Finding ledger

| ID | Finding | Severity | Status | Why it matters |
|---|---|---:|---|---|
| QA-001 | Target evidence was collected but hidden/reconstructed in UI | High | Fixed and browser-verified in Mock Demo | A QA user could not trust or inspect what the system planned from. |
| QA-002 | API truncated route targets at 25 | Critical | Fixed and unit-tested | Test planning and exports could silently omit controls. |
| QA-003 | Product-level controls had no business identity | High | Fixed in extraction contract; browser fixture pending | `Add to cart` must identify *which* item, not just the label. |
| QA-004 | Completed scan could display zero progress/targets | High | Fixed in terminal reconciliation; needs live-run browser regression | Contradictory state destroys confidence in long-running automation. |
| QA-005 | Mock Demo did not conform to live evidence shape | Medium | Fixed and unit-tested | Demo behaviour must not hide real integration defects. |
| QA-006 | Browser test fixture can stall at Playwright startup on this host | High | Open environment blocker | It prevents this audit from claiming the full live-crawler suite passed. |
| QA-007 | Security review is constrained passive/recon style, not full DAST | Critical claim gap | Open product gap | It must not be marketed as confirmed XSS/SQLi testing. |

## Is it really an AI QA product today?

**Partly.** It has the right foundation: real browser crawling, structured selectors, safety classifications, deterministic planning fallback, execution evidence, artifacts and regression concepts. That is more than a UI mockup.

But a senior QA engineer would still reject a “production AI QA platform” claim because it lacks a complete coverage model. It does not yet prove that every important business flow, role, state, API contract, browser, viewport, accessibility risk and failure mode has been considered. The AI can propose tests; the product must measure what it has *not* proved.

## QA gaps to close, in priority order

### P0 — proof and reliability

- Model the run lifecycle as a durable state machine; each transition must be idempotent and replayable after browser/backend/tunnel interruption.
- Stream server events with sequence IDs, reconnection cursors and terminal snapshots. UI polling must never overwrite newer terminal facts with stale progress.
- Add browser E2E tests for live crawl, auth refresh, cancel/retry, reports, baseline save/replay and restart recovery.
- Give each artifact an immutable manifest: run ID, source route/target identity, planner/model version, timestamp, execution step and screenshot hash.
- Treat absent selector/XPath, duplicate target identity and empty extraction as visible quality failures—not quiet success.

### P1 — real QA coverage

- Add a coverage graph: requirement/user story → business risk → scenario → route → target → generated test → executed step → evidence.
- Represent application entities and state: product SKU, cart line, user role, order, filter and feature flag. Heuristics are a start; vertical/domain adapters must be explicit and testable.
- Add test data contracts and cleanup. A test should say what it creates, mutates, expects and removes.
- Include negative, boundary, concurrency, accessibility, visual, responsive, cross-browser and API-contract checks in the coverage model.
- Add locator health: uniqueness score, fallback chain, element screenshot, DOM fingerprint, change history, approval-required healing and flaky-test score.

### P2 — execution at team scale

- Isolate runs in worker processes/containers; never rely on the developer laptop or a localtunnel URL as the test agent.
- Provide a secure agent registration model so a colleague’s run opens a browser **on their worker**, not on the host machine that owns the tunnel.
- Support CI outputs: JUnit, SARIF, Playwright trace, stable Excel/JSON, policy gates and baseline diffs.
- Add RBAC, tenancy, secrets vaulting, retention rules, audit logs and model/provider cost telemetry before external customer use.

## Security truth: current capability versus a real DAST platform

The current security review is intentionally bounded: headers, cookies, mixed content, error disclosure, API inventory, auth boundary observations and optional low-impact checks. This is useful passive reconnaissance, but it is **not proof of SQL injection, XSS, broken access control or OWASP Top 10 coverage**.

For comparison, [OWASP ZAP Baseline](https://www.zaproxy.org/docs/docker/baseline-scan/) spiders a target and passively scans responses without attacking it; ZAP’s passive rules can identify items such as clickjacking-header and error-disclosure issues. [OWASP’s DAST guidance](https://owasp.github.io/DevSecOpsGuideline/2-Process/2-4-Test/2-4-2-Dynamic-Application-Security-Testing/) distinguishes this from active, authorized payload testing and stresses authenticated/API-aware coverage.

The product roadmap should add adapters—not reimplement scanners blindly:

1. ZAP baseline/passive adapter for every authorized staging build.
2. Explicitly approved active ZAP or Burp scan profile for isolated staging only.
3. Auth/context bootstrap, OpenAPI/GraphQL import, rate budgets and scope allowlists.
4. Normalized findings with scanner ID, confidence, request/response evidence, reproduction status, CVSS/OWASP/CWE mapping and false-positive disposition.
5. A safety gate: no active payloads, destructive operations or production targets without recorded authorization.

## Acceptance gates before an external demo claim

- A live e-commerce demo shows at least two distinct product cards with separate add/remove controls, selector + XPath + product context, and exported Excel parity.
- A 50+ target route proves no target is silently truncated.
- Interrupted crawl, lost browser, expired auth and backend restart each have a reproducible recovery test.
- Excel-imported test cases show one execution result per source row and a clear “not executed/invalid/skipped” reason where applicable.
- Security UI labels its mode exactly: `Passive Security Review`, `Authorized Active DAST`, or `Imported Scanner Findings`; never a generic “security scan.”
- All browser tests run reliably in CI on a clean worker. The local Playwright startup stall is resolved or quarantined with a tracked issue.

## Product positioning after these gates

Sell **evidence-backed QA orchestration**, not “AI that tests anything.” The defensible promise is:

> PiOne Testmate maps what the browser actually saw, generates only evidence-grounded safe tests, executes them with traceable proof, and shows exactly what changed between approved runs.

That is compelling because it connects discovery, planning, execution and regression evidence. The AI is the copilot; the observed application and recorded results are the truth.
