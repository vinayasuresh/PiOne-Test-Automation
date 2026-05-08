from urllib.parse import urljoin, urlparse


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


def is_internal_url(url: str, base_url: str) -> bool:
    base_domain = urlparse(base_url).netloc.lower()
    target_domain = urlparse(urljoin(base_url, url)).netloc.lower()

    return target_domain == base_domain


def is_static_asset(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in STATIC_ASSET_EXTENSIONS)


def is_valid_url(url: str, base_url: str) -> bool:
    if not url:
        return False

    parsed_url = urlparse(url.strip())

    if parsed_url.scheme.lower() in IGNORED_SCHEMES:
        return False

    if is_static_asset(url):
        return False

    return is_internal_url(url, base_url)
