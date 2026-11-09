# OmniTest by PiOne — Tara and Universal Support-Bot Integration Blueprint

**Status:** Planned architecture. No production Tara, CAT Sentinel, Teams, Jira, ServiceNow or other customer connector is claimed as implemented by this document.
**Prerequisite:** Complete the pre-Tara release gate in [OMNITEST_V3_PRE_TARA_EXECUTION_TASK.md](OMNITEST_V3_PRE_TARA_EXECUTION_TASK.md).

## Goal

Connect Tara or another customer chatbot/helpdesk to OmniTest so an authorised user complaint can become a **durable, policy-bounded verification job**. The result must be a trustworthy outcome and evidence for the support owner, not an unsupported AI judgement.

```text
Customer / employee
        |
        v
Tara or another support bot
        |  signed, structured ticket event
        v
OmniTest connector gateway
        |  validate tenant, environment, role, scenario and idempotency key
        v
Incident + verification job
        |  approved UI / API / data checks only
        v
Evidence manifest + outcome
        |  signed callback / connector update
        v
Helpdesk / Super Admin / support bot response
```

## One universal connector model

OmniTest should expose a provider-neutral connector contract rather than build Tara-specific business logic into the core test engine.

### Inbound adapter

Each bot/helpdesk adapter translates its native event into a minimum OmniTest incident request:

```json
{
  "event_id": "provider-unique-event-id",
  "correlation_id": "tenant-scoped-unique-id",
  "tenant_id": "customer-tenant",
  "ticket_reference": "SUP-123",
  "summary": "Monthly report download fails",
  "affected_feature": "Reports",
  "expected_behavior": "A valid XLSX file is downloaded",
  "actual_behavior": "The download returns an error",
  "authorised_target_id": "staging-reports",
  "authorised_role_id": "standard_user",
  "scenario_reference": "reports.download.v1",
  "priority": "medium",
  "occurred_at": "2026-11-09T12:00:00Z"
}
```

The intake gateway must:

- authenticate the provider with a per-tenant secret or OIDC workload identity;
- verify an HMAC signature, timestamp and nonce; reject expired or replayed events;
- map tenant, application target, environment, role and scenario from approved configuration;
- use `correlation_id` plus the provider event ID for idempotency;
- store only a sanitised structured summary, never a raw conversation transcript, password, cookie or bearer token;
- create the support incident first, so a support record exists even if testing is blocked.

### Outbound adapter

The receiving system gets a minimal status callback and an evidence link, for example:

```json
{
  "correlation_id": "tenant-scoped-unique-id",
  "ticket_reference": "SUP-123",
  "status": "verification_complete",
  "outcome": "reproduced",
  "failure_category": "application_defect",
  "tested_conditions": {
    "target": "staging-reports",
    "role": "standard_user",
    "scenario": "reports.download.v1"
  },
  "evidence_reference": "opaque-evidence-id",
  "next_action": "Assign Reporting team"
}
```

Use an outbox table plus retry/backoff and dead-letter handling. Callbacks must be signed, idempotent and auditable. A callback failure must never delete or re-run a completed verification.

## Outcome language

OmniTest reports one of the following outcomes:

| Outcome | Meaning |
| --- | --- |
| `reproduced` | The approved scenario observed the reported failure with evidence. |
| `not_reproduced` | The tested conditions worked. It does not invalidate the user’s report. |
| `blocked` | Required access, test data, approved target, dependency or environment was unavailable. |
| `inconclusive` | The evidence was insufficient or inconsistent. |

Separately record the technical category: application defect, test defect, infrastructure failure, configuration issue, access issue, or unknown. A tunnel error, authentication expiry or target timeout must not be labelled as a customer application defect.

## Customer responsibility versus OmniTest responsibility

| Customer team supplies/owns | OmniTest platform supplies/owns |
| --- | --- |
| Authorised targets, non-production test accounts, approved roles, scenario expectations and test data | Connector SDK/adapter contract, intake validation, idempotency, policy enforcement and evidence lifecycle |
| Bot/helpdesk event configuration and callback endpoint approval | Bounded UI/API/database verification orchestration |
| Data classification, retention policy, owner/team routing and incident SLA | Redaction, secret reference handling, audit events and report generation |
| Read-only database account and approved query definitions | Query allow-list enforcement and parity evidence |
| Support response wording and final ticket closure | Result callback with factual outcome and evidence reference |

## Security and tenant isolation

- Store connector secrets in a managed secret store and reference them by opaque ID; never return them to the browser, logs, database exports or artifacts.
- Use tenant-scoped encryption keys, row-level tenant ownership checks and role-based access control.
- Enforce target and scenario allow-lists. No chatbot text can create arbitrary URLs, SQL, shell commands, browser scripts or mutation payloads.
- Require an approved non-production or explicitly authorised environment for state-changing tests. Read-only checks are the default.
- Redact sensitive response fields and control artifact retention and download permissions.
- Preserve every decision with correlation ID, actor, policy result, scenario version and evidence manifest ID.

## IoT scope boundary

The same event pattern can later accept health or telemetry events, but this is a separate capability:

1. **Portal assurance:** test dashboard, roles, reports and API behaviour.
2. **Telemetry assurance:** compare approved device/API readings for freshness and threshold rules.
3. **Physical-device assurance:** requires device-specific adapters, a safe laboratory or explicit operational approval.

MQTT ingestion, stream processing and physical command/control are not current OmniTest features. They should begin only after portal/API/data validation and connector security are release-proven.

## Entry and acceptance criteria

Start connector delivery only when pre-Tara API, dashboard, database, recovery and V2-protection gates are proven. The first pilot must demonstrate:

1. one signed ticket event creates one incident and one job;
2. duplicate delivery creates no duplicate work;
3. an authorised scenario executes with a declared role and environment;
4. the callback carries a factual result and safe evidence link;
5. a blocked test remains a ticket and is visible to the assigned owner;
6. retest after a fix updates the same incident history without overwriting prior evidence.
