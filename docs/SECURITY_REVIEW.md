# PiOne Testmate security review

## Implemented scope

After a crawl, open **Analyze → VAPT Assessment**. Choose **Security review only**
for the bounded review, or **VAPT assessment only** for the expanded safe VAPT
profile. Confirm permission for the application, select checks, then start. This
is an authorized, bounded HTTP assessment; it does not claim ZAP parity or
complete OWASP Top 10 coverage.

Default checks inspect freshly retrieved pages for missing response headers,
cookie attribute omissions, mixed-content references, directory index signals,
source map references and potential credential formats. Findings include evidence,
confidence, severity, CWE/OWASP Top 10 **2021** mapping and remediation.
Missing headers are hardening observations; source-map references and reflection
are not proof of exploitable vulnerabilities. Credential values are never included
in findings. CORS findings require follow-up to establish sensitive-data impact.

The VAPT profile also inventories crawler-captured fetch/XHR URLs, records HTTP
status/content-type metadata, checks safe error disclosures, and records an
authorization-boundary observation when an anonymous request succeeds. These are
observations requiring validation, not automatic exploit claims. The optional
active checkbox sends a reserved CORS Origin and an alphanumeric reflection
marker. It does not execute scripts or send injection payloads.

Routes come from the selected run, then same-origin links and script/style
references can expand the queue. Query values are omitted, forms are not submitted,
and redirects are not followed. Protected routes may therefore be inaccessible.
The scanner does not execute JavaScript, enumerate every endpoint, reuse browser
authentication, or discover hidden APIs, robots/sitemap/OpenAPI references specially.

## Controls and persistence

- UI budget: 20 requests / 120 seconds; API caps: 50 requests / 180 seconds.
- Each request consumes budget, including active probes; no automatic retries.
- One job per server process, at least one second between requests. Run one server
  worker. Multiple workers/processes are not supported for security job coordination.
- Pause/resume/stop apply between requests. Paused time counts toward the budget.
- TLS verification is enabled. DNS results are validated, and the connection is
  pinned to an approved IP while preserving hostname validation/SNI. Non-public
  and metadata destinations are rejected independently of ALLOW_LOCAL_TARGETS.
- Response inspection is capped at 256 KiB. Socket/read activity has a watchdog;
  operating-system DNS resolution remains subject to OS resolver timeout.
- Checkpoints/findings live in `RunContext.security_report` under
  `automation_framework/reports/run_contexts/<run_id>.json`. SQLite/PostgreSQL
  still index the existing runs; this feature requires no DB migration.
- Refresh reloads saved results. Server restart marks an active review interrupted;
  it preserves partial findings but requires starting a new review. Start replaces
  the previous security report for that run. Export first to keep history.
- JSON, Markdown and Excel downloads are available in the Analyze panel. Excel includes
  Coverage/VAPT Summary, Master Findings, Endpoint Inventory and Coverage Gaps sheets
  with the consolidated finding fields used by the supplied report. They are scoped
  to the requested run; no raw response bodies or storage-state downloads exist.
- Delete All Artifacts clears security reports too, and is rejected while a security
  job is active. Security report download is not added to the general artifact index.

The current application is a local single-user tool. Run IDs provide association,
not user authorization. Do not expose this API publicly; multi-user access control,
CSRF policy and distributed job coordination require separate deployment hardening.

## Local fixture verification

`SECURITY_LAB_ORIGIN` can specify one exact HTTP(S) origin for a loopback-only
test fixture, e.g. `http://127.0.0.1:9876`. It never allows private or metadata IPs.
Leave it unset for normal use. No active checks were authorized against third-party
public demo applications by this implementation task.

Run focused tests with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest automation_framework/tests/test_security_review.py -q
```

## Remaining capabilities

Authenticated browser-session reuse, an actual ZAP adapter, authenticated role
comparison, injection verification, JWT/session assessment, SARIF exports,
report history/diffing, crash-resumable jobs and complete security-stage
integration are not implemented. No automatic scanner can establish complete
coverage of access-control design or business logic; these gaps are reported in
every VAPT export rather than presented as passed checks.

## Reference and licensing

Reference: https://github.com/eldarshiraliyev/BugScanner (MIT; local LICENSE reviewed).
Reviewed its orchestrator and finding data model to inform modular checks, confidence,
evidence and remediation fields. This implementation is original; no BugScanner
source, payload sets, dependencies or scanner executable are bundled or invoked.
Upstream performance and false-positive claims have not been independently verified.
If upstream code is incorporated later, include its MIT copyright/license notice.

BugScanner's destructive method tests, credential guessing, cloud metadata probes,
WAF evasion, business-data changes and unrestricted external tool templates are not
enabled by this feature. Broader active assessment belongs in an explicitly scoped,
authorized test environment with independently reviewed rules.
