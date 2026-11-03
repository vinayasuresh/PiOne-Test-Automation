"""Phase-3 tests for safe dashboard-data validation and evidence classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from api.db.repository import DBRepository
from api.models.v3_data_assertion_models import DataAssertionScenarioRequest
from api.services.v3_data_assertion_service import compare_dashboard_data, read_approved_source


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _contract() -> dict:
    return {"base_url": "https://example.com", "is_approved": True, "environment": "staging"}


def _operation(**overrides) -> dict:
    value = {
        "method": "GET", "path": "/metrics", "expected_status_codes": [200],
        "safe_read_only": True, "is_enabled": True, "auth_requirement": "none",
    }
    value.update(overrides)
    return value


def _scenario(**overrides) -> dict:
    value = {
        "expected_filters": {"period": "month", "tenant": "acme"},
        "freshness_window_minutes": 60,
        "tolerance": "0.01",
        "value_format": "currency",
    }
    value.update(overrides)
    return value


def _evaluation(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    value = {
        "ui_value": "$1,000.00",
        "filter_context": {"period": "month", "tenant": "acme"},
        "source_filter_context": {"period": "month", "tenant": "acme"},
        "captured_at": now,
    }
    value.update(overrides)
    return value


def test_read_only_source_and_dashboard_value_match() -> None:
    source = read_approved_source(
        _contract(), _operation(), "metric.total", "updated_at",
        client=_client(lambda request: httpx.Response(200, json={
            "metric": {"total": "1000"}, "updated_at": datetime.now(timezone.utc).isoformat(),
        }, request=request)),
    )
    assert source["result"] == "ok"
    comparison = compare_dashboard_data(_scenario(), _evaluation(), source)
    assert comparison["result"] == "pass"
    assert comparison["classification"] == "matched"


def test_dashboard_rounding_tolerance_and_percentage_normalization() -> None:
    source = {
        "result": "ok", "source_value": "0.981", "source_fresh_at": datetime.now(timezone.utc).isoformat(),
        "source_metadata": {},
    }
    comparison = compare_dashboard_data(
        _scenario(value_format="percentage", tolerance="0.002"),
        _evaluation(ui_value="98%"), source,
    )
    assert comparison["result"] == "pass"
    assert comparison["classification"] in {"rounding_within_tolerance", "rounding_mismatch"}


def test_dashboard_scenario_rejects_invalid_tolerance_and_preserves_capture_time() -> None:
    with pytest.raises(ValidationError):
        DataAssertionScenarioRequest(
            name="Monthly spend", route="/dashboard", ui_selector="[data-kpi='spend']",
            source_operation_id="metric-read", source_field="metric.total", tolerance="not-a-number",
        )

    captured_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    result = compare_dashboard_data(
        _scenario(), _evaluation(captured_at=captured_at),
        {"result": "ok", "source_value": "1000", "source_fresh_at": captured_at.isoformat(), "source_metadata": {}},
    )
    assert result["result"] == "pass"


def test_dashboard_data_classifies_stale_missing_and_filter_mismatch() -> None:
    stale = compare_dashboard_data(
        _scenario(), _evaluation(),
        {"result": "ok", "source_value": "1000", "source_fresh_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), "source_metadata": {}},
    )
    assert stale["result"] == "fail"
    assert stale["classification"] in {"stale_source_data", "stale_data"}

    missing = compare_dashboard_data(_scenario(), _evaluation(), {"result": "blocked", "classification": "missing_source_data", "source_metadata": {}})
    assert missing["result"] == "blocked"
    assert missing["classification"] in {"missing_source_data", "missing_data"}

    filters = compare_dashboard_data(
        _scenario(), _evaluation(filter_context={"period": "week"}),
        {"result": "ok", "source_value": "1000", "source_fresh_at": datetime.now(timezone.utc).isoformat(), "source_metadata": {}},
    )
    assert filters["result"] == "fail"
    assert filters["classification"] == "filter_mismatch"


def test_dashboard_data_detects_visual_source_mismatch_and_blocks_unsafe_source() -> None:
    mismatch = compare_dashboard_data(
        _scenario(tolerance="0"), _evaluation(ui_value="$1,100.00"),
        {"result": "ok", "source_value": "1000", "source_fresh_at": datetime.now(timezone.utc).isoformat(), "source_metadata": {}},
    )
    assert mismatch["result"] == "fail"
    assert mismatch["classification"] == "visual_source_mismatch"

    unsafe = read_approved_source(_contract(), _operation(method="POST", safe_read_only=False), "total", "", client=_client(lambda request: httpx.Response(200, request=request)))
    assert unsafe["result"] == "blocked"
    assert unsafe["classification"] == "source_operation_not_read_only"


def test_data_scenario_and_redacted_observation_are_persisted() -> None:
    contract = DBRepository.register_v3_api_contract(
        {"name": "Dashboard source", "version": "1", "source_type": "openapi", "base_url": "https://example.com", "environment": "staging", "approved": True},
        [{
            "operation_key": "dashboard-source", "method": "GET", "path": "/metrics", "expected_status_codes": [200],
            "required_fields": [], "max_latency_ms": 3000, "auth_requirement": "none", "auth_fixture_ref": "",
            "approved_role": "", "allow_unauthenticated_probe": False, "safe_read_only": True, "is_enabled": True,
        }],
    )
    source_operation_id = contract["operations"][0]["operation_id"]
    scenario = DBRepository.register_v3_data_assertion_scenario({
        "name": "Monthly spend", "route": "/dashboard", "ui_selector": "[data-kpi='spend']", "metric_label": "Spend",
        "source_operation_id": source_operation_id, "source_field": "metric.total", "source_timestamp_field": "updated_at",
        "expected_filters": {"period": "month"}, "timezone": "UTC", "freshness_window_minutes": 60,
        "tolerance": "0.01", "value_format": "currency", "approved": True,
    })
    observation = DBRepository.add_v3_data_assertion_observation({
        "scenario_id": scenario["data_assertion_scenario_id"], "job_id": None, "run_id": None, "evidence_manifest_id": None,
        "scenario_version": "dashboard-v1", "result": "pass", "classification": "matched",
        "ui_raw_value": "$1000", "source_raw_value": "1000", "normalized_ui_value": "1000", "normalized_source_value": "1000",
        "delta": "0", "filter_context": {"period": "month"}, "source_filter_context": {"period": "month"},
        "source_fresh_at": datetime.now(timezone.utc), "captured_at": datetime.now(timezone.utc),
        "source_metadata": {"authorization": "Bearer not-stored"},
    })
    assert "not-stored" not in str(observation)
    assert "[REDACTED]" in str(observation)
    stored = DBRepository.list_v3_data_assertion_observations(scenario["data_assertion_scenario_id"])
    assert stored[-1]["data_observation_id"] == observation["data_observation_id"]

    defaulted_capture = DBRepository.add_v3_data_assertion_observation({
        "scenario_id": scenario["data_assertion_scenario_id"], "job_id": None, "run_id": None,
        "evidence_manifest_id": None, "scenario_version": "dashboard-v1", "result": "blocked",
        "classification": "missing_source_data", "ui_raw_value": "", "source_raw_value": "",
        "normalized_ui_value": None, "normalized_source_value": None, "delta": None,
        "filter_context": {}, "source_filter_context": {}, "source_fresh_at": None,
        "captured_at": None, "source_metadata": {},
    })
    assert defaulted_capture["captured_at"]
