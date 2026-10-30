# V3 / V2 Compatibility Map

## Phase-0 purpose

V3 Client Assurance will extend PiOne Testmate; it will not replace the V2
eight-step workflow.  This map records the V2 records that future V3 incident,
API-contract and dashboard-data assertions must reference.

## Feature boundary

- Environment flag: `V3_ASSURANCE_ENABLED`
- Default: `false`
- Disabled behaviour: no V3 router, UI control, worker, database write or
  background job is registered.  The `/version` response reports only the flag
  state as additive build metadata.
- Enabled behaviour in Phase 0: still no V3 workflow is exposed.  Later phases
  must individually gate their routes, UI and workers with the same flag.

## Existing V2 system of record

| V2 concern | Existing record/service | Required V3 relationship |
| --- | --- | --- |
| Scan lifecycle and target scope | `ScanRun` in `api/db/models.py` and `RunContext` | `verification_job` references `scan_runs.id`; it must not duplicate crawl state. |
| Module, route and UI evidence | `ScanModule`, `ScanRoute`, `ScanElement`, `RouteEdge` | API/data assertions retain a route and selector reference to the existing run. |
| Recovery and user checkpoints | `Checkpoint` and `automation_framework/storage/checkpoint_store.py` | V3 attempts link to a checkpoint ID/version for restart evidence. |
| Generated scenarios | `TestPlan` and `TestCaseModel` | V3 can select only a registered, versioned scenario; no arbitrary generated code. |
| Execution result and step evidence | `Execution` and `ExecutionStep` | A verification attempt references one execution and its steps. |
| Downloads and durable evidence | `Artifact` plus report/export services | `evidence_manifest` stores artifact IDs/checksums, not copied secrets or raw payloads. |
| User-visible lifecycle | scan router and `frontend/index.html` eight-step workflow | V3 must be additive and never change scan, module selection, crawl, analyse, generation, execution or report semantics. |

## Mandatory compatibility rules for later phases

1. Existing V2 `run_id`, checkpoint IDs, execution IDs and artifact IDs remain
   authoritative.  V3 records link to them through foreign keys or stable IDs.
2. No V3 job may run merely because a V2 scan starts.  A durable, authorized
   incident or manually approved assurance trigger is required.
3. An expired browser session, tunnel outage, provider fallback or unavailable
   artifact is a `blocked`/`inconclusive` V3 outcome, not a target defect.
4. V3 evidence must redact credentials, cookies, authorization headers and
   sensitive response data before persistence or export.
5. V2 manual interactive checkpoints remain human-controlled.  V3 agents may
   not silently click Continue or execute arbitrary Python, shell, SQL, HTTP
   mutation or device command.

## Phase-0 evidence

- Configuration unit tests prove the flag is false when absent and only explicit
  true values enable it.
- The `/version` contract exposes the flag state without adding V3 operations.
- Focused V2 lifecycle tests remain the release gate for subsequent phases.
