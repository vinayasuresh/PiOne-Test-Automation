# PiOne Testmate V2 — Current Verification Status

**Status date:** 2026-10-29
**Scope:** V2 only. This document does not approve or describe V3 work.

## Release position

**Conditional V2 baseline; not yet an unconditional production release.**

The implementation has meaningful functional evidence. The one remaining
release-evidence gap is a rendered, live-target, multi-module checkpoint
interruption followed by Retry/Resume, the next checkpoint, and Report
completion in the same journey.

## Evidence completed

| Area | Evidence | Result |
|---|---|---|
| Syntax and diff hygiene | `python -m compileall` on changed fixture/audit files; `git diff --check` | Passed |
| Crawler, planning, persistence, execution, lifecycle, and boundary contracts | Focused pytest groups | Passed in the executed focused groups |
| Manual learning capture | Browser-marked crawler E2E tests for page URL, CSS selector, XPath, source snippet, Continue, and End-and-Save | `4 passed, 10 deselected` |
| Fixture module isolation | Dedicated `/analytics` fixture route added so module-review tests do not fall through to root | Implemented and compiled |
| Requested test count | Rendered Mock Demo audit requested `1`, `3`, `10`, and `30` grounded cases | 30 request produced 30 cases |
| Controlled execution | Rendered Mock Demo audit | 82 passing step rows; no failure rows reported |
| Reporting lifecycle | Rendered Mock Demo audit | JSON, YAML, Excel, Markdown download actions; baseline save; Saved Runs exercised |

## What is deliberately not claimed

- No active XSS, SQL injection, brute-force, or destructive DAST validation.
- No complete OWASP Top 10 coverage.
- No unconditional production certification.
- No proof that every third-party live target will complete within the same
  timeout; target behaviour, authentication expiry, and network conditions are
  external variables.

## Required final V2 release gate

1. Start the current source on an approved host with normal local socket and
   Playwright permissions.
2. Use an authorized live target with at least two selected modules and
   interactive learning enabled.
3. Let module one reach a checkpoint and continue it.
4. At a later checkpoint, create an intentional safe interruption.
5. Verify the UI labels the state as partial/stopped, shows the saved scope,
   confirms Retry separately from Resume, and does not show 100% prematurely.
6. Resume or retry as chosen, reach the next checkpoint, then complete the run.
7. Continue through Analyze, generate the requested number of cases, execute,
   export the report, reopen Saved Runs, and verify the restored evidence.
8. Retain the terminal log, exported artifact names, and rendered screenshots
   with the release record.

## Operator note

`Resume` continues saved work. `Retry` intentionally retests the current or
failed scope after confirmation. They must not be presented as equivalent.
