"""Build deterministic, selector-backed test plans from one scan session."""

from __future__ import annotations

import re
from typing import Any

from api.models.scan_models import ScanResponse, TestCase, TestStep
from automation_framework.crawler.action_classifier import DESTRUCTIVE_WORDS
from automation_framework.crawler.route_health_scorer import is_non_working_route


_MAX_STEPS_PER_ROUTE = 6
_NOISY_WORDS = {"the", "and", "with", "from", "that", "this", "module", "page", "test"}


def build_test_cases(scan: ScanResponse, context: dict[str, Any]) -> list[TestCase]:
    """Create grounded safe-action and verification test cases using current scan data.

    Generated plans contain safe, discovered actions from the element registry
    (safe navigation, open menus, expand tabs/accordions, filters, sorting, search,
    add-to-cart, continue shopping, and visibility assertions).
    Destructive actions (delete, logout, reset, payment, submit) are strictly excluded.
    """
    source_text = "\n".join((
        context.get("requirements", ""), context.get("testcases", ""), context.get("user_prompt", ""),
    ))
    cases: list[TestCase] = []
    for number, route in enumerate(scan.routes, start=1):
        if is_non_working_route(route.route_status):
            continue
        canonical_route = getattr(route, "url", "") or route.path
        module = route.menu_name or route.path.strip("/") or "Home"
        refs = _matching_context_lines(source_text, module, canonical_route)
        steps = _plan_steps_for_route(route, number)
        cases.append(TestCase(
            tc_id=f"TC_{number:03d}",
            title=f"Verify {module} route and safe business workflows",
            module=module,
            route=canonical_route,
            purpose=route.purpose,
            requirement_references=refs,
            steps=steps,
        ))
    return cases


def _plan_steps_for_route(route: Any, number: int) -> list[TestStep]:
    """Derive grounded safe actions and assertions for a scanned route."""
    module = getattr(route, "menu_name", "") or getattr(route, "path", "").strip("/") or "Home"
    path = getattr(route, "path", "/")
    canonical_route = getattr(route, "url", "") or path

    steps: list[TestStep] = [
        TestStep(
            step_id=f"TC_{number:03d}_S01",
            action="navigate",
            route=canonical_route,
            target_label=module,
            expected_result=f"{module} route loads within the scanned application scope.",
            safety_classification="safe_navigation",
        )
    ]

    targets = getattr(route, "automation_targets", []) or []
    step_num = 2
    seen_labels: set[str] = set()

    for target in targets:
        if step_num > _MAX_STEPS_PER_ROUTE:
            break

        label = str(target.get("label") or target.get("text") or "").strip()
        if not label or label.casefold() in seen_labels:
            continue

        safety = (target.get("safety_classification") or "").lower()
        if safety in {"blocked_destructive", "destructive", "requires_manual_confirmation", "mutating_form"}:
            continue

        lbl_lower = label.lower()
        # Strictly reject destructive actions
        if any(re.search(rf"\b{re.escape(w)}\b", lbl_lower) for w in DESTRUCTIVE_WORDS):
            continue

        seen_labels.add(label.casefold())
        selector = _normalize_selector(target.get("selector"), target, label)
        if not selector:
            continue

        ident = target.get("element_identity") or target.get("id") or ""

        # 1. Add to cart safe business action
        if any(p in lbl_lower for p in {"add to cart", "add-to-cart", "add_to_cart", "add to basket"}):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="click",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"Add {label} to cart safely.",
                element_identity=ident,
                safety_classification="safe_action",
            ))
            step_num += 1
            if step_num <= _MAX_STEPS_PER_ROUTE:
                steps.append(TestStep(
                    step_id=f"TC_{number:03d}_S{step_num:02d}",
                    action="assert_cart_count",
                    route=canonical_route,
                    target_label="Shopping cart count",
                    selector={"strategy": "css", "value": ".shopping_cart_badge", "fallback": [".cart_badge", "[data-test='shopping-cart-badge']"]},
                    expected_result="Shopping cart item count updates.",
                    element_identity=ident,
                    safety_classification="safe_assertion",
                ))
                step_num += 1

        # 2. Continue shopping safe navigation
        elif any(p in lbl_lower for p in {"continue shopping", "continue-shopping"}):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="click",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"Click {label} to return to inventory.",
                element_identity=ident,
                safety_classification="safe_navigation",
            ))
            step_num += 1
            if step_num <= _MAX_STEPS_PER_ROUTE:
                steps.append(TestStep(
                    step_id=f"TC_{number:03d}_S{step_num:02d}",
                    action="assert_url",
                    route="/inventory.html",
                    target_label="Inventory Catalog",
                    expected_result="Returned to inventory catalog.",
                    element_identity=ident,
                    safety_classification="safe_navigation",
                ))
                step_num += 1

        # 3. View details / product details link
        elif any(p in lbl_lower for p in {"view details", "details for"}):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="click",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"Click {label} to view product details.",
                element_identity=ident,
                safety_classification="safe_navigation",
            ))
            step_num += 1

        # 4. Sorting dropdown / selection
        elif any(p in lbl_lower for p in {"sort", "order by"}) or target.get("component_tag") == "select" or target.get("type") == "select":
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="select",
                route=canonical_route,
                target_label=label,
                selector=selector,
                value="lohi",
                expected_result=f"Change product sorting order using {label}.",
                element_identity=ident,
                safety_classification="safe_sort",
            ))
            step_num += 1

        # 5. Search / Filter input
        elif target.get("category") == "inputs" or any(p in lbl_lower for p in {"search", "find", "filter"}):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="type",
                route=canonical_route,
                target_label=label,
                selector=selector,
                value="test",
                expected_result=f"Enter search query into {label}.",
                element_identity=ident,
                safety_classification="safe_search",
            ))
            step_num += 1

        # 6. Tab / Accordion / Safe expand / Menu
        elif any(p in lbl_lower for p in {"tab", "accordion", "menu", "overview", "metrics"}) or target.get("element_role") in {"tab", "switch"}:
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="click",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"Toggle or expand {label}.",
                element_identity=ident,
                safety_classification="safe_expand",
            ))
            step_num += 1

        # 7. Pagination
        elif target.get("is_pagination") or any(p in lbl_lower for p in {"next", "prev", "page"}):
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="click",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"Navigate with pagination control {label}.",
                element_identity=ident,
                safety_classification="safe_pagination",
            ))
            step_num += 1

        # 8. Standard visibility verification
        else:
            steps.append(TestStep(
                step_id=f"TC_{number:03d}_S{step_num:02d}",
                action="assert_visible",
                route=canonical_route,
                target_label=label,
                selector=selector,
                expected_result=f"{label} is visible and available to the user.",
                element_identity=ident,
                safety_classification=safety or "safe_navigation",
            ))
            step_num += 1

    return steps


def _normalize_selector(raw: Any, target: dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        primary = raw.get("primary") or {}
        value = str(primary.get("value") or "").strip()
        strategy = str(primary.get("strategy") or "").strip()
        if value and strategy:
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
