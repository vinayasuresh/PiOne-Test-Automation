# OmniTest by PiOne — Documentation Index and Current Delivery Status

**Status date:** 2026-11-09
**Authoritative use:** This page is the starting point for product, engineering, QA, security, DevOps and future Antigravity work. It distinguishes delivered behaviour from implementation that still needs rendered-browser or live-environment proof.

## Product boundary

**OmniTest by PiOne** is an evidence-first assurance platform built on the Testmate V2 workflow. It maps an authorised web application, generates tests only from saved evidence, executes bounded checks, and retains traceable results.

The existing V2 workflow remains the product backbone:

```text
Scan Application -> Choose Modules -> Manual Review -> Crawl Progress
-> Analyze -> Generate Tests -> Execute -> Report
```

V3 is additive. It must remain feature-gated and must not change V2 behaviour while `V3_ASSURANCE_ENABLED` is false.

## Truthful current status

| Area | Status | What is true today | What still needs proof or work |
| --- | --- | --- | --- |
| V2 functional assurance | **Delivered baseline** | Bounded browser discovery, module selection, manual review, crawl evidence, grounded planning, controlled execution, reports, artifacts, checkpoints, Saved Runs and regression baselines exist. | A full live multi-module interruption/retry/resume journey needs repeatable release evidence on an authorised target. |
| Persistence | **PostgreSQL primary locally** | Application data, run metadata and persisted artifacts are designed to use PostgreSQL. SQLite is an explicitly enabled isolated-test/recovery fallback, not the production source of truth. | Shared-environment TLS, least-privilege roles, backup/restore drill, retention and DBA/DevOps sign-off remain open. |
| V3 durable assurance foundation | **Implemented; targeted proof recorded** | Feature-gated incidents, verification jobs, attempts, assignment rules, evidence manifests, correlation IDs and duplicate protection exist as additive records. | Full rendered assurance-desk lifecycle, restart/retry and callback evidence must be completed before Tara integration. |
| V3 API contract testing | **Implemented; release proof incomplete** | Controlled OpenAPI/Swagger/Postman intake, bounded safe operation execution, secret redaction and API Excel reporting are present. | Browser proof of pass/fail/blocked/inconclusive, auth/negative/schema cases and exported workbook fidelity on a configured target are required. |
| Dashboard/API and DB/API/UI validation | **Foundation implemented** | Data assertions and approved read-only cross-read design are present. | Matching and intentional mismatch evidence, timezone/filter/rounding coverage, an approved database connection and browser-visible proof are required. |
| Passive security review | **Bounded only** | Passive, authorised observations and scope controls exist. | This is not confirmed exploit testing, full DAST, SQL injection/XSS verification or complete OWASP Top 10 coverage. |
| Tara / external chatbot / helpdesk integration | **Planned, not implemented** | Requirements and connector blueprint are documented. | Signed intake, tenant mapping, scenario authorisation, callback, status synchronization and operational support must be built after the pre-Tara gate passes. |
| IoT monitoring | **Future scope** | The architecture supports a future connector model. | MQTT/telemetry ingestion, stream rules, device health agents and physical-device verification are not delivered. |

## Documentation map

### Current product and operations

- [Root README](../README.md) — product overview, setup and platform boundary.
- [V2 Release Documentation Hub](v2_release/README.md) — V2 requirements, user guide and historical verification evidence.
- [Software Requirements Specification](v2_release/SOFTWARE_REQUIREMENTS_DOCUMENT.md) — V2 requirements plus the controlled V3 boundary.
- [DevOps CI/CD Guide](DEVOPS_CICD.md) — Docker, CI and environment/secret requirements.

### V3 strategy, execution and evidence

- [V3 Phase Roadmap](V3_PHASE_ROADMAP.md) — longer-term platform phases.
- [V3 Client Assurance Delivery Blueprint](V3_CLIENT_ASSURANCE_DELIVERY_BLUEPRINT.md) — business outcome, architecture and ownership model.
- [V3 Release Task List and Phase Prompts](V3_RELEASE_TASKLIST_AND_PHASE_PROMPTS.md) — implementation sequence and exit gates.
- [Pre-Tara Completion Task](OMNITEST_V3_PRE_TARA_EXECUTION_TASK.md) — release-proof criteria before external integrations.
- [Tara and Universal Bot Integration Blueprint](OMNITEST_TARA_AND_UNIVERSAL_BOT_INTEGRATION_BLUEPRINT.md) — the planned connector contract and responsibilities.
- [Antigravity V3 Execution Prompt](ANTIGRAVITY_V3_EXECUTION_PROMPT.md) — guarded engineering handoff prompt.

### Evidence and governance

- [Governance Decision Pack](PHASE0_GOVERNANCE_DECISION_PACK_2026-11-06.md) — open owner approvals.
- [Current pre-Tara check](OMNITEST_PRE_TARA_CHECK_2026-11-09.md) — historical evidence and remaining gate items.
- [Latest live UI audit](OMNITEST_LIVE_UI_AUDIT_2026-11-09.md) — audit observations; read it as evidence, not a blanket release certificate.
- [V3 compatibility map](V3_V2_COMPATIBILITY_MAP.md) — V2 surfaces V3 may reference but must not replace.

## Required delivery discipline

For every future phase: **inspect -> test -> fix confirmed defect -> retest -> record evidence -> update the relevant document -> commit only the phase-scoped change**.

Do not equate a source file, a visible UI control, or a passing unit test with completed product behaviour. Record whether proof is unit, API, rendered browser, controlled fixture, or authorised live environment.

## Immediate pre-Tara sequence

1. Browser-prove API contract intake, selection, execution outcomes, safe failure handling and Excel download.
2. Browser-prove dashboard/API matching and intentional mismatch classification.
3. Browser-prove an approved read-only database/API/UI comparison without secrets in logs or evidence.
4. Prove restart, retry, resume, cancellation, duplicate protection and artifact lineage.
5. Run V2 regression with V3 disabled and the release gate on a socket-capable CI runner.
6. Only then begin the signed Tara/external-bot connector.
