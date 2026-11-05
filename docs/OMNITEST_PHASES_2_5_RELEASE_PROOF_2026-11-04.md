# OmniTest V3 Phases 2–5 Release Proof — 2026-11-04

Branch: `v3-pre-tara-foundation`
Scope: API contract testing, dashboard/API validation, API/database cross-read
validation, and the pre-Tara release gate. Existing V2 behavior remains
feature-gated and was not replaced.

## Executive result

Phases 2–4 are implemented and pass their automated contract/service tests.
Phase 5 passes its source, migration, and focused release-gate checks, but a
rendered-browser release certification is **blocked on this Windows host**:
port 8000 cannot bind and the async test runtime cannot create its local
socket pair (`WinError 10013`). No browser screenshot or live Chromium pass is
claimed without that proof.

## Phase status

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| 2 | OpenAPI/Swagger/Postman ingestion, ordered operations, SSRF policy, safe request construction, auth, schema/status/latency assertions, redacted 10-sheet report | **Implemented and automated-test verified** | `test_v3_api_contract_phase2.py`, `test_v3_api_testing_workflow.py` |
| 3 | Dashboard ↔ API comparisons, KPI/table/chart/filter/date/time-zone/rounding/staleness classifications | **Implemented and automated-test verified** | `test_v3_dashboard_data_phase3.py`, orchestration tests |
| 4 | Approved read-only database queries and DB ↔ API ↔ UI three-way classifications with redacted evidence | **Implemented and automated-test verified** | `test_v3_database_assertion_phase4.py`, orchestration tests |
| 5 | V2-preserving release gate, migration, recovery/retry/duplicate checks, export/evidence contracts, rendered-browser proof | **Code gate passed; live browser proof blocked** | focused tests and fresh migration pass; browser host limitation below |

## Automated evidence

Commands executed from the repository root:

```text
python -m pytest -q automation_framework/tests/test_v3_api_contract_phase2.py automation_framework/tests/test_v3_api_testing_workflow.py automation_framework/tests/test_v3_dashboard_data_phase3.py automation_framework/tests/test_v3_database_assertion_phase4.py automation_framework/tests/test_v3_orchestration_phase45.py automation_framework/tests/test_interactive_session_phases.py automation_framework/tests/test_interactive_release_gate.py --tb=short
62 passed in 2.60s

python -m pytest -q <all test_v3_*.py> automation_framework/tests/test_interactive_session_phases.py automation_framework/tests/test_interactive_release_gate.py --tb=short
78 passed, 7 deselected in 3.17s

python -m pytest -q automation_framework/tests/test_interactive_session_phases.py automation_framework/tests/test_interactive_release_gate.py --tb=short
14 passed in 1.17s

python -m compileall -q api automation_framework/tests
PASS

python -m alembic upgrade head   # fresh temporary SQLite database
PASS through j0a1b2c3d4e5

git diff --check
PASS (only legacy CRLF normalization warnings)
```

The API workflow tests cover OpenAPI 3, Swagger 2, Postman v2.1, source-order
operation parsing, SSRF/private-address rejection, bearer/API-key/basic/OAuth
vault handling, deterministic parameter precedence, mutation confirmation,
schema and required-field assertions, latency/status checks, and secret-free
report cells. Dashboard and database tests cover mismatch classification,
stale/filter/time-zone/rounding cases, approved read-only SQL, mutation
blocking, redaction, and three-way parity outcomes.

## Browser and V2 release-gate boundary

The repository includes the rendered-browser verifier:
`automation_framework/tests/browser_validate_api_testing.py`. It now accepts
`OMNITEST_BASE_URL` so the same proof can run on a different local port or
host. It could not complete here because:

1. port `8000` is not persistently bindable on this host (`WinError 10013`);
   attempting the same proof on port `8001` produced the same listener error;
   and
2. the same restriction prevents the async test client from creating its
   socket pair, causing the legacy `/system/db-status` test to time out.

This is an execution-host limitation, not evidence that the API workflow is
correct in a real browser. Before release, run the verifier on a socket-capable
runner with Chromium installed and capture its full-page screenshot, operation
table, execution counters, Excel download, and DB/API/UI classification.

The existing local SQLite file also predates the latest Alembic history and
has legacy schema drift. A fresh database migrates cleanly; reconcile or
backup the developer database before any destructive schema operation.

## Release decision

**Do not start Tara integration yet.** The next required gate is a real
rendered-browser run on a socket-capable machine, followed by the V2 suite
with V3 disabled. Once those two checks pass, this branch is ready for the
Tara connector work. No commit was created by this verification pass.
