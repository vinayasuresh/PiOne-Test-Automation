# PiOne Testmate v2 — Live Interaction Release Audit

**Date:** 2026-10-26  
**Verdict:** **Do not release as production-ready yet. Suitable for an internal beta/demo with clear caveats.**  
**Overall rating:** **5.6 / 10**

## What was actually tested

This was a live browser workflow, not a source-only review. I used the application at `http://127.0.0.1:8000` against the public SauceDemo target with Interactive Learning Checkpoints enabled.

1. Discovered modules from a real authenticated SauceDemo session.
2. Selected all three discovered modules and completed the human checkpoint flow.
3. Ran the deep crawl to completion.
4. Inspected Analyze evidence for route, selector, XPath, and product-context capture.
5. Generated the deterministic test plan.
6. Explicitly confirmed and ran all generated tests.
7. Opened the final report and a failed-step screenshot.

No active security/VAPT checks were run in this audit. The test target was used only for the normal functional workflow.

## Observed end-to-end result

| Stage | Live result | Rating |
|---|---|---:|
| Discover + module selection | Three real modules discovered and selected. Authentication/loading was opaque for roughly 25 seconds. | 7/10 |
| Interactive crawl | Human checkpoints appeared and Continue worked. Crawl completed with 3 routes and 22 elements. | 6/10 |
| Analyze evidence | Real route-level selectors and XPath records appeared, including `#add-to-cart-sauce-labs-backpack`. | 7/10 |
| Test planning | Three tests / twelve steps generated, but planning context briefly claimed **0 grounded targets** and retained a stale mock session name. | 3/10 |
| Execution | Completed after about 75 seconds: **9 passed, 2 failed, 1 skipped**. | 5/10 |
| Reporting/evidence | Matrix, counters, downloads, and failed screenshot viewer worked. Screenshot title leaks an unreadable local file path. | 7/10 |
| Production UI readiness | Functional, but visually dense and internally inconsistent in several lifecycle states. | 4/10 |

## How the app works today

1. **Scan Application** starts an authenticated Playwright session against a target.
2. **Choose Modules** turns discovered navigation/areas into a bounded crawl scope.
3. **Manual Review** is the safety checkpoint for a human to approve scope/actions.
4. **Crawl Progress** explores safe actions, finds routes and interactive controls, and records telemetry.
5. **Analyze** presents route health plus the captured UI evidence: locator types, selectors, XPath, labels, and a limited product/context value.
6. **Generate Tests** converts the scan evidence into deterministic test cases.
7. **Execute** runs only checked tests after explicit confirmation and captures per-step evidence/screenshots.
8. **Report** surfaces results and downloadable artifacts; a baseline can be saved for later comparison.

That is the right workflow shape for an AI-assisted QA product. The reliability problem is the handoff of data between these stages, not the existence of the stages.

## Release blockers found in the live run

### P0 — Planner loses or misrepresents scan grounding

The Generate Tests screen initially displayed `3 approved/discovered routes · 0 grounded UI targets` for a scan that Analyze had just shown with 22 real UI targets. The header also continued to call the session `mock_179` although this was a live SauceDemo run. After generation, another label correctly said `3 routes, 22 targets`; those contradictory values make the provenance of a plan impossible to trust.

**Release condition:** one canonical run context must drive every stage; test generation must be blocked if its target count does not match the approved scan evidence.

### P0 — Generated product-detail URL is incomplete

The crawler captured the product page as `/inventory-item.html?id=0`, but the generated test navigated to `/inventory-item.html` without `?id=0`. The next required Add-to-cart step timed out because that URL does not show the expected product control. A later testcase had the same failure pattern.

**Release condition:** preserve the canonical URL including query parameters and fragments whenever the scanner classifies a route. Never reduce a data-bearing route to a path-only string.

### P1 — Crawl counters contradict each other while the run is active

During the live crawl, the screen displayed 100% progress and 3 routes scanned while module progress still showed `0 / 3`. The crawl also reported 11 safe actions and one parking-lot action in telemetry, then ended with zero safe actions and Analyze showed zero parking-lot actions.

**Release condition:** calculate all cards from a single versioned run snapshot. Event-stream counters must be monotonic unless the UI explicitly says they are recalculated.

### P1 — Generated coverage is too shallow for an AI QA promise

Only three happy-path cases were generated. The inventory journey had useful actions such as product details, Add to Cart, cart count, and Continue Shopping, but there was no negative behavior, boundary assertion, or state-reset coverage. The report’s "100% Grounded" statement is technically about traceability, not meaningful functional coverage; that distinction is not clear to a user.

**Release condition:** distinguish *grounded steps* from *coverage achieved*. Show the routes/actions covered, untested actions, and intentionally excluded risk actions.

## Production-style UI improvements for v2

1. **One run identity everywhere.** Replace stale `mock_179` labels with the real session name and visible short run ID. A live run must never look like a demo run.
2. **A compact live status strip.** Show `Running → current module → current route → elapsed time → last event` in one fixed row. Avoid independently updating cards that disagree with one another.
3. **Evidence-first Analyze screen.** Make the element registry the primary content, grouped by route and business component. Each row should show: action label, role, CSS/ARIA/XPath, resolved URL, product/item context, and safety classification.
4. **Readable planning contract.** Before generation, show exactly what is being sent to the planner: `3 routes · 22 targets · 1 parked action · 0 excluded`. Disable Generate when that contract is stale or incomplete.
5. **Execution progress rather than a spinner.** Display `TC_002, step 2 of 3` and elapsed time. The 75-second no-feedback period made a completed run look hung.
6. **Failure diagnosis card.** For a timeout, show expected locator, actual URL, prior action, screenshot, retry option, and whether the likely cause is plan, locator, target, or environment.
7. **Human-friendly evidence modal.** Title it `TC_003 · Step 2 · Add to cart`, keep the path in a copyable secondary field, and never make a raw local filesystem path the heading.
8. **Density and hierarchy.** Current screens contain many similar buttons, badges, cards, and repeated headers. Use fewer competing colors, clearer primary actions, and progressive disclosure for advanced controls.
9. **Truthful labels.** Replace broad claims like “100% Grounded” with `22/22 planned steps trace to scanned evidence` plus a separate coverage score.
10. **Release-grade empty/error states.** Authentication, tunnel interruption, stale job, and no-target states need explicit recovery choices rather than generic backend-unavailable language.

## Functional improvements required for v2

1. Preserve full canonical URLs in crawl records and generated plans.
2. Add a server-side plan-validation gate: each navigation, locator, and assertion must resolve against the same immutable scan snapshot before execution can start.
3. Make run state transitions atomic: crawl telemetry, counters, Analyze registry, plan context, and report must refer to the same run revision.
4. Persist safe-action and parking-lot evidence separately; do not overwrite event totals with snapshot defaults.
5. Generate at least: happy path, negative/invalid state, boundary/state transition, and recovery/return journey when evidence supports them. Label unavailable categories honestly.
6. Provide per-step execution heartbeat/progress and a bounded timeout with a recoverable job state.
7. Validate screenshot/report artifact links through the browser and replace raw absolute paths with artifact IDs and user-facing names.
8. Add live end-to-end regression tests for: authenticated target, interaction checkpoint, query-string route, planner target count, execution evidence, and report download.

## What is already credible in v2

- The full workflow is navigable from discovery through report.
- Interactive learning checkpoints work in the real browser flow.
- The crawler now captures concrete QA evidence rather than only route names.
- Per-step execution results, failure categories, screenshots, report counters, and artifact entry points are present.
- Explicit execution confirmation and parking-lot concepts are good safety/product foundations.

## Release recommendation

Call this **“v2 internal beta / guided demo”**, not a production-grade AI QA platform yet. For a senior-demo, lead with the real evidence capture and traceable execution, then plainly say the next v2 stabilization work is data lineage: preserving route parameters, eliminating stale run state, and validating every generated step against the approved scan.

The product is not fake—it has a promising end-to-end skeleton and real browser evidence—but the current planner-to-execution trust boundary is not strong enough to promise autonomous production QA.

## Baseline closure after the live audit

The following V2 corrections were implemented and committed in `24daaff`:

- Canonical route URLs now retain query strings/fragments from scan evidence into generated steps.
- Analyze/planning context counts targets from the route registry and no longer falls back to an unrelated `total_elements` field.
- Safe-action and parking-lot evidence are carried in the scan response for Analyze, planning, and Report.
- Safe business actions such as Add to Cart are included in safe-action totals.
- New scans use the current session-name field instead of a stale restored name.
- Execution polling shows elapsed seconds rather than an unexplained long spinner.
- Screenshot evidence uses a readable filename title instead of an absolute local path.
- “100% Grounded” was replaced with a truthful linked-target count or an explicit unavailable state.

Verification after the changes: 13 focused planning/route tests passed, the canonical-route and safety-evidence regression passed explicitly, Python compilation passed, and the previous live run remains documented as the pre-fix reproduction. The browser-state test bundle was not claimed because it exceeded the bounded test window; a fresh live SauceDemo rerun should be the next release-gate check.
