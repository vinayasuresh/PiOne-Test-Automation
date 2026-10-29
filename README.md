# Enterprise UI Automation Intelligence Platform

**Version**: `2.1.0` (Enterprise Lifecycle & Session Resiliency Edition)
**Core Architecture**: Playwright Sync Engine + Deterministic DOM State Fingerprinting + Bounded Safe Crawler + Multi-Provider LLM Test Planning + Session Lifecycle Guard + 10-Sheet Audit-Ready Workbook

---

## 1. Overview & Vision

The **Enterprise UI Automation Intelligence Platform** builds deterministic, trustworthy UI intelligence and test planning for modern web applications.

A user provides:
- One target application URL
- Valid credentials or an authenticated session
- Optional module selections (single, multiple, or all)
- Optional requirement documents, test case files, and testing scenario prompts

The platform:
1. **Locks to target boundary**: Enforces strict SSRF protection and path-namespace guards (blocks RFC 1918, loopbacks, link-local metadata `169.254.169.254`, CDNs, external domains, and logout URLs).
2. **Discovers application navigation**: Passively extracts sidebars, top navs, and icon menus into reviewable modules.
3. **Explores deeply**: Executes document scrolling, nested container deep-scanning, overlay inspection, and safe button probing without guessing.
4. **Enforces safety**: Diverts all destructive, submit, mutation, delete, or unknown actions into a reviewable **Parking Lot**.
5. **Supports human-assist review**: Pauses after each module with in-browser controls (`Confirm & Continue` vs `End Scan & Save`).
6. **Produces audit-ready artifacts**: Outputs JSON, YAML, Markdown, and a comprehensive **10-sheet Excel workbook**.
7. **Maintains immutable run context**: Binds every scan, test plan, execution, and regression run to a durable `run_id`.
8. **Protects session lifecycle**: Detects session expiry before execution and offers an **in-place session refresh** (`/session/refresh`) without losing scan results or run IDs.
9. **Executes controlled tests**: Executes safe visual and functional assertions, recording the exact locator used and capturing per-step screenshots.
10. **Tracks regression diffs**: Replays tests and calculates status changes, newly passing, and newly failing tests.
11. **Persists run history**: Stores runs, checkpoints, routes, elements, test plans, regression suites, executions, artifacts, and audit metadata in SQLite or PostgreSQL.
12. **Supports controlled cleanup**: Provides explicit Delete All controls for regression suites and generated artifacts while preserving the database and authentication state.

> **Guiding Principle**: *Deterministic scanner first, AI-assisted test intelligence second.*
> Playwright owns browser discovery and execution. AI models analyze saved evidence to generate test plans, but never drive the browser or bypass safety controls.

---

## 2. What Was Done (Platform v2.1 Upgrades)

### Immutable Run Context (`RunContext` & `run_context_service.py`)
- One single, immutable run context per end-to-end lifecycle.
- Tracks `run_id`, `target_origin`, `allowed_path_namespace`, `auth_session_id`, `selected_modules`, `discovered_modules`, `route_graph`, `ui_state_graph`, `element_registry`, `scan_results`, `generated_test_plan`, `execution_results`, `checkpoints`, and `artifact_paths`.
- Thread-safe in-memory cache with atomic `.tmp` disk persistence under `automation_framework/reports/run_contexts/{run_id}.json`.

### Authentication Session Lifecycle & Expiry Detection (`auth_lifecycle_service.py`)
- Opaque, server-side storage state validation (detects expired cookies, login redirects, and visible password fields).
- Probes live applications avoiding false login triggers on SPA root landing pages.
- Clear error contract on expired sessions: `HTTP 401: "Authentication expired. Refresh the session to continue."`.
- **In-Place Session Refresh**: `/session/refresh` re-authenticates the user and binds the new session state to the exact same `run_id` without restarting the scan or losing test plans.

### SSRF Protection & Boundary Enforcement (`security_guard.py`)
- Strict URL validation rejecting IPv4/IPv6 loopbacks (`127.0.0.1`, `::1`), private ranges (RFC 1918 `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), cloud metadata endpoints (`169.254.169.254`), non-HTTP schemes (`file://`, `data://`), and unsupported ports.
- Provides test-safe bypass (`ALLOW_LOCAL_TARGETS=1`) for local test suites.

### Consolidated Production Crawler (`CrawlerEngine`)
- **Single Traversal Pipeline**:
  1. Scope validation & document guard installation
  2. Page & network stabilization via mutation observers
  3. Base UI state fingerprinting
  4. Initial DOM extraction & 12-attribute registration
  5. Document & nested container deep-scrolling (`ViewportDeepScanner`)
  6. Menu & dropdown overlay capture with clean Escape closure (`MenuOverlayEngine`)
  7. Candidate action classification (`ActionClassifier`)
  8. Parking lot isolation of destructive/mutation buttons
  9. Safe button probing (`InteractionProbeEngine`) with parent state restoration
  10. Bounded child route traversal (`child_depth <= 5`) and loop cycle prevention
  11. Route health scoring (`RouteHealthScorer`)
  12. Module-level human review checkpoints and atomic persistence

### 10-Sheet Audit-Ready Excel Workbook (`crawl_exporter.py`)
Every crawl produces an audit-ready `.xlsx` workbook with all 10 standard enterprise sheets:
1. `working_routes`: Routes with health score >= 70.
2. `partial_routes`: Routes with health score 40-69 (warnings, slow APIs, or minor redirects).
3. `non_working_routes`: Routes with health score < 40 (broken or blank routes).
4. `menu_hierarchy`: Global navigation architecture and module hierarchies.
5. `element_map`: Complete element inventory with all 12 attributes and selector quality scores.
6. `parking_lot_actions`: Blocked/unconfirmed mutation actions requiring manual review.
7. `scan_events`: Deterministic, timestamped event stream log.
8. `test_cases`: Generated test plan cases and steps.
9. `execution_results`: Per-step test execution outcomes, locators used, console/network errors, and artifact paths.
10. `coverage_gaps`: Requirements not covered by discovered UI elements.

### Controlled Test Execution & Step Reruns (`test_execution_service.py`)
- Executes safe navigate and visibility assertion steps.
- Records `locator_used` (`role`, `aria`, `css`, `xpath`, or `text`) for every step.
- Captures per-step full-fidelity screenshots (`{tc_id}_{step_id}.png`).
- Captures console errors and network failures per step.
- Supports individual step rerun (`POST /rerun-step`) for fast troubleshooting.

### Persistence, Saved Runs & Regression Suites (`api/db/`)
- `api/db/models.py` defines persistence tables for runs, modules, routes, elements, checkpoints, test plans, regression suites, executions, artifacts, and audit events.
- `api/db/session.py` selects PostgreSQL when `DATABASE_URL` is configured and otherwise uses SQLite at `automation_framework/reports/pione_testmate.db`.
- `api/db/repository.py` centralizes database transactions, retries, run recovery, suite management, and artifact metadata.
- The dashboard exposes Saved Runs, Regression Suites, and Artifacts views.
- Explicit Delete All controls remove regression suites or generated artifacts after confirmation.

### 8-Step Enterprise UI (`frontend/index.html`)
- Professional 8-step enterprise workspace:
  - **Step 1**: Target URL & Credentials / Scan Application
  - **Step 2**: Authenticated Module Selection
  - **Step 3**: Manual Review Checkpoint
  - **Step 4**: Deep Crawl Progress & Live Telemetry
  - **Step 5**: Scanned UI Map & Route Analysis
  - **Step 6**: Requirement-Aware Test Generation (Neutral LLM Gateway)
  - **Step 7**: Controlled Execution & Per-Step Screenshot Evidence
  - **Step 8**: Audit Report, Downloads & Regression Replay
- Top-level workspace views include Current Run, Saved Runs, Regression Suites, and Artifacts.
- Saved runs can be resumed, duplicated, archived, or discarded.
- Regression suites support named baselines, filters, selection, duplication, execution, and bulk deletion.
- The Artifacts view lists only runs that still have downloadable generated files.
- Real-time Session Expiry Warning Banner with quick-action modal to re-authenticate without restarting.
- Zero destructive browser actions (`localStorage.clear()` removed).

---

## 3. Current Version Details

| Component | Version / Specification |
| :--- | :--- |
| **Platform Version** | `2.1.0` (Enterprise Lifecycle & Session Resiliency Edition) |
| **API Version** | `2.1.0` (FastAPI) |
| **Schema Version** | `2.1.0` |
| **Python Compatibility** | Python 3.11+ |
| **Browser Engine** | Playwright Chromium |
| **Verification** | Run the complete unit, integration, and Playwright suite locally; browser tests require normal local socket permissions. |
| **Live Proof** | SauceDemo, DemoQA, and The Internet are supported test targets; each run is isolated by `run_id`. |

### 3.1 Verified V2 status — 2026-10-29

PiOne Testmate is a **V2 baseline under active verification**, not an
unqualified production certification. The following evidence is current:

- deterministic planning was browser-verified to honour requested counts of
  `1`, `3`, `10`, and `30` where enough grounded safe controls existed;
- a rendered Mock Demo journey covered discovery, crawl, Analyze, planning,
  execution, report downloads, baseline saving, and Saved Runs; the 30-case
  execution recorded 82 passing step rows with no failure rows;
- focused browser tests passed for manual-interaction capture and its
  Continue / End-and-Save checkpoint decisions (`4 passed`);
- component, planning, persistence, lifecycle, execution-reliability, and
  security/boundary suites have been run in focused groups.

The remaining V2 release evidence is a complete **live multi-module** journey
that intentionally interrupts at a human checkpoint, retries from its saved
checkpoint, resumes to the next checkpoint, and reaches Report in the same
rendered browser session. Do not represent passive security review as active
SQL injection/XSS testing or as full OWASP Top 10 coverage.

For complete release specifications, requirements, end-to-end user guides, and verified audit reports, consult the [V2 Release Documentation Hub](docs/v2_release/README.md).

## 3.2 Product Positioning & V3 Direction

PiOne Testmate is currently a strong local/product foundation for **evidence-first
functional assurance**. Its differentiator is not generic AI test generation. It is
the traceable chain from a real scanned route and UI element to a generated step,
execution result, screenshot, security observation and regression decision.

The V3 product concept is the **Assurance Graph**:

```text
target → session → module → route/state → element/API evidence
       → test step → execution proof → finding/diff → verified decision
```

This deliberately keeps Playwright and safety rules authoritative while AI proposes
plans from saved evidence. The security layer remains bounded and honest; deeper DAST
coverage should be integrated from specialist engines such as OWASP ZAP or Burp
Suite rather than presented as passive checks.

The V3 execution blueprint, senior-demo script and copy/paste engineering prompt are
maintained here:

- [V3 phase roadmap](docs/V3_PHASE_ROADMAP.md)
- [Senior demo brief](docs/v2_release/SENIOR_DEMO_BRIEF.md)
- [Antigravity V3 execution prompt](docs/ANTIGRAVITY_V3_EXECUTION_PROMPT.md)

The current release should not claim complete OWASP Top 10 coverage, confirmed SQL
injection/XSS detection, or enterprise multi-tenant scale. Those are explicitly
tracked as future work and coverage gaps.

---

## 4. Prerequisites & Setup

### Prerequisites
- **Python**: Version 3.11 or higher installed.
- **Git**: Installed and configured.
- **OS**: Windows, macOS, or Linux.

### Installation

1. **Clone the repository and enter directory**:
   ```bash
   git clone https://github.com/your-org/2026_auto_test.git
   cd 2026_auto_test
   ```

2. **Create and activate a virtual environment**:
   - **Windows (PowerShell)**:
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   - **macOS / Linux**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Install Playwright Chromium browser**:
   ```bash
   playwright install chromium
   ```

---

## 5. How to Run

### Method 1: Start the Web Dashboard & API (Recommended)

Start the FastAPI application with auto-reload:
```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser to:
- **Interactive UI Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Interactive OpenAPI Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Method 2: Command-Line Interface (CLI)

Run the standalone CLI:
```bash
python main.py
```
Follow the interactive prompts to enter the target URL, supply credentials, and review discovered modules.

### Method 3: Running the Automated Test Suite

Run the default Windows-safe suite (non-browser tests only, browser tests are explicitly marked `@pytest.mark.browser`):
```bash
python -m pytest automation_framework/tests -v
```

Run browser/Playwright tests explicitly when local browser execution is available:
```bash
python -m pytest automation_framework/tests -m browser -v
```

Run a bounded acceptance checklist runner (machine-readable JSON summary):
```bash
python tools/acceptance_runner.py
```

### Method 4: Running the Live SauceDemo End-to-End Proof

Execute the 10-step live integration proof against SauceDemo:
```bash
python -c "
from scratch.live_saucedemo_proof import run_proof
run_proof()
"
```
Or execute the script directly:
```bash
python .system_generated/scratch/live_saucedemo_proof.py
```

### Ollama Setup

Use these Windows PowerShell commands from the repository root:

```powershell
Copy-Item .env.example .env
ollama serve
ollama pull llama3.1:8b
uvicorn api.main:app --reload --port 8000
```

PiOne Testmate auto-loads `.env` at startup. Keep `.env` local only; it is ignored by Git.

---

## Database Configuration

Local development uses SQLite automatically when no `DATABASE_URL` is present:

```text
automation_framework/reports/pione_testmate.db
```

To use PostgreSQL, configure `.env` and restart the API:

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/pione_testmate
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_SSL_MODE=prefer
```

The dashboard database indicator and `GET /system/db-status` show the active engine and connection state. PostgreSQL is optional for a single-user local run and becomes useful for shared history, concurrent users, scheduled regression, and audit retention.

## Cleanup Controls

The dashboard provides explicit destructive actions with confirmation dialogs:

- `DELETE /regression/suites` removes all regression suite records.
- `DELETE /artifacts` removes generated report files and artifact metadata while preserving the database file, saved run records, authentication state, and run-context storage.
- `GET /artifacts` returns only runs that still have downloadable artifact files.

Cleanup does not delete saved runs automatically. This preserves checkpoints and run history for recovery; delete or archive runs separately when that is intended.

## 6. Repository Architecture

```text
2026_auto_test/
├── api/                                # FastAPI Web Platform
│   ├── main.py                         # App entrypoint (v2.1.0)
│   ├── models/
│   │   ├── run_context.py              # Immutable RunContext Pydantic model
│   │   └── scan_models.py              # Pydantic scan, plan, execution & session models
│   ├── routers/
│   │   ├── scan.py                     # Scan, refresh, plan, execute & regression endpoints
│   │   └── scan_events.py              # Server-Sent Events (SSE) router
│   ├── services/
│   │   ├── auth_lifecycle_service.py   # Expiry detection & in-place session refresh
│   │   ├── crawler_service.py          # Crawler orchestration service
│   │   ├── llm_provider_gateway.py     # Neutral multi-provider LLM gateway
│   │   ├── llm_service.py              # Test planning orchestration
│   │   ├── run_context_service.py      # RunContext cache & atomic persistence
│   │   ├── test_execution_service.py   # Controlled execution, locators, screenshots
│   │   └── test_input_normalizer.py    # Requirement & prompt normalizer
│   └── utils/
│       ├── response_builder.py         # ScanResponse builder
│       └── security_guard.py           # SSRF & boundary security protection
├── automation_framework/               # Core Engine
│   ├── config/
│   │   └── settings.py                 # Timeouts, limits, paths
│   ├── crawler/
│   │   ├── action_classifier.py        # Safe vs parking lot classifier
│   │   ├── auth_exceptions.py          # Common AuthenticationExpiredError
│   │   ├── crawl_exporter.py           # 10-sheet Excel, JSON, YAML, Markdown exporter
│   │   ├── crawler_engine.py           # Consolidated production crawler
│   │   ├── interaction_probe_engine.py # Safe button & tab prober
│   │   ├── interactive_capture.py      # Module review overlay injection
│   │   ├── login_handler.py            # Form & credential authentication
│   │   ├── menu_detector.py            # Navigation shell & menu detector
│   │   ├── menu_overlay_engine.py      # PrimeNG/Material overlay expander
│   │   ├── route_graph.py              # Directed graph with state transitions
│   │   ├── route_health_scorer.py      # Deterministic route health (0-100)
│   │   ├── route_tracker.py            # Visited URL & graph coordinator
│   │   ├── session_manager.py          # Playwright context & storage state
│   │   ├── shell_detector.py           # Application shell discovery
│   │   ├── ui_state_fingerprint.py     # DOM state fingerprinting & quality score
│   │   ├── ui_wait_engine.py           # DOM settlement & stability engine
│   │   ├── url_filter.py               # Boundary guard & URL scope enforcer
│   │   └── viewport_deep_scanner.py    # Tall document & container scrolling
│   ├── engine/
│   │   ├── element_identity_registry.py# 12-attribute registry & deduplication
│   │   └── ui_intelligence_engine.py   # DOM extraction & category mapping
│   ├── reports/                        # Saved artifacts, Excel workbooks, screenshots
│   │   ├── auth_sessions/              # Server-side browser storage states
│   │   └── run_contexts/               # Durable RunContext JSON files
│   ├── storage/
│   │   └── checkpoint_store.py         # Windows NTFS-safe atomic store
│   └── tests/                          # Unit, integration, and Playwright tests
│       ├── fixtures/
│       │   └── test_spa_server.py      # Embedded in-memory test SPA server
│       ├── test_action_classifier.py
│       ├── test_boundary_guard.py
│       ├── test_crawler_components.py
│       ├── test_crawler_e2e.py
│       ├── test_llm_gateway.py
│       ├── test_run_context_and_auth_lifecycle.py
│       ├── test_test_planning_service.py
│       └── test_ui_state_and_module_selection.py
├── frontend/                           # Integrated Web Dashboard
│   └── index.html                      # 8-step enterprise workspace
├── main.py                             # Interactive CLI entrypoint
├── pytest.ini                          # Pytest configuration
└── requirements.txt                    # Python dependencies
```

---

## 7. Security & Safety Controls

An authorized **VAPT Assessment** panel is available in **Analyze** after a crawl.
Choose functional-only, security-review-only, VAPT-only, or the combined flow.
It performs bounded anonymous HTTP checks with optional low-impact CORS/reflection
probes, saves run-owned findings, and exports JSON/Markdown/Excel. This is an initial
bounded VAPT feature, not full ZAP or OWASP Top 10 coverage. See
[security review scope, usage and limitations](docs/v2_release/SECURITY_REVIEW.md).

- **SSRF Protection (`security_guard.py`)**: Rejects localhost, loopback, private RFC 1918 addresses, and link-local cloud metadata addresses.
- **Strict Same-Origin Boundary**: Document scope guards actively block redirects or link navigation outside the target domain and namespace.
- **Zero Credential Leakage**: Credentials, cookies, and raw tokens are stripped from all exports and LLM prompts.
- **Zero Destructive Actions**: The crawler will never automatically click delete, submit, save, payment, logout, or file upload buttons.
- **Auditable Records**: All skipped actions and parking lot items are preserved in the Excel `parking_lot_actions` sheet for manual review.
