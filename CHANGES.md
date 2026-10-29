# PiOne Testmate — UX Stabilization and Production Safety Changes

## V2 verification status update (2026-10-29)

- Added a controlled rendered-browser audit covering functional-only planning
  through 30 requested cases, execution, report downloads, baseline save, and
  Saved Runs. The run generated 30 grounded cases and 82 passing execution
  step rows; this is evidence for the Mock Demo path, not a live-target
  certification.
- Added/updated focused browser coverage for manual interaction capture and
  checkpoint decisions. The interaction capture suite passed 4 tests.
- Corrected the controlled browser fixture's `/analytics` route so module
  checkpoint tests exercise a distinct module instead of falling through to
  the root page.
- The outstanding release-evidence item is deliberately tracked: prove one
  rendered live multi-module checkpoint interruption followed by Retry/Resume
  and a successful continuation to Report. Do not claim an unconditional
  production GO until that journey is retained as evidence.

## V3 Product Blueprint (2026-10-26)

- Defined the **Assurance Graph** as the product differentiator: target, session,
  route/state, UI/API evidence, test step, execution proof, security finding and
  regression decision remain linked.
- Added a phase roadmap covering proof-core reliability, API/Excel/security coverage,
  explainable learning and team-scale execution.
- Added a senior-demo brief with an honest narrative, live SauceDemo/Mock Demo flow,
  executive questions and security claims to avoid.
- Added a copy/paste Antigravity execution prompt with preservation rules, mode
  isolation, Excel lineage, DAST adapter boundaries, test matrix and quality gates.
- Updated README product positioning so marketing language matches the implemented
  bounded VAPT scope and current local/single-user maturity.

## Acceptance Hardening Update (Current)

- Standardized the persisted run-state contract returned by `GET /run/{run_id}/state` to include `run_id`, `scan_id`, target namespace, auth status, selected/current module+route, route progress, element/action counters, retry count, last event sequence, and checkpoint version.
- Added duplicate-run prevention for active runs (`409` on concurrent starts for the same run ID) so refresh/reconnect reuses the same run instead of creating a second worker.
- Added monotonic event sequence metadata on telemetry events and stale-event filtering support for reconnect safety.
- Updated scan-control and end-and-save behavior to preserve partial status (`partial_scanned`) and publish correlated control/stop events.
- Made Windows-safe default test execution explicit: browser/Playwright suites are marked `browser` and excluded by default (`pytest -m "not browser"`), with hard per-test timeout to avoid silent hangs.
- Added regression tests for reconnect contract fields, event sequence progression, duplicate run prevention, and partial end-and-save behavior.

## Summary of Completed Priorities

This document records the UX stabilization, responsive layout, accessibility (WCAG AA), notification modernization, and authentication ergonomics implemented in PiOne Testmate.

---

### Priority 1: Workflow Continuity & Route Health Count Normalization
- **Persistent Active Run Bar (`#active-run-banner`):**
  - Displays `Target URL`, `Run ID`, `Current Module`, `Current Route`, `Scan Status`, `Progress %`, `Database Status`, and `Auth Status` persistently across views.
  - Added `#btn-return-current-run` allowing users to jump back into the active workflow step at any time without resetting state.
- **Route Health Metric Parity:**
  - Standardized normalization of route statuses across backend and frontend (`WORKING`, `WORKING_ROUTE`, `PARTIAL`, `PARTIAL_ROUTE`, `NON-WORKING`, `NON_WORKING`, `NON_WORKING_ROUTE`).
  - Implemented `calculateRouteCounts` to derive counts strictly from the normalized `routes` array when routes exist.
  - Resolved false `0` counts on Analyze (Step 5) and Report (Step 8) summary cards.
  - Verified card counts match table rows 1:1 (`5 === 5`).
- **Deterministic Step 4 Completion:**
  - Preserved Step 4 crawl completion without auto-stepping to Step 5.
  - Added explicit `#btn-view-analysis` button on completion.
- **Offline Mock Demo Mode:**
  - Enhanced mock mode to export crawl artifacts and checkpoints deterministically without launching Playwright or failing auth.

---

### Priority 2: Accessibility & Responsive Viewports (WCAG AA)
- **Responsive 8-Step Navigation Stepper:**
  - Converted stepper below 1024px to a single-row horizontal scroll (`overflow-x-auto no-scrollbar`, `flex-shrink-0 min-w-[125px] lg:min-w-0`).
  - Verified clean single-row horizontal scrolling on 1440px (desktop), 1024px (tablet landscape), 768px (tablet portrait), and 375px (mobile).
- **Visible Focus Rings:**
  - Added high-contrast global focus indicators (`outline: 2px solid #2563eb !important; outline-offset: 2px !important`) to all interactive elements (`button`, `a`, `input`, `select`, `textarea`, `[tabindex]`).
- **Semantic Table Headers:**
  - Added `scope="col"` across all 73 table headers across all 12 tables for screen reader navigation.
- **Color Contrast & Dynamic Live Regions:**
  - Upgraded low-contrast placeholder and muted text from `text-slate-400` to `text-slate-500` for WCAG AA 4.5:1 compliance.
  - Added `aria-live="polite"` to telemetry pills and `role="alert"` / `role="alertdialog"` to modals and notification banners.
- **Modal Keyboard Trapping & Escape Key Dismissal:**
  - Implemented global `Escape` key listener and `Tab` focus-trapping across all 8 application modals (`#danger-confirm-modal`, `#exec-confirm-modal`, `#actionable-error-modal`, `#screenshot-modal`, `#refresh-session-modal`, `#baseline-compare-modal`, `#baseline-modal`, `#suite-modal`).

---

### Priority 3: Non-Blocking Notifications, Script Export & Grouped Report Downloads
- **Zero Native Browser Confirmations/Alerts:**
  - Replaced all legacy `alert()` calls with modern, accessible `#toast-container` toast notifications (`showToast(msg, type)`).
  - Ensured destructive operations are strictly guarded by the typed `DELETE` confirmation modal (`#danger-confirm-modal`).
- **Pytest Script Copy & Download:**
  - Added `#btn-copy-script` (copies generated pytest script to clipboard with toast feedback).
  - Added `#btn-download-script` (downloads standalone `.py` script with toast feedback).
- **Grouped Report Downloads (Step 8):**
  - Organized report downloads into two distinct visual groupings:
    - **Primary Audit Package:** JSON, YAML, Excel (10 Sheets), Markdown.
    - **Technical Execution Evidence:** Execution Results, Checkpoint, Screenshots ZIP.
  - Added download feedback toasts (`Download started: ...`) and added `download` attributes to links to prevent page navigation.
- **Sanitized Actionable Error Remediation:**
  - Updated `showActionableError` with regex sanitization of passwords, tokens, API keys, and stack traces.

---

### Priority 4: Mode & Authentication Clarity
- **Mode Explanation Banner (`#mode-explanation-banner`):**
  - Dynamic explanation banner explaining Live Target Scan vs Mock Demo.
  - Live Target: Clarifies Playwright browser launch, SSRF enforcement, credentials handling, and DOM discovery.
  - Mock Demo: Clarifies 100% offline, deterministic simulation without browser launches or network requests.
- **Credential Ergonomics & Reveal:**
  - Added password reveal toggle button `#btn-toggle-password` with eye icon (`fa-regular fa-eye` / `fa-regular fa-eye-slash`) to `#password-input`.
  - Added clear credentials button `#btn-clear-creds` that wipes username/password with toast feedback.
  - Verified no credentials are ever written to `localStorage`, `sessionStorage`, URLs, or checkpoint files.

---

## Verification Test Results

### 1. Python Compilation Check
```
python -c "import glob, py_compile; [py_compile.compile(f, doraise=True) for f in glob.glob('api/**/*.py', recursive=True) + glob.glob('automation_framework/**/*.py', recursive=True)]"
Result: 0 errors (100% compiled successfully)
```

### 2. Pytest Test Suites (66/66 Passing)
- `test_crawler_components.py`: 6 passed in 0.11s
- `test_llm_gateway.py`: 20 passed in 1.25s
- `test_test_planning_service.py`: 11 passed in 0.23s
- `test_run_context_and_auth_lifecycle.py`: 13 passed in 1.22s
- `test_scanner_fixes_and_enterprise_login.py`: 13 passed in 8.03s
- `test_route_health_card_counts.py`: 3 passed in 0.02s
**Total: 66/66 tests passing (100% success rate)**

### 3. Viewport Verification
- **1440px (Desktop):** 8-step stepper renders in an 8-column grid (`scrollWidth === clientWidth`). Active run banner and modal dialogs centered with generous padding.
- **1024px (Tablet Landscape):** Stepper fits without wrapping (`scrollWidth === clientWidth`).
- **768px (Tablet Portrait):** Stepper scrolls horizontally cleanly (`overflow-x-auto`) without breaking into vertical rows.
- **375px (Mobile):** Stepper scrolls horizontally smoothly with touch swipe. Cards collapse to 2 columns.
