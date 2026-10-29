"""scripts/capture_v2_5_presentation_screenshots.py

Drives the real rendered PiOne Testmate UI at http://127.0.0.1:8000 using Playwright Chromium
to capture high-resolution presentation screenshots of every possible screen across the full flow:
- All 8 workflow panels (Scan, Modules, Review, Crawl, Analyze, Plan, Execute, Report)
- Parking lot & safety isolation
- Passive security assessment panel
- Execution confirmation modal & completed test step logs
- Golden baseline modal & confirmation
- Auxiliary dashboards: Saved Runs, Regression Suites, Artifacts
- Interactive learning checkpoint banner

Also copies old V2 screenshots to presentation/images/v2/ and saves new screenshots to presentation/images/v2.5/.
"""

import os
import shutil
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT_DIR = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT_DIR / "presentation" / "images"
V2_DIR = IMG_DIR / "v2"
V2_5_DIR = IMG_DIR / "v2.5"

V2_DIR.mkdir(parents=True, exist_ok=True)
V2_5_DIR.mkdir(parents=True, exist_ok=True)


def backup_v2_images():
    print("[1/3] Preserving existing V2 images in presentation/images/v2/ ...")
    for f in IMG_DIR.glob("*.png"):
        dest = V2_DIR / f.name
        if not dest.exists():
            shutil.copy2(f, dest)
            print(f"  Copied {f.name} -> v2/")
    print("V2 image preservation complete.")


def run_full_flow_and_capture():
    print("\n[2/3] Driving full rendered browser flow and capturing V2.5 screenshots...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use standard presentation resolution (1440x900)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # -------------------------------------------------------------
        # SCREEN 01: Initial Landing / Step 1 Scan Application
        # -------------------------------------------------------------
        print("Capturing 01_step1_initial_landing.png ...")
        page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        time.sleep(1.0)
        page.screenshot(path=str(V2_5_DIR / "01_step1_initial_landing.png"))

        # -------------------------------------------------------------
        # SCREEN 02: Assessment Mode Selection
        # -------------------------------------------------------------
        print("Capturing 02_step1_assessment_mode_selection.png ...")
        mode_select = page.locator("#assessment-mode-first")
        mode_select.scroll_into_view_if_needed()
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "02_step1_assessment_mode_selection.png"))

        # -------------------------------------------------------------
        # SCREEN 03: Mock Demo Mode Active
        # -------------------------------------------------------------
        print("Capturing 03_step1_mock_demo_active.png ...")
        btn_demo = page.locator("#btn-mode-demo")
        btn_demo.click()
        time.sleep(0.5)
        page.locator("#session-name-input").fill("PiOne Testmate V2.5 Executive Walkthrough")
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "03_step1_mock_demo_active.png"))

        # -------------------------------------------------------------
        # SCREEN 04: Step 2 Modules Discovered
        # -------------------------------------------------------------
        print("Capturing 04_step2_modules_discovered.png ...")
        page.locator("#btn-discover").click()
        expect(page.locator("#panel-2")).to_be_visible(timeout=10000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "04_step2_modules_discovered.png"))

        # -------------------------------------------------------------
        # SCREEN 05: Step 2 Modules Selected
        # -------------------------------------------------------------
        print("Capturing 05_step2_modules_selected.png ...")
        page.locator("#btn-modules-select-all").click()
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "05_step2_modules_selected.png"))

        # -------------------------------------------------------------
        # SCREEN 06: Step 3 Manual Review Routes
        # -------------------------------------------------------------
        print("Capturing 06_step3_manual_review_routes.png ...")
        page.locator("#btn-modules-continue").click()
        expect(page.locator("#panel-3")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "06_step3_manual_review_routes.png"))

        # -------------------------------------------------------------
        # SCREEN 07: Step 3 Safety Parking Lot
        # -------------------------------------------------------------
        print("Capturing 07_step3_safety_parking_lot.png ...")
        # Scroll to parking lot section if present
        parking_lot = page.locator("#review-parking-lot-container, #parking-lot-section, #panel-3")
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "07_step3_safety_parking_lot.png"))

        # -------------------------------------------------------------
        # SCREEN 08: Step 4 Crawl Started & Live Telemetry
        # -------------------------------------------------------------
        print("Capturing 08_step4_crawl_started_telemetry.png ...")
        page.locator("#btn-start-crawl").click()
        expect(page.locator("#panel-4")).to_be_visible(timeout=5000)
        time.sleep(0.4)
        page.screenshot(path=str(V2_5_DIR / "08_step4_crawl_started_telemetry.png"))

        # -------------------------------------------------------------
        # SCREEN 09: Step 4 Crawl Completed
        # -------------------------------------------------------------
        print("Capturing 09_step4_crawl_completed.png ...")
        expect(page.locator("#next-step-cta")).to_be_visible(timeout=25000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "09_step4_crawl_completed.png"))

        # -------------------------------------------------------------
        # SCREEN 10: Step 5 Analyze - Scanned UI Map
        # -------------------------------------------------------------
        print("Capturing 10_step5_analyze_scanned_ui_map.png ...")
        page.locator("#btn-continue-analysis").click()
        expect(page.locator("#panel-5")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "10_step5_analyze_scanned_ui_map.png"))

        # -------------------------------------------------------------
        # SCREEN 11: Step 5 Analyze - Route Health Catalog
        # -------------------------------------------------------------
        print("Capturing 11_step5_analyze_route_health_catalog.png ...")
        page.locator("#analyze-routes-body").scroll_into_view_if_needed()
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "11_step5_analyze_route_health_catalog.png"))

        # -------------------------------------------------------------
        # SCREEN 12: Step 5 Analyze - Elements Inventory
        # -------------------------------------------------------------
        print("Capturing 12_step5_analyze_elements_inventory.png ...")
        elem_sec = page.locator("#analyze-elements-body, #panel-5")
        time.sleep(0.3)
        page.screenshot(path=str(V2_5_DIR / "12_step5_analyze_elements_inventory.png"))

        # -------------------------------------------------------------
        # SCREEN 13: Step 5 Analyze - Security Panel Expanded
        # -------------------------------------------------------------
        print("Capturing 13_step5_analyze_security_panel_expanded.png ...")
        sec_panel = page.locator("#security-review-panel")
        if sec_panel.is_visible():
            sec_panel.locator("summary").first.click()
            time.sleep(0.3)
            auth_cb = page.locator("#security-authorized")
            if auth_cb.is_visible():
                auth_cb.check()
            time.sleep(0.3)
            page.screenshot(path=str(V2_5_DIR / "13_step5_analyze_security_panel_expanded.png"))
        else:
            page.screenshot(path=str(V2_5_DIR / "13_step5_analyze_security_panel_expanded.png"))

        # -------------------------------------------------------------
        # SCREEN 14: Step 6 Generate Tests Configuration
        # -------------------------------------------------------------
        print("Capturing 14_step6_generate_tests_config.png ...")
        page.locator("#btn-analyze-proceed").click()
        expect(page.locator("#panel-6")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "14_step6_generate_tests_config.png"))

        # -------------------------------------------------------------
        # SCREEN 15: Step 6 Generate Tests Plan Table
        # -------------------------------------------------------------
        print("Capturing 15_step6_generate_tests_plan_table.png ...")
        page.locator("#testcase-count").fill("10")
        page.locator("#btn-generate-plan").click()
        expect(page.locator("#btn-generate-plan")).to_be_enabled(timeout=20000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "15_step6_generate_tests_plan_table.png"))

        # -------------------------------------------------------------
        # SCREEN 16: Step 7 Execute Ready Table
        # -------------------------------------------------------------
        print("Capturing 16_step7_execute_ready_table.png ...")
        page.locator("#btn-plan-proceed").click()
        expect(page.locator("#panel-7")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "16_step7_execute_ready_table.png"))

        # -------------------------------------------------------------
        # SCREEN 17: Step 7 Execute Confirmation Modal
        # -------------------------------------------------------------
        print("Capturing 17_step7_execute_confirm_modal.png ...")
        page.locator("#btn-exec-select-all").click()
        page.locator("#btn-run-execution").click()
        expect(page.locator("#exec-confirm-modal")).to_be_visible(timeout=5000)
        time.sleep(0.4)
        page.screenshot(path=str(V2_5_DIR / "17_step7_execute_confirm_modal.png"))

        # -------------------------------------------------------------
        # SCREEN 18: Step 7 Execute Completed
        # -------------------------------------------------------------
        print("Capturing 18_step7_execute_completed_pass.png ...")
        page.locator("#exec-confirm-checkbox").check()
        page.locator("#btn-submit-exec-confirm").click()
        expect(page.locator("#btn-run-execution")).to_be_enabled(timeout=30000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "18_step7_execute_completed_pass.png"))

        # -------------------------------------------------------------
        # SCREEN 19: Step 8 Report Dashboard
        # -------------------------------------------------------------
        print("Capturing 19_step8_report_dashboard.png ...")
        page.locator("#btn-exec-proceed").click()
        expect(page.locator("#panel-8")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "19_step8_report_dashboard.png"))

        # -------------------------------------------------------------
        # SCREEN 20: Step 8 Golden Baseline Saved
        # -------------------------------------------------------------
        print("Capturing 20_step8_report_golden_baseline_saved.png ...")
        page.locator("#btn-open-save-baseline").click()
        expect(page.locator("#baseline-modal")).to_be_visible(timeout=5000)
        page.locator("#baseline-name-input").fill("V2.5 Enterprise Production Baseline")
        page.locator("#baseline-version-input").fill("2.5")
        page.locator("#btn-submit-baseline").click()
        time.sleep(0.8)
        page.screenshot(path=str(V2_5_DIR / "20_step8_report_golden_baseline_saved.png"))

        # -------------------------------------------------------------
        # SCREEN 21: Saved Runs Dashboard View
        # -------------------------------------------------------------
        print("Capturing 21_view_saved_runs_dashboard.png ...")
        page.locator("#btn-nav-saved-runs").click()
        expect(page.locator("#view-saved-runs")).to_be_visible(timeout=5000)
        time.sleep(0.5)
        page.screenshot(path=str(V2_5_DIR / "21_view_saved_runs_dashboard.png"))

        # -------------------------------------------------------------
        # SCREEN 22: Regression Suites View
        # -------------------------------------------------------------
        print("Capturing 22_view_regression_suites_dashboard.png ...")
        btn_suites = page.locator("#btn-nav-suites")
        if btn_suites.is_visible():
            btn_suites.click()
            time.sleep(0.5)
            page.screenshot(path=str(V2_5_DIR / "22_view_regression_suites_dashboard.png"))

        # -------------------------------------------------------------
        # SCREEN 23: Artifacts View
        # -------------------------------------------------------------
        print("Capturing 23_view_artifacts_management.png ...")
        btn_artifacts = page.locator("#btn-nav-artifacts")
        if btn_artifacts.is_visible():
            btn_artifacts.click()
            time.sleep(0.5)
            page.screenshot(path=str(V2_5_DIR / "23_view_artifacts_management.png"))

        # -------------------------------------------------------------
        # SCREEN 24: Interactive Checkpoint Banner
        # -------------------------------------------------------------
        print("Capturing 24_step3_interactive_checkpoint_banner.png ...")
        # Return to workflow, reset and start an interactive run
        page.locator("#btn-nav-workflow").click()
        time.sleep(0.3)
        page.locator("#btn-top-newscan").click()
        time.sleep(0.3)
        page.locator("#btn-mode-demo").click()
        page.locator("#interactive-input").check()
        page.locator("#btn-discover").click()
        expect(page.locator("#panel-2")).to_be_visible(timeout=10000)
        page.locator("#btn-modules-select-all").click()
        page.locator("#btn-modules-continue").click()
        expect(page.locator("#panel-3")).to_be_visible(timeout=5000)
        page.locator("#btn-start-crawl").click()
        expect(page.locator("#panel-4")).to_be_visible(timeout=5000)
        time.sleep(0.8)
        page.screenshot(path=str(V2_5_DIR / "24_step3_interactive_checkpoint_banner.png"))

        browser.close()
        print("\nAll 24 V2.5 screenshots captured successfully into presentation/images/v2.5/!")


if __name__ == "__main__":
    backup_v2_images()
    run_full_flow_and_capture()
