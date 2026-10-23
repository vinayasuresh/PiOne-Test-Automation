"""Capture high-resolution UI screenshots of PiOne Testmate for the presentation."""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def capture_all():
    out_dir = Path(__file__).resolve().parents[1] / "presentation" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 1440x900 viewport with 2x device scale factor for ultra-sharp visuals
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = context.new_page()

        print("[CAPTURE] Opening PiOne Testmate at http://127.0.0.1:8000")
        page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        time.sleep(1)

        def dismiss_modals():
            page.evaluate("""
                document.querySelectorAll('#actionable-error-modal, #danger-confirm-modal, #exec-confirm-modal').forEach(m => m.classList.add('hidden'));
                for(let i=1; i<=8; i++) unlockStep(i);
            """)

        # 1. Step 1: Scan Application
        print("[CAPTURE] 1. Scan Application")
        page.fill("#url-input", "https://demo.pione.ai/enterprise-app")
        page.fill("#username-input", "qa_lead@enterprise.com")
        page.fill("#password-input", "SuperSecretToken2026!")
        page.screenshot(path=str(out_dir / "01_scan_application.png"), full_page=False)

        # 2. Demo Mode Discovery -> Step 2: Choose Modules
        print("[CAPTURE] 2. Choose Modules")
        demo_btn = page.locator("#btn-mode-demo")
        if demo_btn.is_visible():
            demo_btn.click()
            time.sleep(0.3)
        page.click("#btn-discover")
        time.sleep(1)
        page.screenshot(path=str(out_dir / "02_choose_modules.png"), full_page=False)

        # 3. Step 3: Manual Review
        print("[CAPTURE] 3. Manual Review")
        page.click("#btn-modules-continue")
        time.sleep(1)
        page.screenshot(path=str(out_dir / "03_manual_review.png"), full_page=False)

        # 4. Step 4: Crawl Progress Telemetry
        print("[CAPTURE] 4. Crawl Progress Telemetry")
        page.click("#btn-start-crawl")
        time.sleep(2)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "04_crawl_telemetry.png"), full_page=False)

        # 5. Step 5: Route Health & Element Catalog
        print("[CAPTURE] 5. Route Health & Element Catalog")
        dismiss_modals()
        page.evaluate("switchStep(5);")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "05_route_health_catalog.png"), full_page=False)

        # 6. Step 6: Security Assessment Panel
        print("[CAPTURE] 6. Security Assessment Panel")
        dismiss_modals()
        sec_panel = page.locator("#security-review-panel")
        if sec_panel.is_visible():
            sec_panel.scroll_into_view_if_needed()
            time.sleep(0.5)
            sec_panel.screenshot(path=str(out_dir / "06_security_assessment.png"))

        # 7. Step 7: Multi-Provider AI Test Planning
        print("[CAPTURE] 7. AI Test Planning")
        dismiss_modals()
        page.evaluate("switchStep(6);")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "07_ai_test_planning.png"), full_page=False)

        # 8. Step 8: Controlled Test Execution
        print("[CAPTURE] 8. Controlled Test Execution")
        dismiss_modals()
        page.evaluate("switchStep(7);")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "08_controlled_execution.png"), full_page=False)

        # 9. Step 9: Audit Reports & Downloads
        print("[CAPTURE] 9. Audit Reports & Downloads")
        dismiss_modals()
        page.evaluate("switchStep(8);")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "09_audit_reports.png"), full_page=False)

        # 10. View: Saved Runs Dashboard
        print("[CAPTURE] 10. Saved Runs Dashboard")
        dismiss_modals()
        page.evaluate("switchView('saved-runs'); if (typeof renderSavedRuns === 'function') renderSavedRuns();")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "10_saved_runs_dashboard.png"), full_page=False)

        # 11. View: Regression Suites
        print("[CAPTURE] 11. Regression Suites")
        dismiss_modals()
        page.evaluate("switchView('suites'); if (typeof renderSuites === 'function') renderSuites();")
        time.sleep(1.5)
        dismiss_modals()
        page.screenshot(path=str(out_dir / "11_regression_suites.png"), full_page=False)

        browser.close()
        print(f"\n[SUCCESS] All 11 presentation screenshots captured successfully in:\n{out_dir}\n")

if __name__ == "__main__":
    capture_all()
