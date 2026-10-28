# PiOne Testmate — Senior Demo Brief

**Purpose:** A simple, honest script for the next senior review
**Recommended target:** SauceDemo in Live Target mode, with Mock Demo available as a deterministic fallback
**Recommended duration:** 10–12 minutes

## 1. Opening statement (say this first)

> PiOne Testmate is an evidence-first UI assurance platform. It scans the real
> application with Playwright, builds a grounded map of routes and UI elements,
> generates safe functional tests from that evidence, executes them with proof,
> and keeps the result available for regression. Security review is a separate,
> explicitly authorized layer. AI helps plan; it does not control the browser or
> invent evidence.

Then say the important limitation:

> This is not claiming to replace ZAP or Burp. Our advantage is connecting the
> application map, functional tests, security observations and regression proof
> in one traceable run. Where deep active security testing is required, V3 will
> integrate specialist engines rather than pretending a passive check is an exploit.

That sentence builds more trust than a heroic demo that accidentally discovers a
button and calls it artificial intelligence.

## 2. What to prepare before the meeting

- Start the backend on `127.0.0.1:8000` and verify `/system/db-status`.
- Open the dashboard in a fresh browser tab.
- Keep SauceDemo credentials ready: `standard_user` and its demo password.
- Keep Mock Demo as the offline fallback if the public target, tunnel or network is
  unreliable.
- Clear old runs only if they make the navigation confusing; do not delete database
  data during the demo.
- Confirm the browser can open generated screenshots and downloaded Excel files.
- Do not run active security probes against a public demo site. Show the bounded
  security panel and explain the scope, or use an authorized local fixture.
- Keep one prepared screenshot of a completed report in case the live network gets
  stage fright. Computers enjoy drama when executives are watching.

## 3. Demo flow

### Minute 0–1: Explain the problem

Many UI automation tools either require manual scripting or let an AI guess. Guessing
creates flaky selectors, untraceable failures and reports that say “failed” without
showing why.

Testmate starts from observed application evidence and keeps the evidence attached to
the decision.

### Minute 1–3: Scan a real application

1. Select **Live Target**.
2. Enter `https://www.saucedemo.com`.
3. Enter the demo credentials.
4. Choose the assessment mode deliberately:
   - **Functional testing only** for this main demo.
   - Explain that security review is not silently run in this mode.
5. Discover modules.
6. Select the product/menu module and continue.

Say:

> The crawler is not clicking every button blindly. It classifies actions, keeps
> destructive or uncertain actions in a parking lot, and records the route and
> element evidence used later by the test planner.

### Minute 3–5: Show crawl proof

Show:

- Module and route counts.
- Live telemetry events.
- Elements found and safe actions.
- Parking-lot actions.
- The persisted run ID and checkpoint.

Say:

> This is the important difference from a text-only AI demo: the plan can be
> audited back to a real route and element. If the crawler did not observe it, the
> planner should not present it as grounded coverage.

### Minute 5–7: Generate a small functional plan

Use a scenario such as:

> Verify that a standard user can browse inventory, open a product, add one item to
> the cart, confirm the cart count, open the cart and return to inventory without
> performing checkout or destructive actions.

Set a small testcase count, such as 3. Demonstrate Gemini or Ollama only if the
connection is healthy. If the provider fails, explain that the deterministic fallback
keeps the workflow usable and the provider failure is recorded.

Point out:

- Test cases are generated from the captured UI evidence.
- AI suggestions are reviewed before execution.
- The test count is an explicit user control, not an AI guess.

### Minute 7–9: Execute and show proof

Execute the selected cases. Open one result and show:

- Pass/fail/skipped status.
- Exact locator used.
- Screenshot or trace evidence.
- Console/network failure information where present.
- Excel report with execution results.

Say:

> The output is not just a green checkmark. A reviewer can inspect what ran, where
> it ran, what locator was used, and which artifact proves the result.

### Minute 9–10: Show baseline/regression

Save the completed plan as a baseline. Change one safe expectation or rerun after a
small target change. Show the diff.

Say:

> Regression is a comparison against an approved evidence-backed baseline, not just
> running the same script again and hoping the dashboard remembers it.

### Optional minute 10–12: Explain security mode

Do not present the current bounded VAPT panel as a full penetration test.

Say:

> Security review is a separate authorized mode. The current implementation checks
> evidence such as headers, cookies, mixed content, exposure signals, API inventory,
> authentication boundaries and error disclosure. It explicitly reports what it did
> not test. V3 will integrate ZAP/Burp for deeper authorized DAST and import their
> findings into the same Assurance Graph.

## 4. Questions seniors may ask

### “What is unique?”

The unique value is not the LLM. It is the evidence chain: one observed application
map supports test generation, execution, security observations, screenshots, exports
and regression decisions.

### “Why not just use Testsigma or mabl?”

They are stronger for broad SaaS test lifecycle, cloud scale and self-healing. Testmate
is differentiated by a controlled, explainable scan-to-proof workflow that can combine
our own grounding with external security engines and local execution.

### “Does it find SQL injection or XSS?”

Not reliably today. The current VAPT layer is deliberately bounded and does not claim
active exploit coverage. SQLi, XSS, SSRF, IDOR, business logic and role-differential
testing are V3 integration/engineering work.

### “Is the AI trusted?”

The browser is not driven directly by the model. Playwright and safety rules control
discovery and execution. The model proposes a plan from saved evidence; unsupported
steps must be rejected or marked ungrounded.

### “What happens when Gemini fails?”

The provider gateway records the failure and falls back to deterministic generation,
so a temporary model outage does not stop the whole QA workflow.

### “Can we use it with Excel?”

Yes. The current flow preserves uploaded testcases and exports execution results. V3
will make row/step lineage a formal acceptance gate so every input row is accounted for.

### “Is it production-ready?”

The current version is a strong local/product foundation, not yet a finished multi-team
SaaS platform. The V3 first gate is reliability, recovery, governance and evidence
integrity before large-scale cloud execution.

## 5. Closing statement

> Our goal is not to make another black-box AI tester. Our goal is to make testing
> explainable and reusable: scan once, ground the plan, execute safely, preserve
> proof, connect security evidence, and know exactly what changed next time.

## 6. Demo red lines

- Never claim complete OWASP Top 10 coverage.
- Never call a reflection marker confirmed XSS.
- Never run intrusive scans against public demo applications.
- Never hide a provider fallback or skipped step.
- Never delete old evidence to make the dashboard look clean.
- Never say “100% accurate.” Say “evidence-backed and explicit about coverage.”
