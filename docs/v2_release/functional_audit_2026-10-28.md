# PiOne Testmate V2 — Functional Audit

**Date:** 2026-10-28
**Environment:** local PiOne Testmate at `http://127.0.0.1:8000`
**Scope:** functional workflow, mode controls, evidence visibility, test planning, execution reliability, persistence, and security-review guardrails.

## Browser evidence

A rendered-browser run was completed using **Mock Demo** in **Functional testing only** mode.

- Selected 2 of the 3 discovered modules.
- Crawl completed at 100% with 5 routes and 47 discovered UI evidence records.
- Analyze displayed all five routes as working and exposed per-control route, context, primary selector, XPath, and discovery source.
- The Functional-only Analyze view did not show the Security Assessment panel, while the normal test-planning path remained available.

The browser-control capability became unavailable immediately after opening Analyze, so Steps 6–8 could not be exercised in the same rendered session. This report does **not** claim browser proof for generation, execution, export download, saved-run restore, regression replay, or interactive-click capture.

## Automated contract checks

| Area | Command | Result |
| --- | --- | --- |
| Crawler checkpoint and execution reliability | `pytest test_crawler_components.py test_execution_reliability.py -q -rA` | 17 passed |
| Requested testcase count and planning | `pytest test_llm_gateway.py -q -k "testcase_count or input_normalizer" -rA` | 5 passed |
| Security review safety boundaries | `pytest test_security_review.py -q -k "scope or ssrf or findings or clickjacking or vapt_reuses" -rA` | 21 passed |
| Python compilation | `python -m compileall -q api automation_framework` | passed |

## Methods exercised

1. **Rendered browser, Mock Demo, Functional-only:** discovery, reduced module selection, manual review, crawl completion, Analyze evidence inventory.
2. **Unit/contract:** route health classification, menu ordering, nested-menu reconstruction, checkpoint storage, checkpoint route recovery, execution retry classification, execution checkpoint durability, stale-artifact replacement.
3. **Planning:** uploaded-input normalization, exact requested count capping/expansion, and grounded shortfall behavior.
4. **Security review:** same-origin scoping, path traversal and sensitive route rejection, SSRF protections, redaction, clickjacking analysis, and same-origin auth-cookie reuse.
5. **Static mode-path inspection:** Functional-only hides security controls and keeps planning enabled; Security-only/VAPT-only disable Analyze-to-planning; the security API requires authorization and separate active-probe opt-in.

## Blocking test-host condition

The explicit browser-marked release gate (`test_browser_release_gate.py`) cannot initialize on this host. Its FastAPI TestClient blocks while Windows creates `asyncio.socketpair()` during the AnyIO portal startup. Direct localhost requests from the terminal also fail with `WinError 10013`. This prevents an on-host full API/browser release-gate run, independent of the product UI already shown in the in-app browser.

## Status

Core behavior has meaningful evidence, but the full end-to-end release claim remains **not yet proven** until browser control is available and Steps 6–8, recovery, exports, saved-run restore, regression replay, and interactive target-browser capture are completed in one rendered run.

## Follow-up defect fixed during audit

**Checkpoint resume could restart fresh on routes whose URL contained `token` text.**  The generic persistence redactor treated a route-graph key such as `/enrollment-tokens` as a credential field and replaced its graph node with `[REDACTED]`.  Route-graph deserialization then expected a dictionary and aborted checkpoint loading.

The fix preserves URL-keyed graph entries during redaction and makes graph loading skip only a malformed legacy entry rather than abandoning the whole checkpoint.  The focused component suite passed **13 tests**, including new regression coverage for both future persistence and legacy-checkpoint recovery.

## Current verification addendum — 2026-10-29

Subsequent rendered audit work exercised the application at
`http://127.0.0.1:8000` in Mock Demo mode. It requested 30 grounded cases,
generated 30 cases, executed 82 passing step rows, and invoked the JSON, YAML,
Excel, and Markdown report downloads, baseline save, and Saved Runs lifecycle.
Focused browser tests for manual interaction capture and checkpoint Continue /
End-and-Save decisions passed (`4 passed`).

This addendum improves the evidence status but does **not** prove an end-to-end
live-target multi-module Retry -> Resume -> next checkpoint recovery. That
specific rendered journey remains the final V2 release gate.
