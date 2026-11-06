# OmniTest V3 Phase 0 Governance Decision Pack

**Prepared:** 2026-11-06
**Branch:** `v3-pre-tara-foundation`
**Purpose:** give the accountable owners a concise, reviewable P1–P8 / DB-01 decision record so the governance portion of Phase 0 can close without treating recommendations as approvals.

## Current decision

**Technical V2 protection gate: passed for the recorded local run. Governance Phase 0: still open.** The application team can prepare recommendations and evidence; only the named company owners can authorize pilot scope, data handling, deployment, and release thresholds. No approval or signer identity is inferred here.

The V2 evidence is recorded in [the Phase 0 regression proof](OMNITEST_V2_PHASE0_LIVE_REGRESSION_PROOF_2026-11-06.md). It used the built-in Mock Demo and an isolated local test database. It is not customer application evidence or a production release approval.

## Recommended decisions for owner review

| ID | Accountable role | Recommended decision | Required owner action / proof |
|---|---|---|---|
| P1 | Product head; Legal for external naming | Standardize the product as **Pi One OmniTest** and the current delivery as **V3 pre-Tara foundation**. Use product/version names consistently in UI, reports, docs, and demos. | Approve the product/release labels; Legal confirms external trademark clearance before launch. |
| P2 | Product head and each pilot application owner | Select three pilot targets and environments. Proposed candidates: controlled Mock Demo fixture; CAT Sentinel staging; Pione Zentri staging. CAT Sentinel and Pione are candidates only until their respective owners approve scope and provide test accounts. | Name one release owner and test role per target; attach written authorization and safe fixture/data instructions. |
| P3 | Support lead / CAT Sentinel owner | Use Tara on CAT Sentinel as the first chatbot/helpdesk connector after the pre-Tara evidence gate. Keep the connector contract vendor-neutral so other ticket systems can be added later. | Approve Tara as pilot connector and identify a non-production tenant and integration owner. |
| P4 | QA lead and Release owner | Start with explicit hard gates, not an uncalibrated composite score: all designated must-pass checks pass or have a named, reasoned release waiver; zero open Critical findings; blocked/inconclusive outcomes stay distinct from pass/fail. Calibrate numeric weights and threshold using pilot data before enabling a score-based release block. | Approve must-pass scenarios, severity mapping, waiver authority, and when/how a numeric score is calibrated. |
| P5 | Product head and DBA/DevOps | Keep SQLite for local single-operator V2/demo use. Prefer PostgreSQL for shared multi-user V3 only after the deployment, migration, rollback, backup/restore, encryption, and ownership plan is approved. Keep OmniTest platform storage separate from a customer database used for read-only cross-validation. | Decide the hosting model and identify the authoritative platform DB. Approve data-preserving migration and recovery plan. |
| P6 | Product head, Security and Data owner | Make client-data model training opt-in only; default to no training on customer data. Select local/cloud planner per deployment, with redaction, retention, region, and provider controls documented. | Approve model/provider choices, data residency, retention, and customer consent language. |
| P7 | Security lead and pilot application owner | Default pilot scope to staging. Production verification is read-only and requires written authorization, approved role/account, defined origin and test window. Destructive UI/API/SQL operations, physical-device commands, and unattended schedules remain disabled unless separately reviewed and approved. | Approve the scope, allowlist, account, test window, and any exception before an active run. |
| P8 | Product head and Legal | Exclude native mobile automation and in-app AI from the initial V3 pilot unless separately approved. Clear OmniTest, Pulse, and DevAssist names before external use. | Record the scope decision and legal naming review. |
| DB-01 | Product owner and DBA/DevOps | Treat local PostgreSQL `Omni_Test` as an unclassified candidate, not as a selected OmniTest DB. A local read-only connection succeeded and showed zero visible tables in `public`; this does not prove application configuration, schema ownership, or intended use. For shared deployment, create a dedicated least-privilege application role; do not use a database administrator account. | Confirm database purpose, owner/schema, host/environment, dedicated role, TLS/auth, migrations, backup/restore and rollback. Keep credentials in an approved secret store; never put them in this document or tracker. |

## Phase 0 governance close checklist

- [ ] Product head records P1, P2, P5, P6, and P8 decisions or assigns named owners and due dates.
- [ ] Security lead approves P6/P7 controls and the pilot authorization/evidence policy.
- [ ] Each pilot owner signs the target/environment/account/safe-data authorization for P2/P7.
- [ ] QA and Release owners sign P4 must-pass, severity, waiver, and calibration rules.
- [ ] DBA/DevOps confirms P5 and DB-01 platform topology, data-preserving migration, backup/restore, and rollback.
- [ ] Support lead approves P3 Tara pilot and identifies its non-production tenant/owner.
- [ ] Tracker rows GOV-01 through GOV-04 change to `Complete` only when the above signed evidence is linked in the tracker.

## Decision record fields

For each decision, record:

```text
Decision ID:
Approved / rejected / deferred:
Decision and constraints:
Accountable approver name and role:
Approval date:
Evidence or meeting record link:
If deferred: named owner and due date:
```

## What this pack does not authorize

This recommendation pack does not authorize testing any live customer or production target, writing to a customer database, enabling unattended schedules, storing credentials in the repository, choosing a database on behalf of the DBA, or closing governance tracker items. The local test credential shared for the database check was intentionally used only for that local connectivity check and is not retained here.
