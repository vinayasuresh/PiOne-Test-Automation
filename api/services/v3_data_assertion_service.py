"""Bounded dashboard-data validation using registered, read-only API sources."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any

import httpx

from api.services.v3_api_contract_service import _get_nested_value, build_operation_url
from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_value(value: Any, value_format: str) -> Decimal:
    """Normalize visible/source scalar values without locale-dependent float math."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("value is blank")
    cleaned = raw.replace(",", "").replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
    percentage = cleaned.endswith("%")
    if percentage:
        cleaned = cleaned[:-1].strip()
    try:
        normalized = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("value is not numeric") from exc
    # 98% and API 0.98 express the same normalized percentage.  Plain 98 from
    # a percentage-configured API is treated as 98%, avoiding hidden rounding.
    if value_format == "percentage" and (percentage or normalized > 1):
        normalized = normalized / Decimal("100")
    return normalized


def _blocked(classification: str, *, source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return redact_sensitive_data({
        "result": "blocked",
        "classification": classification,
        "source_value": None,
        "source_fresh_at": None,
        "source_metadata": source_metadata or {"reason": classification},
    })


def read_approved_source(
    contract: dict[str, Any],
    operation: dict[str, Any],
    source_field: str,
    source_timestamp_field: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch a value from one pre-registered read-only operation only."""
    if not contract.get("is_approved"):
        return _blocked("source_contract_not_approved")
    if not operation.get("is_enabled") or not operation.get("safe_read_only") or operation.get("method", "").upper() not in {"GET", "HEAD", "OPTIONS"}:
        return _blocked("source_operation_not_read_only")
    if operation.get("auth_requirement") == "fixture":
        # Phase 3 deliberately does not retrieve authentication secrets. The
        # later policy/orchestration phase supplies approved isolated fixtures.
        return _blocked("source_auth_fixture_not_available")
    try:
        base_url = validate_target_url(contract["base_url"], is_mock=client is not None)
        target_url = build_operation_url(base_url, operation["path"])
    except ValueError as exc:
        return _blocked("source_scope_invalid", source_metadata={"error": str(exc)})

    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)
    try:
        started = perf_counter()
        response = client.request(operation["method"], target_url, headers={"Accept": "application/json"})
        latency_ms = round((perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        return _blocked("source_timeout")
    except httpx.HTTPError as exc:
        return _blocked("source_unavailable", source_metadata={"error": str(exc)})
    finally:
        if owns_client:
            client.close()

    if response.status_code not in operation.get("expected_status_codes", []):
        classification = "source_authorization_failure" if response.status_code in {401, 403} else "source_unexpected_status"
        return _blocked(classification, source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    try:
        body = response.json()
    except ValueError:
        return _blocked("source_not_json", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    present, source_value = _get_nested_value(body, source_field)
    if not present or source_value is None:
        return _blocked("missing_source_data", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    fresh_at = None
    if source_timestamp_field:
        timestamp_present, timestamp_value = _get_nested_value(body, source_timestamp_field)
        if not timestamp_present or not parse_timestamp(timestamp_value):
            return _blocked("missing_source_freshness", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
        fresh_at = parse_timestamp(timestamp_value)
    return redact_sensitive_data({
        "result": "ok",
        "classification": "source_read",
        "source_value": str(source_value),
        "source_fresh_at": fresh_at.isoformat() if fresh_at else None,
        "source_metadata": {"status_code": response.status_code, "latency_ms": latency_ms, "path": operation["path"]},
    })


def compare_dashboard_data(
    scenario: dict[str, Any],
    evaluation: dict[str, Any],
    source_reading: dict[str, Any],
) -> dict[str, Any]:
    """Classify the comparison without treating data/infrastructure gaps as UI defects."""
    # The redactor deliberately handles untrusted mapping content, but a
    # Pydantic request may carry a datetime. Preserve that typed timestamp so
    # freshness is evaluated against the user-supplied capture time rather
    # than silently falling back to "now".
    safe_evaluation = redact_sensitive_data(evaluation)
    safe_evaluation["captured_at"] = evaluation.get("captured_at")
    source_metadata = source_reading.get("source_metadata") or {}
    base = {
        "ui_raw_value": safe_evaluation.get("ui_value", ""),
        "source_raw_value": source_reading.get("source_value") or "",
        "filter_context": safe_evaluation.get("filter_context") or {},
        "source_filter_context": safe_evaluation.get("source_filter_context") or {},
        "source_fresh_at": source_reading.get("source_fresh_at"),
        "source_metadata": source_metadata,
    }
    if source_reading.get("result") != "ok":
        return {"result": "blocked", "classification": source_reading.get("classification", "source_unavailable"), **base}
    if base["filter_context"] != (scenario.get("expected_filters") or {}) or base["source_filter_context"] != (scenario.get("expected_filters") or {}):
        return {"result": "fail", "classification": "filter_mismatch", **base}
    source_fresh_at = parse_timestamp(source_reading.get("source_fresh_at"))
    captured_at = parse_timestamp(safe_evaluation.get("captured_at")) or datetime.now(timezone.utc)
    if source_fresh_at and (captured_at - source_fresh_at).total_seconds() > int(scenario["freshness_window_minutes"]) * 60:
        return {"result": "fail", "classification": "stale_source_data", **base}
    try:
        ui_value = normalize_value(base["ui_raw_value"], scenario["value_format"])
    except ValueError:
        return {"result": "blocked", "classification": "missing_ui_data", **base}
    try:
        source_value = normalize_value(base["source_raw_value"], scenario["value_format"])
    except ValueError:
        return {"result": "blocked", "classification": "missing_source_data", **base}
    tolerance = Decimal(str(scenario["tolerance"]))
    delta = abs(ui_value - source_value)
    classification = "matched" if delta == 0 else "rounding_within_tolerance" if delta <= tolerance else "visual_source_mismatch"
    return {
        "result": "pass" if delta <= tolerance else "fail",
        "classification": classification,
        "normalized_ui_value": str(ui_value),
        "normalized_source_value": str(source_value),
        "delta": str(delta),
        **base,
    }
