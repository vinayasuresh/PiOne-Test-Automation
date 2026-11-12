# CAT Sentinel–OmniTest Integration: Questionnaire and Prerequisites

**Purpose:** Gather the information needed to connect Tara and the CAT Sentinel support-ticket system to OmniTest for an initial staging pilot.

**Requested from:** CAT Sentinel backend/API, Tara/chatbot, support/helpdesk and DevOps/security teams. Please enter `Unknown` or `Not available` where applicable. Share links to current documentation when possible.

**Important:** Do not enter passwords, API keys, bearer tokens, private keys or customer data in this document. Share credentials through an approved secrets channel.

**Completed by:** ____________________  **Team:** ____________________  **Date:** __________

## 1. Pilot and contacts

1. Technical owner and contact: ____________________
2. Tara/chatbot owner and contact: ____________________
3. Helpdesk/Super Admin owner and contact: ____________________
4. DevOps/security owner and contact: ____________________
5. Approved staging application URL and environment: ____________________
6. Pilot scenario and expected behaviour: ____________________
7. Should verification start automatically on ticket creation, or when an admin selects **Verify with OmniTest**? ____________________
8. Who should receive the result and who can approve ticket closure? ____________________

## 2. API and ticket ownership — CAT backend team

We observed two documented API families. Please confirm the environments and whether these are tenant-side and admin-side services or have another relationship:

- `/api/v1/support-ticket`: ____________________
- `/api/v1/support-tickets`: ____________________

1. Which API is the source of truth for a ticket raised through Tara? ____________________
2. Does Super Admin see the same ticket record? If records are synchronized, explain the mapping and timing: ____________________
3. How are tenant ID, user ID, ticket ID and admin-side ID represented and mapped? ____________________
4. Which endpoints should OmniTest use to list and read tickets, read conversation context, add a result, attach/link evidence and update status? Please provide methods, paths and current OpenAPI link: ____________________
5. Are there private/internal notes visible only to support staff? Which endpoint supports them? ____________________
6. Are `sync-status` and `sync-response` supported integration interfaces or internal-only? ____________________
7. What are the valid status transitions, ownership rules and closure permissions? ____________________
8. Are pagination, filtering by tenant/update time, rate limits and ordering documented? ____________________
9. We observed a “Support ticket not found” message in an earlier test. Is this known, and how should it be reproduced or tracked in staging? ____________________

## 3. Ticket events — CAT backend/Tara team

1. Are ticket-created and ticket-updated webhooks available? **Yes / No / Unknown**
2. If yes, provide event documentation, example sanitized payload, delivery retries, timeout and event ID: ____________________
3. How are events authenticated and protected against replay (signature, timestamp, key rotation)? ____________________
4. If webhooks are unavailable, can an approved service account poll the ticket API? Specify filters, cursor or updated timestamp, limits and recommended frequency: ____________________
5. Can the same event be delivered more than once, and what stable ID identifies it? ____________________

## 4. Authentication, access and deployment — DevOps/security

1. Staging HTTPS API base URL and current OpenAPI URL: ____________________
2. Authentication type and token expiry/refresh method: ____________________
3. Can a dedicated service identity be created with tenant-scoped, least-privilege permissions? **Yes / No / Unknown**
4. Which roles/scopes are needed to read tickets and add an internal result? ____________________
5. Approved secret store and method for providing credential references: ____________________
6. Network requirements: private connectivity, firewall allowlists, certificates or mTLS: ____________________
7. Data retention, redaction and evidence access requirements: ____________________
8. Is the HTTP specification URL provided for documentation only? What secure HTTPS endpoint should integrations use? ____________________

## 5. Verification and evidence — product/support teams

1. Approved target application, staging environment and test role: ____________________
2. Available safe test account and test-data provisioning process (no credentials here): ____________________
3. Which approved scenario should be run for the pilot, and how should its expected result be checked? ____________________
4. Are state-changing actions allowed in staging? List explicit limits and approval owner: ____________________
5. Where should screenshots, logs and reports be stored, and who may view them? ____________________
6. How should **reproduced**, **not reproduced**, **blocked** and **inconclusive** outcomes appear in the ticket? ____________________
7. Should OmniTest retry automatically? Specify limits and how a retest after a fix is requested: ____________________

## 6. Pilot acceptance checklist — joint review

- [ ] A test user raises a ticket through Tara and receives its ticket reference.
- [ ] The correct Super Admin sees the ticket in the approved tenant.
- [ ] OmniTest receives or discovers the same ticket using an approved integration.
- [ ] Duplicate delivery does not create duplicate verification jobs.
- [ ] One approved scenario executes with the agreed test role and environment.
- [ ] The result and protected evidence link appear on the correct ticket.
- [ ] A blocked test leaves the ticket available for support; it is not reported as an application defect.
- [ ] The Super Admin can communicate with the user and close the ticket under the agreed rules.
- [ ] A retest retains the original evidence and updates the same incident history.
- [ ] Service outages, access denial and retry/restart recovery are tested.

## 7. Open issues and next steps

| Question / dependency | Owner | Target date | Status / response |
| --- | --- | --- | --- |
|  |  |  |  |
|  |  |  |  |

**Links to supporting API documentation or sample sanitized payloads:** ____________________
