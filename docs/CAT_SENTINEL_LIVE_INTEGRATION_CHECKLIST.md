# CAT Sentinel / Tara Live Integration Checklist

**Status:** Gated Pre-Integration Specification
**Target Environment:** Staging Pilot
**Recipient Teams:** CAT Sentinel Backend, Tara Chatbot, Support/Helpdesk, DevOps & Security

> **Note:** OmniTest's Tara connector foundation is verified in code and local contract tests, but remains feature-gated (`TARA_INTEGRATION_ENABLED=false`). Live production or staging integration cannot be certified until the following checklist items are agreed, delivered, and verified in a live staging exchange.

---

## 1. Integration Prerequisites Checklist

| # | Integration Requirement | CAT Sentinel Deliverable / Specification | OmniTest Requirement | Status |
|---|---|---|---|---|
| 1 | **Tara Webhook URL** | Endpoint where Tara / CAT Sentinel posts ticket lifecycle events. | Endpoint provided by CAT team (e.g. `https://omnitest-staging.../v3/tara/webhooks/tickets`). | Pending CAT Team |
| 2 | **Incoming Event Schema** | JSON event contract for `ticket.created`, `ticket.updated`. | Must map to `tenant_id`, `ticket_id`, `event_id`, `summary`, `target_origin`, `priority`, etc. | Contract Defined; Pending CAT Schema Validation |
| 3 | **HMAC Algorithm & Secret Delivery** | Shared secret distribution via secure secrets channel (e.g., Vault reference, Azure KeyVault, AWS Secrets Manager). Never share secrets via email/chat. | `HMAC-SHA256` computed as `hex(HMAC_SHA256(secret, f"{timestamp}.{raw_body}"))`. | Algorithm Implemented; Secret Delivery Pending |
| 4 | **Timestamp & Replay Window** | Unix epoch timestamp header `X-OmniTest-Timestamp` (seconds). | Configurable tolerance window (default 300 seconds / 5 minutes). Drift > tolerance rejected with HTTP 401 `stale signature`. | Implemented & Verified |
| 5 | **Authentication Method** | Header-based HMAC signature: `X-OmniTest-Signature: <hex_sha256>`. | Authenticates caller without transmitting static secrets across the wire. | Implemented & Verified |
| 6 | **Callback URL for Verification Results** | CAT Sentinel endpoint to receive test execution outcomes, reproduction proof, and sanitized report links. | `POST /api/v1/support-tickets/{ticket_id}/responses` or designated callback endpoint. | Pending CAT Team Endpoint Confirmation |
| 7 | **Organization & Environment Identifiers** | Mapping of CAT tenant identifiers (e.g. `tenant_id`, `organization_id`) to OmniTest project/tenant scopes (`staging`, `qa`, `production`). | Ensures strict multi-tenant isolation and prevents cross-tenant data leakage. | Pending Tenant Mapping Agreement |
| 8 | **Approved Target Applications & Test Accounts** | Allowed target origins (e.g., `https://staging.app.pionedata.com`) and test credentials provisioned safely through OmniTest Secret Vault. | Live scans reject non-allowlisted origins via strict SSRF guards. Zero credentials stored in plain text. | Pending Staging Target Allowlist |
| 9 | **Scenario-Selection Rules** | Business logic mapping ticket `affected_feature` and `reporter_role` to approved V3 assurance scenarios (API contract, bounded workflow, or data assertion). | Unmapped features route to default triage without running arbitrary untrusted code. | Implemented & Verified |
| 10 | **Result-Status Mapping** | Formal mapping between OmniTest terminal verdicts and CAT Sentinel ticket statuses: | | Defined: |
| | • `reproduced` | Confirmed bug with captured HTTP/UI trace | Status -> `Investigating` / `Defect Confirmed` |
| | • `not_reproduced` | Contract satisfied / nominal execution | Status -> `Resolved` / `Requires Customer Info` |
| | • `blocked` | Target unreachable, timeout, or auth failure | Status -> `Blocked` (NOT reported as defect) |
| | • `inconclusive` | Missing UI capture or ambiguous data | Status -> `Manual Review Required` |
| 11 | **Retry & Idempotency Expectations** | Delivery retry policy with stable `event_id` and `ticket_id`. | OmniTest enforces deduplication: exact duplicate events return HTTP 200 without creating redundant verification jobs. | Implemented & Verified |

---

## 2. Webhook Event Contract Specification (OmniTest Ingest)

### Headers
```http
POST /v3/tara/webhooks/tickets HTTP/1.1
Host: omnitest.yourdomain.com
Content-Type: application/json
X-OmniTest-Timestamp: 1731411300
X-OmniTest-Signature: 5a8e1b...6f3d (HMAC-SHA256 hex digest)
Content-Length: 412
```

### Event Payload Schema (JSON)
```json
{
  "tenant_id": "pione-dev",
  "ticket_id": "T-10492",
  "event_id": "evt-771829",
  "event_type": "ticket.created",
  "summary": "Customer cannot export audit report PDF in staging",
  "affected_feature": "AuditExport",
  "expected_behavior": "Audit PDF downloads within 5 seconds",
  "actual_behavior": "Server returns HTTP 500 error",
  "target_origin": "https://staging.pionedata.com",
  "authorized_role": "analyst",
  "priority": "high",
  "retry_limit": 1,
  "assessment_mode": "both",
  "scenario_version": "1.0",
  "reporter_role": "user"
}
```

### Ingest Responses
* `201 Created`:
  ```json
  {
    "status": "accepted",
    "correlation_id": "tara_9f2a8c1e4d...",
    "created": true,
    "incident_id": "inc_771829",
    "job_id": "job_382104"
  }
  ```
* `200 OK` (Duplicate event):
  ```json
  {
    "status": "duplicate",
    "correlation_id": "tara_9f2a8c1e4d...",
    "created": false,
    "incident_id": "inc_771829",
    "job_id": "job_382104"
  }
  ```
* `401 Unauthorized`: Invalid HMAC signature or stale timestamp (`stale signature`).
* `413 Payload Too Large`: Payload exceeds 262,144 bytes (256 KB).
* `422 Unprocessable Entity`: Malformed JSON or missing required fields.
* `404 Not Found` / `503 Service Unavailable`: Feature gate disabled.

---

## 3. End-to-End Live Verification Sequence (Staging Pilot Gate)

Before certifying live integration in production, the following sequence must be demonstrated in staging:

```
[Tara Chatbot] User raises complaint
      │
      ▼
[CAT Sentinel] Creates Ticket & emits signed webhook
      │
      ▼
[OmniTest /v3/tara/webhooks/tickets]
      ├─ Verifies HMAC & Timestamp
      ├─ Normalizes & Redacts payload
      ├─ Enforces Deduplication (1 ticket = 1 job)
      └─ Creates SupportIncident & VerificationJob in PostgreSQL
      │
      ▼
[OmniTest Worker] Executes bounded scenario (API / UI / Data Assertion)
      │
      ▼
[Evidence Storage] Saves sanitized audit logs & execution artifacts
      │
      ▼
[CAT Sentinel Callback] Posts verdict & evidence link to ticket response API
      │
      ▼
[Super Admin Desk] Reviews reproduction evidence; communicates with user
```
