# OmniTest by PiOne — Enterprise Assurance Platform

**Version**: `2.1.0` (Enterprise Lifecycle & Session Resiliency Edition)

---

## Overview & Vision

OmniTest by PiOne is an evidence‑first assurance platform built on the PiOne Testmate V2 workflow. It discovers authorised web applications, generates grounded test cases from real UI evidence, executes bounded checks, and produces audit‑ready artifacts.

## Product Boundary

- **V2 Baseline** (delivered & validated): Scan → Module Selection → Manual Review → Crawl → Analyze → Generate Tests → Execute → Report.
- **V3 Extensions** (feature‑gated by `V3_ASSURANCE_ENABLED=true`): Durable assurance graph, API contract testing, dashboard‑API validation, database‑UI cross‑read, and future Tara/chatbot integration.

## Current Delivery Status (as of 2026‑11‑09)

| Area | Status | Delivered / Implemented | Proof Needed |
|------|--------|--------------------------|--------------|
| V2 functional assurance | **Delivered baseline** | Full V2 workflow with PostgreSQL persistence, checkpointing, saved runs, regression baselines, and 10‑sheet Excel workbooks. | Live multi‑module interruption/retry/resume proof on an authorised target. |
| Persistence | **PostgreSQL primary** | All run metadata, artifacts, and audit records stored in PostgreSQL. SQLite used only for explicit isolated test fallback. | TLS, role‑based access, backup/restore drills. |
| V3 durable assurance foundation | **Implemented; targeted proof recorded** | Incident, verification job, attempt records, evidence manifest, duplicate protection exist. | Full rendered assurance‑desk lifecycle, restart/retry proof, Tara integration gated. |
| V3 API contract testing | **Implemented; release proof incomplete** | OpenAPI/Swagger/Postman ingestion, safe operation execution, secret redaction, Excel reporting. | Browser‑level pass/fail/blocked outcomes, auth/negative cases, workbook fidelity. |
| Dashboard/API & DB/UI validation | **Foundation implemented** | Data assertions and read‑only cross‑read design present. | Matching/mismatch evidence, timezone handling, approved DB connection, browser proof. |
| Passive security review | **Bounded only** | Authorized observations (headers, cookies, CORS) without destructive testing. | No full OWASP Top 10 exploit verification. |
| Tara / chatbot / helpdesk integration | **Planned** | Requirements and connector blueprint documented. | Signed intake, tenant mapping, scenario authorisation, callbacks, operational support to be built post‑pre‑Tara gate. |
| IoT monitoring | **Future scope** | Architecture supports future connectors. | MQTT/telemetry ingestion, device health agents not delivered. |

## Documentation Map

- **Root README** – this overview and status table.
- **V2 Release Documentation Hub** – `docs/v2_release/README.md` (requirements, user guide, verification evidence).
- **V3 Strategy & Evidence** – `docs/OMNITEST_DOCUMENTATION_INDEX.md`, `V3_PHASE_ROADMAP.md`, `V3_CLIENT_ASSURANCE_DELIVERY_BLUEPRINT.md`, `V3_RELEASE_TASKLIST_AND_PHASE_PROMPTS.md`.
- **Pre‑Tara Execution Task** – `docs/OMNITEST_V3_PRE_TARA_EXECUTION_TASK.md`.
- **Tara Integration Blueprint** – `docs/OMNITEST_TARA_AND_UNIVERSAL_BOT_INTEGRATION_BLUEPRINT.md`.
- **Antigravity Execution Prompt** – `docs/ANTIGRAVITY_V3_EXECUTION_PROMPT.md`.
- **DevOps CI/CD Guide** – `docs/DEVOPS_CICD.md`.
- **Evidence & Governance** – audit reports, live UI audit, governance packs under `docs/`.

---

## Usage Guidelines

- **Do not claim full release certification** unless the rendered‑browser V2 lifecycle, exports, persistence, recovery, and regression flows are proven.
- **Feature gating**: V3 features are invisible when `V3_ASSURANCE_ENABLED=false`.
- **Primary database**: PostgreSQL is the source of truth; SQLite is only for isolated test fallback.
- **Documentation integrity**: Preserve existing comments and docstrings; use `git mv` for moving authored docs if needed.
- **No secrets**: Never expose passwords, tokens, or internal URLs in docs or UI.
