"""Build deterministic, selector-backed test plans from one scan session."""

from __future__ import annotations

import re
from typing import Any

from api.models.scan_models import ScanResponse, TestCase, TestStep


_MAX_ASSERTIONS_PER_ROUTE = 3
_NOISY_WORDS = {"the", "and", "with", "from", "that", "this", "module", "page", "test"}


def build_test_cases(scan: ScanResponse, context: dict[str, Any]) -> list[TestCase]:
    """Create safe visual-verification cases using only current scan data.

    The plan deliberately contains navigate/assert-visible steps only. A scan
    proves a selector exists; it does not authorize clicks, data entry, saves,
    downloads, or other state-changing interactions.
    """
    source_text = "\n".join((
        context.get("requirements", ""), context.get("testcases", ""), context.get("user_prompt", ""),
    ))
    cases: list[TestCase] = []
    for number, route in enumerate(scan.routes, start=1):
        if route.route_status == "NON-WORKING":
            continue
        module = route.menu_name or route.path.strip("/") or "Home"
        refs = _matching_context_lines(source_text, module, route.path)
        steps = [
            TestStep(
                step_id=f"TC_{number:03d}_S01", action="navigate", route=route.path,
                expected_result=f"{module} route loads within the scanned application scope.",
            )
        ]
        targets = _candidate_targets(route.automation_targets)
        for step_number, target in enumerate(targets, start=2):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_number:02d}", action="assert_visible",
                route=route.path, target_label=target["label"], selector=target["selector"],
                expected_result=f"{target['label']} is visible and available to the user.",
            ))
        cases.append(TestCase(
            tc_id=f"TC_{number:03d}", title=f"Verify {module} route and discovered controls",
            module=module, route=route.path, purpose=route.purpose,
            requirement_references=refs, steps=steps,
        ))
    return cases


def _candidate_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets:
        label = str(target.get("label") or target.get("text") or "").strip()
        if not label or label.casefold() in seen:
            continue
        selector = _normalize_selector(target.get("selector"), target, label)
        if not selector:
            continue
        seen.add(label.casefold())
        selected.append({"label": label, "selector": selector})
        if len(selected) >= _MAX_ASSERTIONS_PER_ROUTE:
            break
    return selected


def _normalize_selector(raw: Any, target: dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        primary = raw.get("primary") or {}
        value = str(primary.get("value") or "").strip()
        strategy = str(primary.get("strategy") or "").strip()
        if value and strategy:
            # Keep the ordered alternatives collected by the crawler.  They
            # are part of a target's identity and must survive planning so
            # execution can recover from a stale primary locator.
            fallback = [item for item in raw.get("fallback", []) if isinstance(item, str) and item.strip()]
            return {"strategy": strategy, "value": value, "fallback": fallback}
    role = str(target.get("element_role") or "").strip()
    if role:
        return {"strategy": "role", "value": f"{role}[name={label}]"}
    target_type = str(target.get("type") or "").lower()
    if target_type == "button":
        return {"strategy": "role", "value": f"button[name={label}]"}
    return {"strategy": "text", "value": label}


def _matching_context_lines(text: str, module: str, route: str) -> list[str]:
    keywords = {
        token.casefold() for token in re.split(r"[^A-Za-z0-9]+", f"{module} {route}")
        if len(token) > 2 and token.casefold() not in _NOISY_WORDS
    }
    if not keywords:
        return []
    matching = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned and any(keyword in cleaned.casefold() for keyword in keywords):
            matching.append(cleaned[:240])
        if len(matching) >= 3:
            break
    return matching
