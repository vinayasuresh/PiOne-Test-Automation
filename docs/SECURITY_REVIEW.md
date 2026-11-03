# PiOne Testmate Security Architecture & Assurance Boundary Guide

**Document Version:** `3.0.0` (Client Assurance & V2/V3 Security Baseline)  
**Applicability:** PiOne Testmate Core Engine, V2 Bounded HTTP Review / VAPT, and V3 Client Assurance Vertical Slice  
**Classification:** Technical & Security Architecture Standard  

---

## 1. Guiding Principles & Threat Model

PiOne Testmate is an **evidence-first quality assurance and client-assurance platform**. The architecture enforces strict boundaries between deterministic scanning, safe test execution, bounded security observations, and client incident triage.

```text
Target / Incident → Bounded Scope & Policy Guard → Safe Execution Worker → Assurance Graph
                                                                          ↓
                                            Truthful Verdict & Redacted Evidence Manifest
```

### Core Tenets
1. **Deterministic Discovery First, AI Intelligence Second:** Playwright, boundary guards, and schema validators own all browser and network interactions. AI models analyze saved evidence to propose test plans or summarize observations, but never autonomously drive browsers, alter payloads, or execute arbitrary commands.
2. **Evidence-Grounded Findings:** Every observation is tied to a specific `run_id`, URL route, DOM element, HTTP request/response metadata, and cryptographic evidence checksum.
3. **Truthful Outcome Reporting:** Infrastructure failures, authentication timeouts, or missing fixtures are reported as `blocked` or `inconclusive`—never converted into green passes or reported as customer-product exploits.
4. **Zero Trust for Network Egress & Mutation:** Unchecked egress to private networks, cloud metadata services, and destructive mutation methods are blocked by design.

---

## 2. Non-Negotiable Safety Rules & Mode Isolation

### 2.1 Mode Isolation
The platform strictly isolates testing profiles to eliminate accidental security probes during functional test runs and unintended test mutations during security audits:
- **Functional-Only Mode:** Active security checks, CORS probes, reflection markers, and VAPT scanners are completely disabled and hidden from the UI.
- **Security-Only Mode:** Functional test generation, step execution, and UI state mutations are disabled; the worker operates purely in passive observation mode.
- **Combined Mode:** Clearly segregates functional artifacts from security observations, executing security checks with explicit user consent and rate budgets.

### 2.2 SSRF Protection & Target Boundary Guards (`security_guard.py`, `url_filter.py`)
All target URLs, API endpoints, and crawler navigation targets pass through strict SSRF and boundary validation:
- **Blocked Destinations:**
  - IPv4/IPv6 loopbacks: `127.0.0.1`, `localhost`, `::1`, `0.0.0.0`.
  - RFC 1918 private subnets: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.
  - Link-local and cloud metadata addresses: `169.254.169.254`, `fe80::/10`.
  - Non-HTTP protocols: `file://`, `ftp://`, `gopher://`, `data:`.
- **Local Fixture Testing:** Loopback targets are strictly blocked unless `ALLOW_LOCAL_TARGETS=1` or `SECURITY_LAB_ORIGIN` is explicitly set in the local development environment for controlled test fixtures.
- **Same-Origin & Path Namespace Enforcement:** Navigation guards intercept in-page links and redirects. Any attempt to navigate outside the authorized target origin or allowed path prefix is aborted.

### 2.3 Destructive Action Parking Lot (`action_classifier.py`)
During crawling and test execution, interactive DOM controls are classified prior to interaction:
- Any element matching mutation, deletion, submission, administrative reset, file upload, or payment patterns (e.g., `delete`, `remove`, `drop`, `submit`, `pay`, `purchase`, `logout`) is diverted to the **Parking Lot**.
- Diverted actions are never clicked automatically; they are recorded in the `parking_lot_actions` audit sheet for explicit human review.

### 2.4 Credential & Secret Redaction
- **Storage Hygiene:** Raw passwords, session tokens, bearer tokens, API keys, and cookies are never stored in browser `localStorage` or `sessionStorage`.
- **Prompt & Export Sanitization:** All LLM prompts, SSE event streams, JSON/YAML artifacts, Excel workbooks, and Markdown reports pass through regex and key-based sanitizers that redact sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`) and confidential values.

---

## 3. V2 Bounded HTTP Security Review & VAPT Profile

The V2 **Analyze → VAPT Assessment** module provides authorized, bounded HTTP evaluations for web applications.

### 3.1 Implemented Scope
- **Passive Hardening Checks:**
  - Missing HTTP security response headers (`Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`).
  - Insecure cookie attribute omissions (`Secure`, `HttpOnly`, `SameSite`).
  - Mixed-content resource references (HTTP resources embedded in HTTPS pages).
  - Directory indexing disclosure indicators.
  - Source map references and debug artifact exposure.
- **Safe Bounded VAPT Probes:**
  - Crawler-captured fetch/XHR endpoint inventory with status code and content-type metadata.
  - Safe error disclosure analysis (e.g., verbose stack trace detection).
  - Authorization boundary check: records an observation when an endpoint succeeds anonymously without expected credentials.
  - Optional low-impact reflection probe: sends a reserved CORS `Origin` header and a non-executable alphanumeric reflection token. It never sends executable script tags or SQL injection syntax.

### 3.2 Operational Guardrails & Budgets
- **Request Caps:** UI budget is hard-capped at 20 requests / 120 seconds; API caps at 50 requests / 180 seconds.
- **Pacing:** Minimum 1-second interval between requests; single-worker process coordination.
- **Response Bounds:** Response body inspection is capped at 256 KiB; socket read watchdogs prevent connection stalls.
- **Storage:** Checkpoints and observations are saved under `RunContext.security_report` in `automation_framework/reports/run_contexts/<run_id>.json`.

---

## 4. V3 Client Assurance: API Contract Validation Security

In accordance with the **Client Assurance Delivery Priority**, API contract testing is mandatory before autonomous orchestration.

### 4.1 Safe Contract Validation Principles
- **Read-Only Operation Enforcement:**
  - Ingestion of OpenAPI v3 and Postman Collection v2.1 contracts normalizes read-only methods (`GET`, `HEAD`, `OPTIONS`) as executable.
  - All write and mutation operations (`POST`, `PUT`, `PATCH`, `DELETE`) are strictly registered as disabled records (`executable: false`).
  - Mutation endpoints cannot be executed by the automated runner, eliminating the risk of data corruption in staging environments.
- **Safe Contract Checks:**
  - Status code verification (matching expected status, e.g. 200 OK).
  - Strict JSON schema structural validation and required field verification.
  - Latency SLA threshold verification (e.g., response time under 1500ms).
  - Authorization boundary tests: anonymous requests to protected endpoints verify that a 401 Unauthorized or 403 Forbidden is returned.
- **Target Verification:**
  - API contract checks only execute against authorized target origins registered to the active `run_id`.
  - Full request metadata, authorization headers, and response payloads are redacted before being linked to the `api_observation` record.

---

## 5. V3 Client Assurance: Dashboard-Data Validation & Integrity Separation

Dashboard-data validation automates the verification of business metrics presented in charts, tables, and KPI cards against underlying data sources.

### 5.1 Clear Separation: Data Integrity vs Security Vulnerability
- A numerical mismatch, rounding discrepancy, or stale cache figure is classified strictly as a **Data Integrity Finding** (`data_integrity_mismatch`), NOT a security vulnerability or exploit.
- Defect classifications include:
  1. `stale_data`: Data timestamp exceeds declared freshness tolerance.
  2. `missing_data`: Required metric or row absent from source payload.
  3. `filter_mismatch`: Filter context (date range, tenant, status) between UI and source does not align.
  4. `rounding_mismatch`: Discrepancy is within acceptable rounding tolerance (e.g., 0.1%).
  5. `visual_source_mismatch`: Rendered DOM value diverges from source value beyond tolerance.

### 5.2 Read-Only Data Source Adapters
- Data source adapters support read-only query endpoints or pre-recorded API response payloads.
- Arbitrary SQL execution, write operations, DDL/DML statements, and raw database shell commands are strictly prohibited.
- Scenario definitions are immutable and registered in the test catalog before execution.

---

## 6. V3 Orchestration & Incident Triage Guardrails (Tara / Helpdesk)

When ingesting customer complaints from Tara or monitoring webhooks:
- **Idempotency:** Replaying a Tara `correlation_id` returns the existing `support_incident` and `verification_job` without launching duplicate workers.
- **Bounded Verification Attempts:** Verification jobs enforce strict execution caps (`max_attempts: 1..3`) and bounded timeouts.
- **Truthful Terminal Verdicts:** The verification worker can only conclude:
  - `reproduced`: The reported symptom was observed under the exact registered scenario conditions.
  - `not_reproduced`: The registered scenario passed without reproducing the issue.
  - `blocked`: An external prerequisite (auth expiry, target down, network block) prevented test completion.
  - `inconclusive`: Ambiguous DOM state or missing data prevented verification.
- **Deterministic Ownership:** Incidents are routed to designated engineering queues (e.g., Reporting Engineering, Platform QA) based on explicit rules, preventing unmonitored agent actions.

---

## 7. Explicit Boundaries & Future Security Integrations

### What PiOne Testmate IS:
- A deterministic, evidence-grounded web application crawler and functional test executor.
- An automated API contract conformance validator for read-only operations.
- A dashboard data-integrity verification engine.
- A bounded, safe passive HTTP security and header reviewer.
- An incident triage verifier that produces auditable evidence packs.

### What PiOne Testmate IS NOT (Explicit Out-of-Scope Items):
- **Not an Active Exploitation Tool:** The platform does NOT execute active SQL injection (`' OR 1=1--`), Cross-Site Scripting (XSS payloads `<script>`), remote code execution, or directory traversal attacks.
- **Not a Replacement for Specialist DAST Tools:** PiOne Testmate does NOT replace OWASP ZAP or PortSwigger Burp Suite. For deep dynamic application security testing (DAST), the platform's external adapter interface must be used to ingest and normalize ZAP/Burp findings into the Assurance Graph.
- **Not an Autonomous Penetration Tester:** The platform will never attempt credential stuffing, password brute-forcing, privilege escalation bypasses, or cloud metadata exfiltration.
- **No Claim of Complete OWASP Top 10 Coverage:** Passive header checks and status code assertions must never be marketed or represented as full OWASP Top 10 compliance.
