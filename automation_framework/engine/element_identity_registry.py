"""ElementIdentityRegistry: Computes and enforces deterministic element identities.

Uniqueness rule:
    route + xpath + role + normalized_label + index

Guarantees:
- Never deduplicates by label alone.
- Preserves alternative/fallback selectors.
- Stable across repeated scans and re-renders.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_label(label: str) -> str:
    """Normalize label text by collapsing whitespace and lowercasing."""
    if not label:
        return ""
    cleaned = re.sub(r"\s+", " ", label.strip())
    return cleaned.lower()


def compute_element_identity(
    route: str,
    xpath: str,
    role: str,
    label: str,
    index: int = 1,
) -> str:
    """Compute the canonical composite identity for a UI element."""
    # Normalize route URL
    parsed = urlparse(route or "")
    norm_route = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        "", "", "",
    ))

    norm_xpath = (xpath or "").strip()
    norm_role = (role or "").strip().lower()
    norm_label = normalize_label(label)
    norm_index = int(index or 1)

    return f"{norm_route}|{norm_xpath}|{norm_role}|{norm_label}|{norm_index}"


class ElementIdentityRegistry:
    """Maintains deduplicated element records keyed by the composite identity."""

    def __init__(self) -> None:
        self._registry: dict[str, dict[str, Any]] = {}
        self._route_elements: dict[str, list[str]] = {}

    def register(
        self,
        route: str,
        element: dict[str, Any],
    ) -> tuple[str, bool]:
        """Register an element. Returns (identity, is_new)."""
        xpath = element.get("element_xpath") or element.get("xpath") or ""
        role = element.get("element_role") or element.get("role") or ""
        label = element.get("label") or element.get("text") or ""
        index = int(element.get("element_index") or 1)

        identity = compute_element_identity(route, xpath, role, label, index)
        element["element_identity"] = identity
        element["id"] = identity
        element["element_id"] = identity
        element["route"] = route
        element["module_id"] = element.get("module_id") or f"route:{route}"
        element["ui_state_id"] = element.get("ui_state_id") or "state_base"
        element["hierarchy_path"] = element.get("hierarchy_path") or element.get("page") or element.get("category") or "default"
        element["role"] = role or element.get("type", "")
        element["element_role"] = element["role"]
        element["label"] = label
        element["xpath"] = xpath
        element["element_xpath"] = xpath
        element["discovery_mechanism"] = element.get("discovery_mechanism") or "passive_dom"

        if "quality_score" not in element:
            from automation_framework.crawler.ui_state_fingerprint import compute_selector_quality_score
            element["quality_score"] = compute_selector_quality_score(element.get("selector"))

        if "safety_classification" not in element:
            from automation_framework.crawler.action_classifier import classify_action
            element["safety_classification"] = classify_action(element)

        if identity in self._registry:
            # Merge fallback selectors if new ones discovered
            existing = self._registry[identity]
            new_fallbacks = element.get("selector", {}).get("fallback", [])
            if new_fallbacks and isinstance(existing.get("selector"), dict):
                curr_fallbacks = existing["selector"].setdefault("fallback", [])
                for fb in new_fallbacks:
                    if fb not in curr_fallbacks:
                        curr_fallbacks.append(fb)
            return identity, False

        self._registry[identity] = element
        self._route_elements.setdefault(route, []).append(identity)
        return identity, True

    def has(self, identity: str) -> bool:
        return identity in self._registry

    def get(self, identity: str) -> dict[str, Any] | None:
        return self._registry.get(identity)

    def get_elements_for_route(self, route: str) -> list[dict[str, Any]]:
        identities = self._route_elements.get(route, [])
        return [self._registry[ident] for ident in identities if ident in self._registry]

    def get_all_elements(self) -> list[dict[str, Any]]:
        return list(self._registry.values())

    def unregister_route(self, route: str) -> None:
        """Remove all elements registered for a route."""
        identities = self._route_elements.pop(route, [])
        for ident in identities:
            self._registry.pop(ident, None)

