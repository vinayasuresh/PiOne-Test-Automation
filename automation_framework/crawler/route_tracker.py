from urllib.parse import urlparse, urlunparse

from automation_framework.crawler.route_graph import RouteGraph, normalize_route_url


class RouteTracker:
    """Tracks visited routes and maintains the traversal RouteGraph.

    Normalization rules:
    - drop query params and fragments
    - drop trailing slash
    - lowercase scheme and netloc while preserving case-sensitive paths
    """

    def __init__(self, graph: RouteGraph | None = None) -> None:
        self.graph = graph or RouteGraph()

    def normalize_url(self, url: str) -> str:
        return normalize_route_url(url)

    def is_visited(self, url: str) -> bool:
        return self.graph.is_visited(url)

    def mark_visited(self, url: str, status: str = "WORKING_ROUTE", score: int = 100) -> None:
        self.graph.mark_visited(url, status=status, score=score)

    def unmark_visited(self, url: str) -> None:
        self.graph.unmark_visited(url)

    def get_visited_routes(self) -> set[str]:
        return self.graph.get_visited_routes()

