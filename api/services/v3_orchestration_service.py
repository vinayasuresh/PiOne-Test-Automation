"""Bounded V3 verification planning.

This service deliberately chooses only a pre-approved, registered recipe.  It
does not generate Python, crawl arbitrary pages, or perform mutating actions.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from api.services.v3_api_contract_service import validate_registered_operation
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


def execute_approved_scenario(scenario: dict[str, Any], registration: dict[str, Any] | None) -> dict[str, Any]:
    """Execute a single approved read-only contract operation, or state limits honestly."""
    events: list[dict[str, str]] = [{"event": "choose_action", "detail": scenario["scenario_type"]}]
    if scenario["scenario_type"] == "data_assertion":
        return {"verdict": "inconclusive", "category": "ui_capture_required", "events": events + [{"event": "conclude", "detail": "awaiting a captured dashboard value"}]}
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
