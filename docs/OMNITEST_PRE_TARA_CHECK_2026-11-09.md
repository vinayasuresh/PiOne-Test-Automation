# Pre-Tara proof check — 2026-11-09

Branch: `v3-pre-tara-foundation`; HEAD `f3d8e6c` plus uncommitted working-tree changes. These results apply to that working tree, not to a clean release commit or CI run.

## Results

- Focused V3 feature flags, assurance, API contracts/workflow, dashboard, database assertion, and orchestration tests: **64 passed, 7 browser-marked tests deselected**, using a disposable SQLite test database. This is service/unit evidence, not a PostgreSQL or rendered-browser certification.
- Initial default-suite run inside the restricted process timed out while creating a Windows async socket. A host run against an isolated SQLite database completed: **275 passed, 2 failed, 85 browser-marked tests deselected**. Both failures were test fixture problems: an incomplete Playwright context double and a scenario prompt inconsistent with the strict grounded-planning rules.
- After correcting those fixtures, the host default suite with V3 and interactive-browser flags disabled passed: **277 passed, 85 browser-marked tests deselected**, one Starlette deprecation warning. The test process used an explicitly enabled isolated SQLite database; the running application's PostgreSQL configuration was not changed.
- The complete browser-marked suite did not finish: its controlled multi-module crawl exceeded a 45-second diagnostic timeout. This is not a full browser-suite result. A separate host run of `test_v3_dashboard_data_phase8.py -m browser` passed **7 tests**, including browser capture and match/mismatch/outage fixtures, against a separate SQLite test database. These fixture tests do not establish the complete rendered OmniTest workflow.
- The running Assurance Desk rendered in the browser with `DB: postgresql`. It listed many existing test-like incidents and scenario definitions; their provenance and ownership have not been assessed. Nothing was deleted or changed in the running application's database during the isolated suite.
- **Rendered API positive path:** On the running OmniTest app at `127.0.0.1:8000` with PostgreSQL active, pasted a one-operation OpenAPI 3 contract for public `GET https://jsonplaceholder.typicode.com/todos/1`. The UI displayed one parsed operation, then PASS / HTTP 200 / `contract_satisfied` for plan `plan_7597c07a14be`. The browser downloaded `omnitest_api_report_plan_7597c07a14be.xlsx`; inspection confirmed the 10 named sheets and a matching execution-result row. No credentials were required. The schema assertion was SKIP because this sample contract supplied no response schema; this is not proof of schema validation or auth, negative, retry or database cross-read workflows.

## Still needed

Run the browser-marked suite on a supported host and complete the V3 API negative/auth/schema matrix, dashboard/API mismatch, approved database/API/UI cross-read, recovery and idempotency checks, PostgreSQL restore/concurrency checks, and the reviewed-commit CI gate. Do not mark the pre-Tara release gate complete from these automated results alone.
