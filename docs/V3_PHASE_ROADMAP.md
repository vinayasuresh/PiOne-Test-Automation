# PiOne Testmate V3 — Assurance Graph Roadmap

**Status:** Blueprint for the next product phase
**Audience:** Product, engineering, QA, security and senior reviewers
**Last reviewed:** 2026-10-26

## 1. The product decision

Do not position PiOne Testmate as “another AI test generator.” That market already
has strong products. Do not position it as a replacement for OWASP ZAP or Burp
Suite. Those products have deeper crawling, attack rules and security research.

Position Testmate as an **Evidence-First Assurance Platform**:

> **Map the real application once, then connect every functional test, security
> observation, execution result, screenshot, regression diff and remediation to
> the exact route and UI evidence that produced it.**

The V3 differentiator is the **Assurance Graph**. It is a durable, explainable
relationship between:

```text
target → authenticated session → module → route/state → element/API evidence
       → test case/step → execution result → screenshot/trace → finding/diff
       → owner/remediation → verified regression result
```

Generic AI tools generate steps. Testmate should prove why a step exists, what it
was grounded on, what happened during execution, and whether the same behavior is
still safe after a change.

## 2. What we have already accomplished

The current v2.1 foundation is real and demoable:

- Playwright-based live browser discovery with strict URL/same-origin boundaries.
- Deterministic route, module, UI-element and state evidence instead of an LLM
  driving the browser blindly.
- Safety classification and a parking lot for destructive, mutation or uncertain
  actions.
- Gemini and Ollama planning adapters with deterministic fallback.
- Requirement/scenario/testcase input and generated functional plans.
- Controlled execution with locators, status categories, console/network evidence
  and per-step screenshots.
- Durable run IDs, SQLite/PostgreSQL option, saved runs, checkpoints, artifacts,
  baseline comparison and regression replay.
- 10-sheet Excel export plus execution results and uploaded-workbook results.
- Explicit assessment modes: functional-only, security-review-only, VAPT-only or
  combined.
- Bounded passive VAPT observations with evidence, confidence, CWE/OWASP mapping,
  remediation and explicit coverage gaps.
- Session-expiry detection and in-place refresh without discarding the run.

This is a strong foundation for a product, but it is not yet enterprise parity.
The next phase must improve reliability and proof before adding more AI sparkle.

## 3. The V3 promise in plain language

Imagine a very careful robot visiting a website:

1. It draws a map of the rooms and doors it actually saw.
2. It labels each door and button with a trustworthy identity.
3. It writes a checklist using only things on that map.
4. It performs the checklist and saves a tiny video-like proof: result, screenshot,
   route and exact element.
5. A security guard checks the same map for weak locks.
6. Next time, it compares the old map and new map and tells us exactly what changed.

That is the Assurance Graph. The AI may suggest the checklist, but the evidence
and the browser engine remain in control.

## 4. V3 phase plan

### Phase V3.0 — Proof Core

**Goal:** Make every run trustworthy, restartable and explainable before scaling it.

**Deliverables**

- Explicit run state machine: `created → scanning → scanned → planning → planned
  → executing → completed/partial/failed` with legal transitions only.
- Crash-resume for crawl, planning and execution, including idempotent retries.
- One live event contract for telemetry with sequence numbers, heartbeats and
  reconnect replay.
- Artifact manifest containing checksum, run ID, source step and creation time.
- Stable screenshot/trace opening and downloadable evidence from every report view.
- CI acceptance suite covering Mock Demo, SauceDemo, uploaded Excel, Gemini failure,
  Ollama generation, session expiry and restart recovery.
- Security mode guard: functional-only runs must not expose or start security work;
  security-only runs must not silently enter functional planning.

**Exit gate**

- 20 consecutive clean Mock Demo runs without a stuck state.
- A killed worker resumes without duplicating steps or losing artifacts.
- Every visible status has a matching persisted state and event sequence.
- No demo proceeds while a showstopper remains open.

### Phase V3.1 — Coverage Mesh

**Goal:** Expand beyond browser-only UI while preserving the same Assurance Graph.

**Deliverables**

- OpenAPI/Postman import and API test generation from discovered network evidence.
- Data-driven Excel execution: every row, case and step receives a result, reason,
  evidence path and final status in the selected output format.
- Role/session matrix: compare anonymous, standard-user and admin observations with
  explicit authorization boundaries.
- Visual diff and browser trace attachment for important routes.
- ZAP adapter with safe baseline, API and explicitly authorized full-scan profiles.
- Optional Burp adapter for authenticated crawl/audit projects where the customer
  already owns Burp.
- Unified finding schema: title, severity, CVSS, OWASP, CWE, component, evidence,
  impact, likelihood, remediation, verification state and source engine.

**Exit gate**

- One scan can produce linked UI, API, functional and security evidence.
- A workbook can be uploaded, executed row-by-row and exported with no lost rows.
- ZAP/Burp findings remain traceable to the Testmate route/API that supplied them.

### Phase V3.2 — Learning Loop

**Goal:** Reduce maintenance without turning AI into an unreviewed authority.

**Deliverables**

- Candidate locator healing ranked by evidence quality, not guesswork.
- Human approval queue for locator changes and generated-plan changes.
- Failure clustering across runs: selector drift, auth expiry, target outage,
  product defect, environment defect and test defect.
- Prompt/model versioning, token/cost telemetry and deterministic replay of plans.
- Test impact analysis: only rerun cases affected by changed routes/elements.
- Coverage score based on observed route/state/role evidence, not raw test count.

**Exit gate**

- Every automatic heal has before/after locator evidence and an approval trail.
- A model outage does not block deterministic test generation.
- Failure summaries identify the likely owner and supporting evidence.

### Phase V3.3 — Team and Scale

**Goal:** Move from a strong local tool to a product teams can safely share.

**Deliverables**

- RBAC, tenant/project isolation, encrypted secret storage and audit log.
- Distributed worker queue with concurrency limits, quotas and cancellation.
- Scheduled runs, CI gates, webhooks and integrations for Jira/TestRail/GitHub.
- SARIF/JUnit/HTML output and signed report manifests.
- Retention, archival and deletion policies for screenshots, traces and secrets.
- Browser/device matrix and containerized runners.

**Exit gate**

- Two teams can run simultaneously without cross-run contamination.
- A failed worker cannot corrupt another team's run.
- Security and functional results can gate a build with reproducible evidence.

## 5. What not to build first

- Do not build a new SQLMap-like payload engine.
- Do not promise complete OWASP Top 10 coverage from passive checks.
- Do not add more model providers before model observability and fallback are solid.
- Do not scale concurrency before the run state machine and artifact integrity are
  reliable.
- Do not hide uncertainty. A clearly labelled “not tested” result is safer than a
  green badge that nobody can defend.

## 6. North-star metrics

- **Evidence completeness:** percentage of steps with route, locator, result and
  artifact links.
- **Run reliability:** completed runs / started runs, excluding target outages.
- **Recovery rate:** runs resumed successfully after worker/browser interruption.
- **Grounding rate:** generated steps backed by a scanned element/API observation.
- **Flake rate:** repeated outcomes that change without an application change.
- **Security proof rate:** findings with reproducible request/response evidence and
  independent verification state.
- **Excel fidelity:** uploaded rows preserved and accounted for in exported results.
- **Time to diagnosis:** start of a failure to a categorized, evidence-backed owner.

## 7. Final positioning

PiOne Testmate is not trying to win by having the largest scanner. It wins by
making automation **defensible**: a senior can follow the chain from target route
to test step to proof to regression decision. Mature scanners become engines in the
graph; they do not replace the graph.
