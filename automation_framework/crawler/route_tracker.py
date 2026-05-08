from urllib.parse import urlparse, urlunparse


class RouteTracker:
    """Tracks visited routes after normalizing URLs.

    Normalization rules (per architectural fix):
    - drop query params and fragments
    - drop trailing slash
    - lowercase scheme, netloc, and path
    """

    def __init__(self) -> None:
        self._visited_routes: set[str] = set()

    def normalize_url(self, url: str) -> str:
        parsed_url = urlparse(url)
        normalized_path = parsed_url.path.rstrip("/").lower() or "/"

        return urlunparse(
            (
                parsed_url.scheme.lower(),
                parsed_url.netloc.lower(),
                normalized_path,
                "",
                "",
                "",
            )
        )

    def is_visited(self, url: str) -> bool:
        return self.normalize_url(url) in self._visited_routes

    def mark_visited(self, url: str) -> None:
        self._visited_routes.add(self.normalize_url(url))

    def get_visited_routes(self) -> set[str]:
        return self._visited_routes.copy()
