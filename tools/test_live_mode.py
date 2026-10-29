"""Full live mode verification test for PiOne Testmate.

Executes a real live crawl and real live DAST assessment against https://www.saucedemo.com
through the live running FastAPI server at http://127.0.0.1:8000 (mock: False).
"""
import json
import sys
import time
import httpx

BASE_URL = "http://127.0.0.1:8000"
TARGET_URL = "https://www.saucedemo.com"
USERNAME = "standard_user"
PASSWORD = "secret_sauce"

def log(msg):
    print(f"[LIVE TEST] {msg}", flush=True)

def main():
    log("Starting PiOne Testmate Live Mode verification...")
    client = httpx.Client(base_url=BASE_URL, timeout=180.0)

    # Step 1: Health check
    log("Step 1: Checking /health...")
    h = client.get("/health").json()
    log(f"Server health: status={h.get('status')}, build_id={h.get('build_id')}, browser_ready={h.get('browser_ready')}")
    assert h.get("status") == "ok", "Server not healthy"
    assert h.get("browser_ready") is True, "Browser runtime not ready"

    existing_run_id = sys.argv[1] if len(sys.argv) > 1 else None
    if existing_run_id:
        log(f"Using existing live scanned run: {existing_run_id}")
        run_id = existing_run_id
    else:
        # Step 2: Live Module Discovery
        log("Step 2: Conducting live module discovery on SauceDemo (mock: False)...")
        t0 = time.time()
        disc_resp = client.post("/discover-modules", json={
            "url": TARGET_URL,
            "username": USERNAME,
            "password": PASSWORD
        })
        log(f"Discovery response status: {disc_resp.status_code} in {time.time()-t0:.2f}s")
        if disc_resp.status_code != 200:
            log(f"Discovery error: {disc_resp.text}")
            sys.exit(1)

        disc_data = disc_resp.json()
        run_id = disc_data.get("run_id")
        auth_session_id = disc_data.get("auth_session_id")
        modules = disc_data.get("modules", [])
        log(f"Created run_id={run_id}, auth_session_id={auth_session_id[:16]}... Discovered modules: {len(modules)}")
        for m in modules:
            log(f"  - Module: id={m.get('id')}, label='{m.get('label')}', route='{m.get('route')}'")

        selected_module_ids = [m["id"] for m in modules]
        assert len(selected_module_ids) >= 1, "Expected at least 1 discovered module"

        # Step 3: Live Crawl
        log(f"Step 3: Conducting live crawl with selected modules {selected_module_ids} (mock: False)...")
        t0 = time.time()
        crawl_resp = client.post("/scan", json={
            "url": TARGET_URL,
            "username": USERNAME,
            "password": PASSWORD,
            "run_id": run_id,
            "auth_session_id": auth_session_id,
            "selected_module_ids": selected_module_ids,
            "mock": False
        })
        log(f"Crawl response status: {crawl_resp.status_code} in {time.time()-t0:.2f}s")
        if crawl_resp.status_code != 200:
            log(f"Crawl error: {crawl_resp.text}")
            sys.exit(1)

        crawl_data = crawl_resp.json()
        log(f"Crawl status: {crawl_data.get('status')}, routes={len(crawl_data.get('routes', []))}")
        for r in crawl_data.get("routes", []):
            log(f"  - Scanned Route: {r.get('path')} (status: {r.get('route_status')}, inputs: {len(r.get('components', {}).get('inputs', []))}, buttons: {len(r.get('components', {}).get('buttons', []))})")

    # Step 4: Live DAST Security Review
    log("Step 4: Launching live DAST security review...")
    sec_start_resp = client.post(f"/runs/{run_id}/security/start", json={
        "assessment_mode": "review",
        "authorized": True,
        "active_opt_in": True,
        "checks": [
            "headers", "cookies", "mixed_content", "disclosure",
            "api_inventory", "auth_boundary", "error_disclosure",
            "cors_probe", "reflection_canary"
        ],
        "max_requests": 20,
        "max_seconds": 120
    })
    log(f"Security start status: {sec_start_resp.status_code}")
    if sec_start_resp.status_code != 202:
        log(f"Security start error: {sec_start_resp.text}")
        sys.exit(1)

    sec_data = sec_start_resp.json()
    log(f"Review ID: {sec_data.get('review_id')}, Initial status: {sec_data.get('status')}")

    # Poll DAST execution
    log("Step 5: Polling live DAST review execution...")
    report = None
    for poll_idx in range(60):
        time.sleep(2)
        poll_resp = client.get(f"/runs/{run_id}/security")
        report = poll_resp.json()
        status = report.get("status")
        completed = report.get("completed_pages", 0)
        pending = report.get("pending_pages", 0)
        findings_count = len(report.get("findings", []))
        log(f"  Poll {poll_idx+1}: status={status}, completed_pages={completed}, pending_pages={pending}, findings={findings_count}")
        if status in {"completed", "completed_with_gaps", "partial", "stopped", "interrupted", "route_validation_failed", "scanner_error"}:
            break

    terminal_status = report.get("status")
    log(f"DAST terminal status: {terminal_status}")
    assert terminal_status in {"completed", "completed_with_gaps", "partial"}, f"Unexpected DAST status: {terminal_status}"

    summary = report.get("summary", {})
    findings = report.get("findings", [])
    endpoints = report.get("endpoints", [])
    log("================ DAST SUMMARY ================")
    log(f"Endpoints Assessed: {len(endpoints)}")
    for ep in endpoints:
        log(f"  {ep.get('method')} {ep.get('url')} [HTTP {ep.get('status')}]")
    log(f"Total Findings: {summary.get('total')}")
    log(f"Findings by Severity: {summary.get('by_severity')}")
    log(f"Findings by OWASP Category: {summary.get('by_owasp')}")
    log(f"Observations: {summary.get('observations')}")
    log(f"Confirmed: {summary.get('confirmed')}")

    # Step 6: Verify Export Endpoints
    log("Step 6: Verifying Export Endpoints...")
    json_resp = client.get(f"/runs/{run_id}/security/export/json")
    assert json_resp.status_code == 200, f"JSON export failed: {json_resp.status_code}"
    log(f"  [OK] JSON export: {len(json_resp.content)} bytes")

    md_resp = client.get(f"/runs/{run_id}/security/export/markdown")
    assert md_resp.status_code == 200, f"Markdown export failed: {md_resp.status_code}"
    log(f"  [OK] Markdown export: {len(md_resp.content)} bytes")

    excel_resp = client.get(f"/runs/{run_id}/security/export/excel")
    assert excel_resp.status_code == 200, f"Excel export failed: {excel_resp.status_code}"
    log(f"  [OK] Excel export: {len(excel_resp.content)} bytes")

    log("ALL LIVE MODE VERIFICATIONS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
