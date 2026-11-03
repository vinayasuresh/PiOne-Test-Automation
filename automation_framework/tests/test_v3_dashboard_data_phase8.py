"""Phase 8 tests: OmniTest V3 Dashboard Data Validation.

Verifies:
1. Matching dashboard produces 'not_reproduced' (passes release assurance).
2. Mismatched dashboard produces evidence-backed 'reproduced' and auto-assigns incident.
3. Target outage and authentication expiry produce truthful 'blocked' verdicts.
4. UI pass cannot mask failed source data assertion (stale data, missing fields, source 5xx).
5. Scalar currencies, percentages, chart series, and tabular row structures compare cleanly.
6. Timezone offsets normalize accurately to UTC without offset distortion.
7. Headless Playwright capture produces verified visible value, screenshot, and SHA-256 evidence.
"""

from __future__ import annotations

import http.server
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import Response

from api.db.repository import DBRepository
from api.models.v3_assurance_models import V3IncidentIntakeRequest
from api.models.v3_data_assertion_models import DataAssertionEvaluationRequest
from api.models.v3_orchestration_models import AssuranceScenarioRequest, V3OrchestrateRequest
from api.routers.v3_assurance import (
    evaluate_data_assertion_scenario,
    intake_incident,
    orchestrate_job,
    register_assurance_scenario,
    register_data_assertion_scenario,
)
from api.services.v3_data_assertion_service import (
    capture_dashboard_ui_value,
    compare_dashboard_data,
    parse_timestamp,
    read_approved_source,
)
from api.services.v3_orchestration_service import execute_approved_scenario


# Includes a real headless Chromium capture; run it in the opt-in browser gate
# rather than the Windows-safe unit suite.
pytestmark = pytest.mark.browser


class _DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/dashboard"):
            html = """<!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>OmniTest App Dashboard</title></head>
            <body>
                <header><h1>Executive Dashboard</h1></header>
                <div id="kpi-spend" data-metric="spend">$1,500.00</div>
                <div id="kpi-mismatch">$1,200.00</div>
                <div id="kpi-percent">99.2%</div>
                <div id="kpi-chart" data-series="[10, 20.0, 30]">
                    <span class="bar" data-value="10">10</span>
                    <span class="bar" data-value="20.0">20.0</span>
                    <span class="bar" data-value="30">30</span>
                </div>
                <table id="tbl-users">
                    <tbody>
                        <tr><td>Alice</td><td>Admin</td></tr>
                        <tr><td>Bob</td><td>User</td></tr>
                    </tbody>
                </table>
            </body>
            </html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif self.path.startswith("/protected"):
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized")
        elif self.path.startswith("/login"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Login Screen</body></html>")
        elif self.path.startswith("/api/metrics"):
            now = datetime.now(timezone.utc).isoformat()
            data = {
                "spend": {"total": 1500},
                "availability": 0.992,
                "series": [10, 20.0, 30],
                "users": [["Alice", "Admin"], ["Bob", "User"]],
                "updated_at": now,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def dashboard_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _DashboardHandler)
    host, port = server.server_address
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base_url
    server.shutdown()


def _setup_contract(base_url: str) -> dict:
    return DBRepository.register_v3_api_contract({
        "name": f"OmniTest Metrics {uuid4().hex[:8]}",
        "version": "1.0.0",
        "source_type": "openapi",
        "base_url": base_url,
        "environment": "staging",
        "approved": True,
    }, [{
        "operation_key": f"get-metrics-{uuid4().hex[:8]}",
        "method": "GET",
        "path": "/api/metrics",
        "expected_status_codes": [200],
        "required_fields": ["spend.total"],
        "max_latency_ms": 3000,
        "auth_requirement": "none",
        "auth_fixture_ref": "",
        "approved_role": "analyst",
        "allow_unauthenticated_probe": False,
        "safe_read_only": True,
        "is_enabled": True,
    }])


def test_matching_dashboard_produces_not_reproduced(dashboard_server, monkeypatch):
    """Matching rendered dashboard value and API source produces 'not_reproduced'."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("ALLOW_LOCAL_TARGETS", "1")

    contract = _setup_contract(dashboard_server)
    op_id = contract["operations"][0]["operation_id"]

    data_scenario = DBRepository.register_v3_data_assertion_scenario({
        "name": "Executive Monthly Spend",
        "route": "/dashboard",
        "ui_selector": "#kpi-spend",
        "metric_label": "Spend",
        "source_operation_id": op_id,
        "source_field": "spend.total",
        "source_timestamp_field": "updated_at",
        "expected_filters": {},
        "timezone": "UTC",
        "freshness_window_minutes": 60,
        "tolerance": "0.01",
        "value_format": "currency",
        "approved": True,
    })

    assurance_sc = DBRepository.register_v3_assurance_scenario({
        "name": "Dashboard Spend Match Test",
        "feature": "Billing",
        "environment": "staging",
        "authorized_role": "analyst",
        "target_origin": dashboard_server,
        "scenario_type": "data_assertion",
        "reference_id": data_scenario["data_assertion_scenario_id"],
        "max_actions": 3,
        "is_approved": True,
    })

    intake = intake_incident(V3IncidentIntakeRequest(
        correlation_id=f"match-{uuid4().hex}",
        grouping_key="billing:spend:match",
        summary="Spend KPI verification",
        affected_feature="Billing",
        expected_behavior="Shows $1,500.00",
        actual_behavior="Reported anomaly",
        target=f"{dashboard_server}/dashboard",
        authorized_role="analyst",
        mode="functional_only",
    ), Response())

    job_id = intake["incident"]["jobs"][0]["job_id"]
    result = orchestrate_job(job_id, V3OrchestrateRequest(scenario_id=assurance_sc["scenario_id"]))

    assert result["status"] == "completed"
    assert result["job"]["verdict"] == "not_reproduced"
    assert result["outcome"]["category"] == "matched"
    assert result["job"]["state"] == "completed"

    manifests = DBRepository.get_v3_job(job_id)["evidence_manifests"]
    assert len(manifests) >= 2
    types = {m["content_type"] for m in manifests}
    assert "application/json" in types
    assert "image/png" in types

    observations = DBRepository.list_v3_data_assertion_observations(data_scenario["data_assertion_scenario_id"])
    assert len(observations) >= 1
    latest = observations[-1]
    assert latest["result"] == "pass"
    assert latest["classification"] == "matched"
    assert float(latest["normalized_ui_value"]) == 1500.0
    assert float(latest["normalized_source_value"]) == 1500.0


def test_mismatched_dashboard_produces_evidence_backed_reproduced(dashboard_server, monkeypatch):
    """Mismatched UI rendering truthfully produces 'reproduced' and auto-assigns."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("ALLOW_LOCAL_TARGETS", "1")

    DBRepository.create_v3_assignment_rule({
        "feature": "Revenue",
        "owner_team": "FinOps Core",
        "severity_threshold": "High",
        "escalation_window_minutes": 15,
        "is_enabled": True,
    })

    contract = _setup_contract(dashboard_server)
    op_id = contract["operations"][0]["operation_id"]

    data_scenario = DBRepository.register_v3_data_assertion_scenario({
        "name": "Mismatched Spend Test",
        "route": "/dashboard",
        "ui_selector": "#kpi-mismatch",  # Renders $1,200.00 while source is 1500
        "metric_label": "Spend",
        "source_operation_id": op_id,
        "source_field": "spend.total",
        "source_timestamp_field": "updated_at",
        "expected_filters": {},
        "timezone": "UTC",
        "freshness_window_minutes": 60,
        "tolerance": "0.01",
        "value_format": "currency",
        "approved": True,
    })

    assurance_sc = DBRepository.register_v3_assurance_scenario({
        "name": "Dashboard Spend Anomaly",
        "feature": "Revenue",
        "environment": "staging",
        "authorized_role": "analyst",
        "target_origin": dashboard_server,
        "scenario_type": "data_assertion",
        "reference_id": data_scenario["data_assertion_scenario_id"],
        "max_actions": 3,
        "is_approved": True,
    })

    intake = intake_incident(V3IncidentIntakeRequest(
        correlation_id=f"mismatch-{uuid4().hex}",
        grouping_key="revenue:spend:anomaly",
        summary="Spend KPI appears inaccurate",
        affected_feature="Revenue",
        expected_behavior="Spend is $1,500.00",
        actual_behavior="Spend shows $1,200.00",
        target=f"{dashboard_server}/dashboard",
        authorized_role="analyst",
        mode="functional_only",
    ), Response())

    job_id = intake["incident"]["jobs"][0]["job_id"]
    result = orchestrate_job(job_id, V3OrchestrateRequest(scenario_id=assurance_sc["scenario_id"]))

    assert result["status"] == "completed"
    assert result["job"]["verdict"] == "reproduced"
    assert result["outcome"]["category"] == "visual_source_mismatch"

    incident = DBRepository.get_v3_incident(intake["incident"]["incident_id"])
    assert incident["owner_team"] == "FinOps Core"
    assert incident["status"] == "assigned"


def test_target_outage_or_auth_expiry_produces_blocked(dashboard_server, monkeypatch):
    """Target outages and authorization failures produce truthful 'blocked' verdicts."""
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    monkeypatch.setenv("ALLOW_LOCAL_TARGETS", "1")

    contract = _setup_contract(dashboard_server)
    op_id = contract["operations"][0]["operation_id"]

    # Test Auth Expiry (Protected route 401)
    data_sc_auth = DBRepository.register_v3_data_assertion_scenario({
        "name": "Protected KPI Auth Check",
        "route": "/protected",
        "ui_selector": "#kpi-spend",
        "metric_label": "Protected",
        "source_operation_id": op_id,
        "source_field": "spend.total",
        "tolerance": "0",
        "approved": True,
    })
    auth_sc = DBRepository.register_v3_assurance_scenario({
        "name": "Auth Expiry Scenario",
        "feature": "Security",
        "environment": "staging",
        "authorized_role": "analyst",
        "target_origin": dashboard_server,
        "scenario_type": "data_assertion",
        "reference_id": data_sc_auth["data_assertion_scenario_id"],
        "max_actions": 3,
        "is_approved": True,
    })
    intake_auth = intake_incident(V3IncidentIntakeRequest(
        correlation_id=f"auth-{uuid4().hex}",
        grouping_key="security:auth:expired",
        summary="Auth failure verification",
        affected_feature="Security",
        expected_behavior="Page opens",
        actual_behavior="Unauthorized",
        target=f"{dashboard_server}/protected",
        authorized_role="analyst",
        mode="functional_only",
    ), Response())
    auth_res = orchestrate_job(intake_auth["incident"]["jobs"][0]["job_id"], V3OrchestrateRequest(scenario_id=auth_sc["scenario_id"]))
    assert auth_res["job"]["verdict"] == "blocked"
    assert auth_res["outcome"]["category"] == "auth_expired"

    # Test Target Outage (Unused port)
    dead_target = "http://127.0.0.1:59999"
    data_sc_outage = DBRepository.register_v3_data_assertion_scenario({
        "name": "Outage Target Data Scenario",
        "route": "/dashboard",
        "ui_selector": "#kpi-spend",
        "metric_label": "Spend",
        "source_operation_id": op_id,
        "source_field": "spend.total",
        "tolerance": "0",
        "approved": True,
    })
    outage_sc = DBRepository.register_v3_assurance_scenario({
        "name": "Outage Check",
        "feature": "Infra",
        "environment": "staging",
        "authorized_role": "analyst",
        "target_origin": dead_target,
        "scenario_type": "data_assertion",
        "reference_id": data_sc_outage["data_assertion_scenario_id"],
        "max_actions": 3,
        "is_approved": True,
    })
    intake_outage = intake_incident(V3IncidentIntakeRequest(
        correlation_id=f"outage-{uuid4().hex}",
        grouping_key="infra:outage:dead",
        summary="Server outage verification",
        affected_feature="Infra",
        expected_behavior="Server reachable",
        actual_behavior="Connection refused",
        target=f"{dead_target}/dashboard",
        authorized_role="analyst",
        mode="functional_only",
    ), Response())
    outage_res = orchestrate_job(intake_outage["incident"]["jobs"][0]["job_id"], V3OrchestrateRequest(scenario_id=outage_sc["scenario_id"]))
    assert outage_res["job"]["verdict"] == "blocked"
    assert outage_res["outcome"]["category"] in {"target_unavailable", "target_timeout"}


def test_ui_pass_cannot_mask_failed_source_data_assertion():
    """A rendered UI pass must never mask a stale or failed source data assertion."""
    scenario = {
        "expected_filters": {"period": "month"},
        "freshness_window_minutes": 30,
        "tolerance": "0.01",
        "value_format": "currency",
    }
    evaluation = {
        "ui_value": "$1,500.00",
        "filter_context": {"period": "month"},
        "source_filter_context": {"period": "month"},
        "captured_at": datetime.now(timezone.utc),
    }

    # Case A: Stale source data (older than 30 minutes)
    stale_source = {
        "result": "ok",
        "source_value": "1500",
        "source_fresh_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "source_metadata": {},
    }
    stale_comp = compare_dashboard_data(scenario, evaluation, stale_source)
    assert stale_comp["result"] == "fail"
    assert stale_comp["classification"] in {"stale_data", "stale_source_data"}

    # Case B: Missing source data field
    missing_source = {
        "result": "blocked",
        "classification": "missing_data",
        "source_value": None,
        "source_metadata": {"status_code": 200},
    }
    missing_comp = compare_dashboard_data(scenario, evaluation, missing_source)
    assert missing_comp["result"] == "blocked"
    assert missing_comp["classification"] in {"missing_data", "missing_source_data"}

    # Case C: Source API server error (500)
    error_source = {
        "result": "blocked",
        "classification": "source_unavailable",
        "source_value": None,
        "source_metadata": {"status_code": 500},
    }
    error_comp = compare_dashboard_data(scenario, evaluation, error_source)
    assert error_comp["result"] == "blocked"
    assert error_comp["classification"] == "source_unavailable"


def test_formats_currency_percentage_table_rows_and_chart_series():
    """Validates currencies, percentages, chart series, and tabular row structures."""
    now = datetime.now(timezone.utc)

    # Currency format
    curr_sc = {"expected_filters": {}, "freshness_window_minutes": 60, "tolerance": "0.01", "value_format": "currency"}
    curr_res = compare_dashboard_data(curr_sc, {"ui_value": "€ 2,450.50", "captured_at": now}, {"result": "ok", "source_value": "2450.50", "source_fresh_at": now.isoformat()})
    assert curr_res["result"] == "pass"
    assert curr_res["classification"] == "matched"

    # Percentage format
    pct_sc = {"expected_filters": {}, "freshness_window_minutes": 60, "tolerance": "0.001", "value_format": "percentage"}
    pct_res = compare_dashboard_data(pct_sc, {"ui_value": "99.2%", "captured_at": now}, {"result": "ok", "source_value": "0.992", "source_fresh_at": now.isoformat()})
    assert pct_res["result"] == "pass"
    assert pct_res["classification"] == "matched"

    # Chart series format
    chart_sc = {"expected_filters": {}, "freshness_window_minutes": 60, "tolerance": "0.05", "value_format": "chart_series"}
    chart_pass = compare_dashboard_data(chart_sc, {"ui_value": "[10, 20.0, 30]", "captured_at": now}, {"result": "ok", "source_value": "[10, 20.02, 30]", "source_fresh_at": now.isoformat()})
    assert chart_pass["result"] == "pass"
    assert chart_pass["classification"] == "rounding_mismatch"

    chart_fail = compare_dashboard_data(chart_sc, {"ui_value": "[10, 25.0, 30]", "captured_at": now}, {"result": "ok", "source_value": "[10, 20.0, 30]", "source_fresh_at": now.isoformat()})
    assert chart_fail["result"] == "fail"
    assert chart_fail["classification"] == "visual_source_mismatch"

    # Table rows format
    tbl_sc = {"expected_filters": {}, "freshness_window_minutes": 60, "tolerance": "0", "value_format": "table_rows"}
    tbl_pass = compare_dashboard_data(tbl_sc, {"ui_value": '[["Alice", "Admin"], ["Bob", "User"]]', "captured_at": now}, {"result": "ok", "source_value": '[["Alice", "Admin"], ["Bob", "User"]]', "source_fresh_at": now.isoformat()})
    assert tbl_pass["result"] == "pass"
    assert tbl_pass["classification"] == "matched"

    tbl_fail = compare_dashboard_data(tbl_sc, {"ui_value": '[["Alice", "Admin"]]', "captured_at": now}, {"result": "ok", "source_value": '[["Alice", "Admin"], ["Bob", "User"]]', "source_fresh_at": now.isoformat()})
    assert tbl_fail["result"] == "fail"
    assert tbl_fail["classification"] == "visual_source_mismatch"


def test_timezone_normalized_to_utc():
    """Source timestamps with non-UTC timezone offsets are normalized safely to UTC."""
    raw_ist = "2026-11-03T15:30:00+05:30"
    parsed_utc = parse_timestamp(raw_ist)
    assert parsed_utc is not None
    assert parsed_utc.tzinfo == timezone.utc
    assert parsed_utc.hour == 10
    assert parsed_utc.minute == 0

    sc = {"expected_filters": {}, "freshness_window_minutes": 60, "tolerance": "0", "value_format": "number"}
    captured = datetime(2026, 11, 3, 10, 15, 0, tzinfo=timezone.utc)
    res = compare_dashboard_data(
        sc,
        {"ui_value": "100", "captured_at": captured},
        {"result": "ok", "source_value": "100", "source_fresh_at": raw_ist},
    )
    assert res["result"] == "pass"
    assert res["classification"] == "matched"


def test_headless_browser_capture_with_playwright(dashboard_server, monkeypatch):
    """Playwright capture retrieves rendered DOM text, URL, and screenshot evidence."""
    monkeypatch.setenv("ALLOW_LOCAL_TARGETS", "1")
    capture = capture_dashboard_ui_value(
        target_origin=dashboard_server,
        route="/dashboard",
        ui_selector="#kpi-spend",
        filter_context={"period": "monthly"},
        value_format="currency",
        is_mock=True,
    )

    assert capture["result"] == "ok"
    assert capture["classification"] == "ui_captured"
    assert capture["ui_value"] == "$1,500.00"
    assert capture["selector"] == "#kpi-spend"
    assert "period=monthly" in capture["url"]
    assert Path(capture["screenshot_path"]).exists()
    assert len(capture["screenshot_checksum"]) == 64
