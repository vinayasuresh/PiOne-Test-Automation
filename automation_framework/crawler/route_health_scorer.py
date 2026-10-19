"""RouteHealthScorer: Evaluates health and classifies routes (WORKING, PARTIAL, NON-WORKING).

Scores 0–100 based on:
- Page load success
- DOM stability
- Route redirect consistency
- API/network evidence
- Interaction success rate
- Discovered element coverage
"""

from __future__ import annotations

from typing import Any


class RouteStatus:
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    NON_WORKING = "NON-WORKING"

    # Aliases
    WORKING_ROUTE = "WORKING"
    PARTIAL_ROUTE = "PARTIAL"
    NON_WORKING_ROUTE = "NON-WORKING"


def compute_normalized_score(
    components: dict[str, int],
    api_present: bool,
    interaction_attempted: bool,
) -> tuple[int, int]:
    """Normalize weighted route signals while excluding unavailable signals.

    Missing API traffic or lack of attempted interactions are often legitimate.
    They do not penalize the score unless an actual failure was recorded.
    """
    available_weight = 25 + 20 + 20  # DOM, redirect, and element signals
    if api_present:
        available_weight += 20
    if interaction_attempted:
        available_weight += 15
    earned = sum(components.values())
    score = round(earned * 100 / max(1, available_weight))
    return min(100, max(0, score)), available_weight


def score_route(
    expected_url: str,
    actual_url: str,
    stable: bool,
    target_count: int,
    api_responses: list[dict[str, Any]] | None = None,
    interaction_stats: dict[str, int] | None = None,
    page_load_success: bool = True,
    document_status: int = 200,
    scan_error: bool = False,
    is_login_page: bool = False,
    visible_control_count: int = 0,
    dom_element_count: int = 0,
    readiness_timeout_reached: bool = False,
    console_errors: list[str] | None = None,
    redirect_chain: list[str] | None = None,
) -> dict[str, Any]:
    """Score a scanned route and classify its operational health status."""
    responses = api_responses or []
    failed_api = [item for item in responses if int(item.get("status", 0)) >= 400]
    api_present = bool(responses)
    redirected = actual_url.rstrip("/") != expected_url.rstrip("/")

    interaction = interaction_stats or {"attempted": 0, "succeeded": 0}
    interaction_attempted = bool(interaction.get("attempted", 0) > 0)
    interaction_rate = (
        interaction["succeeded"] / interaction["attempted"]
        if interaction_attempted else 0.0
    )

    components = {
        "dom_stability": 25 if stable else 0,
        "api_response": 20 if (api_present and not failed_api) else (8 if api_present else 0),
        "redirect_consistency": 20 if not redirected else 0,
        "interaction_success": round(15 * interaction_rate),
        "element_detection": 20 if target_count >= 5 else (12 if target_count > 0 else 0),
    }

    score, available_weight = compute_normalized_score(
        components, api_present, interaction_attempted
    )

    diagnostic_reason = ""
    # Rule D: zero extracted elements on page with visible controls MUST be PARTIAL or NON_WORKING, score < 70
    if target_count == 0 and visible_control_count > 0:
        diagnostic_reason = "zero_extracted_elements_with_visible_controls"
        score = min(score, 65)

    if readiness_timeout_reached:
        diagnostic_reason = diagnostic_reason or "readiness_timeout_reached"
        score = min(score, 65)

    if scan_error or not stable or not page_load_success or is_login_page or score < 40:
        status = RouteStatus.NON_WORKING
    elif score < 70 or failed_api or redirected or target_count == 0 or readiness_timeout_reached or bool(diagnostic_reason):
        status = RouteStatus.PARTIAL
    else:
        status = RouteStatus.WORKING

    chain = redirect_chain or ([expected_url, actual_url] if redirected else [actual_url])

    return {
        "status": status,
        "score": score,
        "diagnostic_reason": diagnostic_reason,
        "score_components": components,
        "score_available_weight": available_weight,
        "expected_url": expected_url,
        "actual_url": actual_url,
        "final_url": actual_url,
        "redirect_chain": chain,
        "redirected": redirected,
        "dom_stable": stable,
        "dom_element_count": dom_element_count,
        "visible_element_count": visible_control_count,
        "readiness_timeout_reached": readiness_timeout_reached,
        "console_errors": console_errors or [],
        "page_load_success": page_load_success,
        "document_status": document_status,
        "api_response_present": api_present,
        "failed_api_responses": len(failed_api),
        "failed_network_responses": len(failed_api),
        "target_count": target_count,
        "interaction_success_rate": round(interaction_rate, 3),
        "interaction_attempted": interaction_attempted,
    }
