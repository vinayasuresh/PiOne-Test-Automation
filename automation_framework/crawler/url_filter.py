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


def is_static_asset(url: str) -> bool:
    path = unquote(urlparse(url).path).lower().rstrip("/")
    return any(path.endswith(extension) for extension in STATIC_ASSET_EXTENSIONS)


def is_valid_url(url: str, base_url: str) -> bool:
    if not url:
        return False

    resolved_url = urljoin(base_url, url.strip())
    parsed_url = urlparse(resolved_url)

    if parsed_url.scheme.lower() in IGNORED_SCHEMES:
        return False

    if is_static_asset(resolved_url):
        return False

    if is_non_application_path(resolved_url):
        return False

    return is_internal_url(resolved_url, base_url)
