"""RouteGraph: Maintains a directed traversal graph of discovered application routes.

Tracks:
- Route URL (normalized)
- Parent route
- Navigation cause (landing, menu, content_link, router_link, trigger, etc.)
- Return strategy (navigate_url, browser_back, close_overlay)
- Route status (WORKING_ROUTE, PARTIAL_ROUTE, NON_WORKING_ROUTE, PENDING)
- Visited state and health score
- Child routes and probed actions
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_route_url(url: str) -> str:
    """Normalize URLs by stripping fragments, query parameters, and trailing slashes."""
    if not url:
        return ""
    parsed = urlparse(url)
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        normalized_path,
        "",
        "",
        "",
    ))


@dataclass
class RouteNode:
    route: str
    parent_route: str | None = None
    navigation_cause: str = "landing"  # landing | menu | content_link | router_link | expand_trigger | manual
    return_strategy: str = "navigate_url"  # navigate_url | browser_back | close_overlay
    status: str = "PENDING"  # PENDING | WORKING_ROUTE | PARTIAL_ROUTE | NON_WORKING_ROUTE
    visited: bool = False
    score: int = 0
    menu_name: str = ""
    depth: int = 0
    actions_probed: list[dict[str, Any]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    ui_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    state_transitions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteNode:
        valid_keys = {
            "route", "parent_route", "navigation_cause", "return_strategy",
            "status", "visited", "score", "menu_name", "depth",
            "actions_probed", "children", "ui_states", "state_transitions",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class RouteGraph:
    """Directed graph of application routes discovered during scanning."""

    def __init__(self) -> None:
        self.nodes: dict[str, RouteNode] = {}

    def add_route(
        self,
        route: str,
        parent_route: str | None = None,
        navigation_cause: str = "content_link",
        return_strategy: str = "navigate_url",
        menu_name: str = "",
    ) -> RouteNode:
        normalized = normalize_route_url(route)
        norm_parent = normalize_route_url(parent_route) if parent_route else None

        if normalized in self.nodes:
            node = self.nodes[normalized]
            if not node.menu_name and menu_name:
                node.menu_name = menu_name
            return node

        depth = (self.nodes[norm_parent].depth + 1) if norm_parent and norm_parent in self.nodes else 0
        node = RouteNode(
            route=normalized,
            parent_route=norm_parent,
            navigation_cause=navigation_cause,
            return_strategy=return_strategy,
            status="PENDING",
            visited=False,
            score=0,
            menu_name=menu_name,
            depth=depth,
        )
        self.nodes[normalized] = node

        if norm_parent and norm_parent in self.nodes:
            if normalized not in self.nodes[norm_parent].children:
                self.nodes[norm_parent].children.append(normalized)

        return node

    def mark_visited(
        self,
        route: str,
        status: str = "WORKING_ROUTE",
        score: int = 100,
    ) -> None:
        normalized = normalize_route_url(route)
        if normalized not in self.nodes:
            self.add_route(normalized)
        node = self.nodes[normalized]
        node.visited = True
        node.status = status
        node.score = score

    def is_visited(self, url: str) -> bool:
        normalized = normalize_route_url(url)
        node = self.nodes.get(normalized)
        return bool(node and node.visited)

    def unmark_visited(self, url: str) -> None:
        normalized = normalize_route_url(url)
        if normalized in self.nodes:
            self.nodes[normalized].visited = False
            self.nodes[normalized].status = "PENDING"

    def get_node(self, route: str) -> RouteNode | None:
        return self.nodes.get(normalize_route_url(route))

    def get_visited_routes(self) -> set[str]:
        return {route for route, node in self.nodes.items() if node.visited}

    def get_pending_routes(self) -> list[RouteNode]:
        return [node for node in self.nodes.values() if not node.visited]

    def record_action(self, route: str, action: dict[str, Any]) -> None:
        normalized = normalize_route_url(route)
        if normalized in self.nodes:
            self.nodes[normalized].actions_probed.append(action)

    def record_ui_state(self, route: str, ui_state: dict[str, Any]) -> None:
        normalized = normalize_route_url(route)
        if normalized not in self.nodes:
            self.add_route(normalized)
        state_id = ui_state.get("ui_state_id") or "state_base"
        self.nodes[normalized].ui_states[state_id] = ui_state

    def record_state_transition(
        self,
        route: str,
        from_state: str,
        to_state: str,
        action: dict[str, Any],
    ) -> None:
        normalized = normalize_route_url(route)
        if normalized in self.nodes:
            self.nodes[normalized].state_transitions.append({
                "from_state": from_state,
                "to_state": to_state,
                "action": action,
            })

    def to_dict(self) -> dict[str, Any]:
        return {route: node.to_dict() for route, node in self.nodes.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteGraph:
        graph = cls()
        for route, node_dict in data.items():
            graph.nodes[route] = RouteNode.from_dict(node_dict)
        return graph
