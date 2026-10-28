# PiOne Testmate V2.0 Enterprise: Release Documentation Hub

This directory is the central repository for all human-authored documentation, formal requirements, operational user guides, verification scorecards, and audit histories for the **PiOne Testmate V2.0 Enterprise** baseline.

---

## 1. Directory Purpose & Architecture Boundary

### Authored Documentation vs. Application-Generated Artifacts

To maintain repository hygiene, clear audit provenance, and prevent accidental data loss, PiOne Testmate strictly separates human-authored engineering documentation from runtime-generated application artifacts:

| Category | Storage Path | Version Control | Description |
| :--- | :--- | :---: | :--- |
| **Authored V2 Documentation** | `docs/v2_release/` | **Tracked in Git** | Formal specifications, user guides, verification status, audit reports, and engineering handoff prompts. |
| **Authored Presentations** | `presentation/` | **Tracked in Git** | Executive pitch decks, presentation slide outlines, speaker notes, and demo walk-through scripts. |
| **Project Onboarding** | `README.md` (root) | **Tracked in Git** | Quickstart, installation, local setup, architecture overview, and onboarding instructions. |
| **Future V3 Blueprints** | `docs/` (root) | **Tracked in Git** | V3 roadmap and architecture planning (`V3_PHASE_ROADMAP.md`, `ANTIGRAVITY_V3_EXECUTION_PROMPT.md`). |
| **Generated Workbooks & Reports** | `automation_framework/reports/` | **Ignored / Runtime** | Generated 10-sheet Excel workbooks (`.xlsx`), JSON summaries, Markdown executive summaries, and YAML test plans. |
| **Generated Forensic Screenshots**| `automation_framework/screenshots/` | **Ignored / Runtime** | Step-by-step visual assertion screenshots (`{tc_id}_{step_id}.png`) and failure captures. |
| **Runtime Persistence & DB** | `storage/`, `*.db`, `pione_testmate.db` | **Ignored / Runtime** | SQLite database files, WAL logs, and file-based state locks. |
| **Scan Outputs & Raw Contexts** | `scans/`, `reports/run_contexts/` | **Ignored / Runtime** | Atomic RunContext JSON files, crawler session states, and raw DOM telemetry. |
| **Application Runtime Logs** | `logs/`, `*.log` | **Ignored / Runtime** | Backend FastAPI/Uvicorn server logs, crawler event streams, and debugging logs. |

> [!IMPORTANT]
> Files inside `automation_framework/reports/`, `automation_framework/screenshots/`, `scans/`, `storage/`, and `logs/` are dynamically created and updated during crawler and test execution runs. They should never be manually moved, edited, or conflated with authored governance documentation.

---

## 2. Document Catalog & Guide

The documents in this directory represent the authoritative V2 knowledge base. Below is an index of each document and its role in the release lifecycle:

### Core Requirements & User Operations
1. **[SOFTWARE_REQUIREMENTS_DOCUMENT.md](SOFTWARE_REQUIREMENTS_DOCUMENT.md)**
   - *Role:* Authoritative Engineering Specification
   - *Content:* Comprehensive functional requirements across all 8 workflow stages (Scan, Modules, Review, Crawl, Analyze, Generate, Execute, Report), non-functional requirements (performance, reliability, memory, browser concurrency), data persistence schemas, security guardrails (SSRF, credential stripping), and session lifecycle models.
2. **[END_TO_END_WORKFLOW_USER_GUIDE.md](END_TO_END_WORKFLOW_USER_GUIDE.md)**
   - *Role:* Operational Manual & Tester Field Guide
   - *Content:* Step-by-step instructions for operating PiOne Testmate via the web UI. Covers both Mock Demo mode and Live Application testing, authentication handling, interactive review checkpoints, test plan customization, controlled execution, report analysis, Saved Runs management, in-place session refresh, and regression replay.

### Verification Status & Release Audits
3. **[V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md](V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md)**
   - *Role:* Current Executive Release Status (Authoritative)
   - *Content:* Concise, evidence-backed statement of the current **"Conditional V2 baseline"**. Details verified metrics across Mock Demo (30 requested → 30 generated test cases, 82 passing execution steps) and live target runs, defines the remaining live multi-module release gate, and clarifies the bounded scope of passive security review.
4. **[V2_RELEASE_AUDIT_REPORT_2026-10-28.md](V2_RELEASE_AUDIT_REPORT_2026-10-28.md)**
   - *Role:* Comprehensive Technical & Architectural Release Audit
   - *Content:* Full-stack audit covering rendered browser release gates, SSRF defenses, lifecycle persistence, Windows port hygiene (preventing WinError 10013), and detailed findings from end-to-end execution testing.
5. **[v2_release_gate_verification_report.md](v2_release_gate_verification_report.md)**
   - *Role:* Automated Browser Release Gate Verification Ledger
   - *Content:* Step-by-step test execution record of `scripts/run_release_gate.py`, detailing the 9 browser release gate assertions, timing, selector grounding, and headless browser validation.
6. **[functional_audit_2026-10-28.md](functional_audit_2026-10-28.md)**
   - *Role:* 8-Step Functional Verification Matrix
   - *Content:* Tabular mapping of expected vs. observed behavior across every UI step in the 8-step workflow.

### Investigation & Historical Remediation Records
7. **[V2_LIVE_AUDIT_AND_REMEDIATION_2026-10-26.md](V2_LIVE_AUDIT_AND_REMEDIATION_2026-10-26.md)**
   - *Role:* Live Target Remediation Record
   - *Content:* Technical notes on stabilizing live SauceDemo execution, fixing structured selector validation, and ensuring execution continuity across pages.
8. **[V2_RELEASE_INTERACTION_AUDIT_2026-10-26.md](V2_RELEASE_INTERACTION_AUDIT_2026-10-26.md)**
   - *Role:* Manual Interaction & Checkpoint Audit
   - *Content:* Verification of Step 3 interactive review overlays, user-in-the-loop action classification, and learning checkpoints.
9. **[QA_PRODUCT_AUDIT_2026-10-26.md](QA_PRODUCT_AUDIT_2026-10-26.md)**
   - *Role:* Product & SDET Assessment
   - *Content:* Analysis of testing capabilities, assertion rigor, and comparison against industry QA automation tools.

### Security, Demo & Engineering Handoff
10. **[SECURITY_REVIEW.md](SECURITY_REVIEW.md)**
    - *Role:* Security Boundary & Passive DAST Specification
    - *Content:* Defines the non-destructive scope of the passive security scanner (HTTP headers, secure cookies, sensitive input masking, form action checks). Explicitly clarifies boundaries: this is passive observation, not active penetration testing (no SQL injection or XSS exploit execution).
11. **[SENIOR_DEMO_BRIEF.md](SENIOR_DEMO_BRIEF.md)**
    - *Role:* Stakeholder & Senior Management Demo Brief
    - *Content:* Strategic value propositions, high-level architecture overview, and key differentiation points for executive presentations.
12. **[ANTIGRAVITY_V2_LIVE_AUDIT_FIX_PROMPT.md](ANTIGRAVITY_V2_LIVE_AUDIT_FIX_PROMPT.md)**
    - *Role:* Engineering Handoff & Reproduction Prompt
    - *Content:* Standalone engineering prompt encapsulating context, reproduction steps, and constraints for subsequent development and maintenance sessions.

---

## 3. Requirements Traceability Matrix

The table below illustrates how core platform requirements defined in the **Software Requirements Document (SRD)** correspond to operational procedures in the **User Guide** and are validated in the **Verification Reports**:

| Workflow Stage | SRD Specification | User Guide Section | Primary Verification Evidence |
| :--- | :--- | :--- | :--- |
| **1. Scan Application** | SRD § 2.1 (SSRF Guard, Auth Capture, Mock Mode) | User Guide § 4.1 & § 5.1 | `V2_RELEASE_AUDIT_REPORT_2026-10-28.md` § 3.1 |
| **2. Choose Modules** | SRD § 2.2 (Semantic Navigation Extraction) | User Guide § 4.2 & § 5.2 | `v2_release_gate_verification_report.md` Gate 2 |
| **3. Manual Review** | SRD § 2.3 (Human-in-the-Loop Checkpoint) | User Guide § 4.3 & § 5.3 | `V2_RELEASE_INTERACTION_AUDIT_2026-10-26.md` |
| **4. Crawl Progress** | SRD § 2.4 (Bounded Traversal, Live Telemetry) | User Guide § 4.4 & § 5.4 | `V2_RELEASE_AUDIT_REPORT_2026-10-28.md` § 3.4 |
| **5. Analyze UI & Security** | SRD § 2.5 (Route Health, Passive DAST Guard) | User Guide § 4.5 & § 5.5 | `SECURITY_REVIEW.md`, `functional_audit_2026-10-28.md` |
| **6. Generate Tests** | SRD § 2.6 (Grounded Planning, Shortfall Guard) | User Guide § 4.6 & § 5.6 | `V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md` § 1 |
| **7. Execute Tests** | SRD § 2.7 (Playwright Sync, Step Screenshots) | User Guide § 4.7 & § 5.7 | `V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md` (82 passing steps) |
| **8. Audit & Report** | SRD § 2.8 (10-Sheet Workbook, JSON/YAML/MD) | User Guide § 4.8 & § 5.8 | `v2_release_gate_verification_report.md` Gate 8 |
| **Session Lifecycle** | SRD § 4.1 (In-Place Refresh `/session/refresh`) | User Guide § 7.1 | `V2_RELEASE_AUDIT_REPORT_2026-10-28.md` § 4.1 |
| **Data Persistence** | SRD § 5.0 (SQLite/PostgreSQL, Saved Runs) | User Guide § 8.0 | `V2_RELEASE_AUDIT_REPORT_2026-10-28.md` § 4.2 |

---

## 4. Current Verification Summary & Release Gate

As established in **[V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md](V2_CURRENT_VERIFICATION_STATUS_2026-10-29.md)**, the formal status of the platform is:

### Status: **CONDITIONAL V2 BASELINE**

#### Verified Strengths & Metrics
- **Deterministic Test Planning Grounding**: In rendered browser Mock Demo runs, requesting 30 grounded test cases produced exactly 30 test cases, 100% anchored to discovered routes and DOM elements.
- **Controlled Execution Reliability**: 30 generated test cases executed across 82 individual assertion and navigation steps with **82 Passed, 0 Failed, 0 Skipped**.
- **Full Artifact Export**: Generated 10-sheet Excel workbooks, JSON summaries, YAML test plans, and Markdown executive summaries all exported without corruption.
- **Saved Runs & Regression**: Saved run state persistence, baseline freezing, and session re-attachment verified.
- **Safe Security Boundaries**: Passive DAST collects security observations without injecting destructive payloads.

#### The Remaining Release Gate
To advance from **Conditional V2 Baseline** to **Unconditional Production Release**, the following single evidence requirement remains:
> A retained, rendered live multi-module test journey (e.g. against SauceDemo) that demonstrates:
> 1. Multi-module discovery and crawl initiation.
> 2. Intentional interruption at a human review checkpoint.
> 3. Clean **Retry / Resume** execution from the saved checkpoint without state loss.
> 4. Progression to the subsequent module checkpoint.
> 5. Generation, execution, and successful **Report** completion in that same rendered browser session.
