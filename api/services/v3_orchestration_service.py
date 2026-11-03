"""Bounded V3 verification planning.

This service deliberately chooses only a pre-approved, registered recipe.  It
does not generate Python, crawl arbitrary pages, or perform mutating actions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from api.services.v3_api_contract_service import validate_registered_operation
from api.services.v3_data_assertion_service import (
    capture_dashboard_ui_value,
    compare_dashboard_data,
    read_approved_source,
)
from api.utils.security_guard import validate_target_url


def _origin(url: str) -> str:
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    return f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}{port}"


def plan_assurance_job(job: dict[str, Any], incident: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Produce a transparent policy decision before one bounded execution."""
    events: list[dict[str, str]] = [{"event": "observe", "detail": "loaded durable incident and approved scenario"}]
    decision: dict[str, Any] = {"scenario_id": scenario["scenario_id"], "max_actions": scenario["max_actions"], "events": events}
    if not scenario.get("is_approved"):
        return {"ready": False, "verdict": "blocked", "category": "scenario_not_approved", "decision": decision}
    if job.get("mode") == "security_only":
        return {"ready": False, "verdict": "blocked", "category": "security_mode_not_in_v3_orchestrator", "decision": decision}
    if scenario.get("authorized_role") and scenario["authorized_role"] != job.get("authorized_role", ""):
        return {"ready": False, "verdict": "blocked", "category": "role_scope_mismatch", "decision": decision}
    try:
        target = validate_target_url(job.get("target", ""))
    except ValueError as exc:
        decision["scope_error"] = str(exc)
        return {"ready": False, "verdict": "blocked", "category": "target_scope_invalid", "decision": decision}
    if _origin(target) != _origin(scenario["target_origin"]):
        return {"ready": False, "verdict": "blocked", "category": "target_origin_mismatch", "decision": decision}
    events.append({"event": "policy_check", "detail": "role, target origin, and approved scenario accepted"})
    return {"ready": True, "verdict": "pending", "category": "", "decision": decision}


def execute_approved_scenario(
    scenario: dict[str, Any],
    registration: dict[str, Any] | None,
    *,
    data_assertion_scenario: dict[str, Any] | None = None,
    auth_storage_state: str | Path | None = None,
    page: Any | None = None,
    client: httpx.Client | None = None,
    is_mock: bool = False,
) -> dict[str, Any]:
    """Execute a single approved read-only contract operation or dashboard data validation."""
    events: list[dict[str, str]] = [{"event": "choose_action", "detail": scenario["scenario_type"]}]

    if scenario["scenario_type"] == "data_assertion":
        if not data_assertion_scenario:
            return {
                "verdict": "inconclusive",
                "category": "ui_capture_required",
                "events": events + [{"event": "conclude", "detail": "awaiting a captured dashboard value"}],
            }

        # Step 1: Headless browser capture of UI value & screenshot
        ui_capture = capture_dashboard_ui_value(
            scenario["target_origin"],
            data_assertion_scenario["route"],
            data_assertion_scenario["ui_selector"],
            filter_context=data_assertion_scenario.get("expected_filters"),
            value_format=data_assertion_scenario.get("value_format", "number"),
            auth_storage_state=auth_storage_state,
            page=page,
            is_mock=is_mock,
        )

        if ui_capture.get("result") != "ok":
            classification = ui_capture.get("classification", "ui_capture_failed")
            if classification in {"target_timeout", "target_unavailable", "target_scope_invalid", "auth_expired"}:
                verdict = "blocked"
            else:
                verdict = "reproduced"
            events.extend([
                {"event": "capture_ui", "detail": f"UI capture {classification}: {ui_capture.get('error', '')}"},
                {"event": "verify", "detail": classification},
                {"event": "conclude", "detail": verdict},
            ])
            return {
                "verdict": verdict,
                "category": classification,
                "events": events,
                "result": ui_capture,
                "screenshot_storage_location": ui_capture.get("screenshot_storage_location"),
                "screenshot_checksum": ui_capture.get("screenshot_checksum"),
                "ui_capture": ui_capture,
            }

        events.append({"event": "capture_ui", "detail": f"captured UI value '{ui_capture.get('ui_value', '')}' from {data_assertion_scenario['route']}"})

        # Step 2: Fetch source data from registered read-only API operation
        if not registration or not registration.get("contract"):
            return {"verdict": "blocked", "category": "approved_operation_unavailable", "events": events}

        source_reading = read_approved_source(
            registration["contract"],
            registration["operation"],
            data_assertion_scenario["source_field"],
            data_assertion_scenario.get("source_timestamp_field", ""),
            client=client,
        )

        # Step 3: Compare dashboard data - UI pass must never mask failed source data assertion
        comparison = compare_dashboard_data(data_assertion_scenario, ui_capture, source_reading)
        classification = comparison.get("classification", "unknown")

        if comparison.get("result") == "pass":
            verdict = "not_reproduced"
        elif comparison.get("result") == "blocked":
            verdict = "blocked"
        elif classification in {"target_timeout", "target_unavailable", "target_scope_invalid", "source_unavailable", "source_authorization_failure", "auth_expired"}:
            verdict = "blocked"
        else:
            verdict = "reproduced"

        events.extend([
            {"event": "read_source", "detail": f"source returned '{source_reading.get('source_value', '')}'"},
            {"event": "verify", "detail": classification},
            {"event": "conclude", "detail": verdict},
        ])
        return {
            "verdict": verdict,
            "category": classification,
            "events": events,
            "result": comparison,
            "screenshot_storage_location": ui_capture.get("screenshot_storage_location"),
            "screenshot_checksum": ui_capture.get("screenshot_checksum"),
            "ui_capture": ui_capture,
            "source_reading": source_reading,
        }

    # API contract verification
    if not registration or not registration.get("contract"):
        return {"verdict": "blocked", "category": "approved_operation_unavailable", "events": events}
    result = validate_registered_operation(registration["contract"], registration["operation"])
    classification = result.get("classification", "unknown")
    if result.get("result") == "pass":
        verdict = "not_reproduced"
    elif classification in {"target_timeout", "target_unavailable", "target_scope_invalid", "contract_not_approved", "unsafe_or_disabled_operation"}:
        verdict = "blocked"
    else:
        verdict = "reproduced"
    events.extend([
        {"event": "execute", "detail": f"approved read-only operation returned {result.get('status_code', 'no response')}"},
        {"event": "verify", "detail": classification},
        {"event": "conclude", "detail": verdict},
    ])
    return {"verdict": verdict, "category": classification, "events": events, "result": result}
