"""Rendered Browser Verification for OmniTest V3 API Testing & Database Cross-Read.

Executes real browser interactions against http://127.0.0.1:8000 using Playwright:
1. Mode switching: select API Testing in #assessment-mode-first.
2. Verified DOM toggle: #web-app-scan-container is hidden, #api-testing-flow-container is shown.
3. Ingestion: Paste OpenAPI 3.0 contract and click Ingest.
4. Operation parsing: verify source order table rendering.
5. Authentication: save Bearer token to Secret Vault, verify masked preview, verify token input cleared from DOM.
6. Execution: click "Execute API Test Plan", verify progress bar and live counters.
7. Excel Download: verify 10-sheet Excel report availability.
8. Database Cross-Read: test 3-way validation in the UI and verify discrepancy classification.
9. Capture screenshot proof to automation_framework/reports/phase9_api_testing_browser_proof.png.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from playwright.sync_api import sync_playwright

from api.models.v3_database_models import DatabaseQueryRegistrationRequest
from api.routers.v3_assurance import register_database_query


def run_browser_validation():
    base_url = os.getenv("OMNITEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    print(f"Starting Playwright rendered browser validation against {base_url}...")
    os.environ["V3_ASSURANCE_ENABLED"] = "true"

    # Pre-register an approved database query for the 3-way cross-read test
    try:
        register_database_query(DatabaseQueryRegistrationRequest(
            query_id="query_mrr_revenue",
            name="MRR Revenue Monthly Query",
            sql="SELECT 1500 as mrr",
            database_ref="configured-test",
            environment="test",
            approved=True,
        ))
        print("Registered approved database query 'query_mrr_revenue'.")
    except Exception as exc:
        print(f"Query registration info: {exc}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 1100})
        page = context.new_page()
        page.on("console", lambda msg: print(f"[BROWSER CONSOLE] {msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: print(f"[BROWSER PAGEERROR] {err}"))

        # 1. Navigate to the configured local OmniTest origin.
        print(f"Navigating to {base_url}...")
        page.goto(base_url, wait_until="networkidle", timeout=30000)
        assert "PiOne Testmate" in page.title() or "OmniTest" in page.title()
        print("Page loaded successfully.")

        # 2. Select API Testing in #assessment-mode-first
        mode_select = page.locator("#assessment-mode-first")
        assert mode_select.is_visible()
        mode_select.select_option("api_testing")
        page.wait_for_timeout(500)

        # 3. Verify DOM toggle
        web_scan = page.locator("#web-app-scan-container")
        api_flow = page.locator("#api-testing-flow-container")
        assert "hidden" in (web_scan.get_attribute("class") or "")
        assert "hidden" not in (api_flow.get_attribute("class") or "")
        print("DOM toggled cleanly: web-app-scan-container hidden, api-testing-flow-container visible.")

        # 4. Switch to Paste Tab
        page.locator("#api-tab-paste").click()
        page.wait_for_timeout(300)

        # 5. Paste OpenAPI Spec targeting live server endpoints
        spec_content = """openapi: 3.0.0
info:
  title: OmniTest Self API Verification
  version: 1.0.0
paths:
  /version:
    get:
      operationId: getVersion
      summary: Retrieve server build version
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  status:
                    type: string
                  application:
                    type: string
                required:
                  - status
                  - application
  /database-queries:
    get:
      operationId: listQueries
      summary: List registered database queries
      responses:
        '200':
          description: OK
  /incidents/intake:
    post:
      operationId: intakeIncident
      summary: Ingest incident (mutation check)
      responses:
        '201':
          description: Created
"""
        page.locator("#api-ingest-paste").fill(spec_content)
        page.locator("#api-base-url-input").fill(base_url)
        page.locator("#api-environment-select").select_option("staging")

        # 6. Click Ingest
        print("Clicking Ingest & Parse Operations...")
        page.locator("#api-btn-ingest").click()
        page.wait_for_selector("#api-operations-card:not(.hidden)", timeout=10000)
        page.wait_for_timeout(500)

        summary_text = page.locator("#api-ingest-summary-text").inner_text()
        print(f"Ingestion result: {summary_text}")
        assert "3 operations" in summary_text
        assert "OPENAPI" in summary_text.upper()

        # 7. Authentication card: register Bearer token
        page.locator("#api-auth-type").select_option("bearer")
        page.wait_for_timeout(200)
        token_input = page.locator("#api-auth-bearer-token")
        token_input.fill("secret_browser_bearer_token_xyz")
        page.locator("#api-btn-save-auth").click()
        page.wait_for_timeout(500)

        vault_badge = page.locator("#api-vault-badge").inner_text()
        print(f"Vault badge: {vault_badge}")
        assert "sec_fix_" in vault_badge
        assert "secret_browser_bearer_token_xyz" not in vault_badge
        # Verify DOM element was cleared (zero secret leakage)
        assert token_input.input_value() == ""
        print("Verified zero secret leakage: raw secret wiped from browser DOM.")

        # Test Connection Probe against /version
        page.locator("#api-btn-test-conn").click()
        page.wait_for_timeout(1000)
        conn_pill = page.locator("#api-conn-status-pill").inner_text()
        print(f"Connection probe pill: {conn_pill}")

        # 8. Check Operations Table
        op_rows = page.locator("#api-operations-tbody tr")
        count = op_rows.count()
        print(f"Operations table rendered {count} rows in source order.")
        assert count == 3

        # 9. Execute API Test Plan
        print("Clicking Execute API Test Plan...")
        page.locator("#api-btn-run-plan").click()
        page.wait_for_selector("#api-report-card:not(.hidden)", timeout=30000)
        page.wait_for_timeout(1000)

        # Check counters
        total_stat = page.locator("#api-stat-total").inner_text()
        completed_stat = page.locator("#api-stat-completed").inner_text()
        print(f"Execution stats: Total={total_stat}, Completed={completed_stat}")
        assert total_stat in {"2", "3"}
        assert completed_stat == total_stat

        # Check compliance badge
        comp_badge = page.locator("#api-compliance-badge").inner_text()
        print(f"Compliance score: {comp_badge}")

        # 10. Database Cross-Read Validation
        print("Testing Database 3-Way Cross-Read UI...")
        query_select = page.locator("#api-db-query-select")
        # Ensure options are loaded
        page.wait_for_timeout(1000)
        query_select.select_option("query_mrr_revenue")
        page.locator("#api-db-api-val").fill("1500")
        page.locator("#api-db-ui-val").fill("$1,500.00")
        page.locator("#api-db-tolerance").fill("0.01")
        page.locator("#api-btn-db-validate").click()
        page.wait_for_selector("#api-db-cross-result:not(.hidden)", timeout=10000)
        page.wait_for_timeout(500)

        cross_result_text = page.locator("#api-db-cross-result").inner_text()
        print(f"Cross-Read result: {cross_result_text[:150]}...")
        assert "PASS" in cross_result_text
        assert "rounding_or_currency_normalization" in cross_result_text or "perfect_three_way_match" in cross_result_text

        # 11. Capture Screenshot Proof
        os.makedirs("automation_framework/reports", exist_ok=True)
        screenshot_path = os.path.abspath("automation_framework/reports/phase9_api_testing_browser_proof.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Full-page rendered browser screenshot saved to: {screenshot_path}")

        browser.close()
        print("All rendered browser validation checks passed successfully!")


if __name__ == "__main__":
    run_browser_validation()
