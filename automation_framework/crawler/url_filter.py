import posixpath
from urllib.parse import unquote, urljoin, urlparse


STATIC_ASSET_EXTENSIONS = {
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".pdf",
    ".zip",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}

IGNORED_SCHEMES = {"mailto", "javascript", "tel"}
NON_APPLICATION_PATHS = {
    "assets", "static", "cdn", "fonts", "images", "media", "vendor",
}


def _normalized_path(path: str) -> str:
    """Normalize URL paths without allowing prefix lookalikes (e.g. /app2)."""
    decoded_path = unquote(path)
    normalized = posixpath.normpath("/" + decoded_path.lstrip("/"))
    return normalized.rstrip("/") or "/"


def is_within_app_path(url: str, base_url: str) -> bool:
    """Require a route to stay under the path explicitly supplied by the user.

    A root URL scopes to the host root. A URL such as ``/portal`` scopes to
    ``/portal`` and its descendants; the sibling ``/portal-admin`` is outside.
    """
    base_path = _normalized_path(urlparse(base_url).path)
    target_path = _normalized_path(urlparse(urljoin(base_url, url)).path)
    if base_path == "/":
        return True
    return target_path == base_path or target_path.startswith(base_path + "/")


def is_non_application_path(url: str) -> bool:
    segments = {part.lower() for part in unquote(urlparse(url).path).split("/") if part}
    return bool(segments & NON_APPLICATION_PATHS)


def is_internal_url(url: str, base_url: str) -> bool:
    base = urlparse(base_url)
    target = urlparse(urljoin(base_url, url))
    same_origin = (
        target.scheme.lower() == base.scheme.lower()
        and target.netloc.lower() == base.netloc.lower()
    )
    return same_origin and is_within_app_path(target.geturl(), base_url)


def install_document_scope_guard(page, base_url: str, event_callback=None) -> None:
    """Block top-level document navigations outside the requested app scope.

    Install this before the first ``page.goto`` (including login navigation),
    otherwise an out-of-scope redirect can load before the crawler is guarded.
    Subresources and API calls are deliberately left alone; this guard controls
    which documents the scanner is allowed to visit.
    """
    def guard(route) -> None:
        request = route.request
        if request.is_navigation_request() and request.frame == page.main_frame:
            if not is_valid_url(request.url, base_url):
                if event_callback:
                    try:
                        event_callback("route_failed", {
                            "route": request.url,
                            "reason": "navigation_blocked_outside_app_scope",
                        })
                    except Exception:
                        pass
                route.abort()
                return
        route.continue_()

    page.route("**/*", guard)


def is_static_asset(url: str) -> bool:
    path = unquote(urlparse(url).path).lower().rstrip("/")
    return any(path.endswith(extension) for extension in STATIC_ASSET_EXTENSIONS)


class AppBoundaryGuard:
    """Enforces target application boundaries for crawler discovery and navigation.

    Guarantees:
    - Same origin (scheme + host + port)
    - Within allowed base path / namespace
    - No external domains, CDN pages, static asset bundles
    - No logout / session termination URLs
    - Respects explicitly configured allowed_paths and blocked_paths
    """

    LOGOUT_PATTERNS = {
        "logout", "signout", "sign-out", "log-out", "log_out", "exit", "disconnect",
    }

    def __init__(
        self,
        base_url: str,
        allowed_paths: list[str] | None = None,
        blocked_paths: list[str] | None = None,
    ) -> None:
        self.base_url = base_url.strip()
        self.allowed_paths = [_normalized_path(p) for p in (allowed_paths or [])]
        self.blocked_paths = [_normalized_path(p) for p in (blocked_paths or [])]

    def is_allowed(self, url: str) -> bool:
        if not url:
            return False

        resolved_url = urljoin(self.base_url, url.strip())
        parsed = urlparse(resolved_url)

        if parsed.scheme.lower() in IGNORED_SCHEMES:
            return False

        if is_static_asset(resolved_url):
            return False

        if is_non_application_path(resolved_url):
            return False

        if not is_internal_url(resolved_url, self.base_url):
            return False

        # Reject logout / signout endpoints
        segments = {part.lower() for part in unquote(parsed.path).split("/") if part}
        if bool(segments & self.LOGOUT_PATTERNS):
            return False

        target_path = _normalized_path(parsed.path)

        # Reject explicitly blocked paths
        for blocked in self.blocked_paths:
            if target_path == blocked or target_path.startswith(blocked + "/"):
                return False

        # If allowed_paths are specified, url must match at least one
        if self.allowed_paths:
            in_allowed = any(
                target_path == allowed or target_path.startswith(allowed + "/")
                for allowed in self.allowed_paths
            )
            if not in_allowed:
                return False

        return True


def is_valid_url(url: str, base_url: str) -> bool:
    if not url:
        return False
    return AppBoundaryGuard(base_url).is_allowed(url)


def is_url_allowed(url: str, base_url: str) -> bool:
    return is_valid_url(url, base_url)

