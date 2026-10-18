"""security_guard.py: SSRF protection, target URL boundary enforcement, and request security.

Guarantees:
- Rejects loopback (127.0.0.0/8, ::1)
- Rejects RFC 1918 private IPv4 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Rejects RFC 4193 unique local IPv6 (fc00::/7)
- Rejects link-local IPv4/IPv6 (169.254.0.0/16, fe80::/10) including cloud metadata (169.254.169.254)
- Rejects localhost, *.localhost, and unsupported URL schemes (file, data, ftp, etc.)
- Supports testing bypass strictly via explicit allow_local=True or ALLOW_LOCAL_TARGETS=1
- Validates redirect targets and document boundaries
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse, urlunparse

BLOCKED_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "metadata.google.internal",
    "instance-data",
}

BLOCKED_SCHEMES = {
    "file", "data", "ftp", "gopher", "dict", "sftp", "tftp", "ldap", "ldaps", "javascript",
}


def is_local_allowed() -> bool:
    """Check if local addresses are permitted by environment (e.g. for test suites)."""
    val = os.getenv("ALLOW_LOCAL_TARGETS", "").strip().lower()
    return val in {"1", "true", "yes"}


def validate_target_url(url: str, allow_local: bool | None = None) -> str:
    """Validate target URL for scheme, hostname, and SSRF restrictions.
    
    Returns the normalized URL or raises ValueError.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL is required and must be a string")

    raw_url = url.strip()
    parsed = urlparse(raw_url)

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"Invalid URL scheme '{scheme}'. Only http:// and https:// are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must include a valid hostname or domain")

    hostname_lower = hostname.lower()

    if allow_local is None:
        allow_local = is_local_allowed()

    # Reject blocked hostnames
    if not allow_local:
        if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.endswith(".localhost") or hostname_lower.endswith(".local"):
            raise ValueError(f"SSRF Protection: Access to '{hostname}' is forbidden.")

    # Validate IP addresses
    try:
        # Check if hostname is direct IP literal
        ip_obj = ipaddress.ip_address(hostname_lower)
        if not allow_local:
            _check_ip_safety(ip_obj, hostname)
    except ValueError:
        # Hostname is a domain name, resolve via DNS
        if not allow_local:
            try:
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, socktype, proto, canonname, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    ip_obj = ipaddress.ip_address(ip_str)
                    _check_ip_safety(ip_obj, hostname)
            except socket.gaierror as exc:
                raise ValueError(f"Cannot resolve hostname '{hostname}': {exc}") from exc

    normalized_path = parsed.path.rstrip("/")
    normalized = urlunparse((
        scheme,
        parsed.netloc.lower(),
        normalized_path,
        "",
        parsed.query,
        "",
    ))
    return normalized


def _check_ip_safety(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, original_host: str) -> None:
    """Raise ValueError if IP address is internal, private, loopback, or link-local."""
    if ip_obj.is_loopback:
        raise ValueError(f"SSRF Protection: Loopback address for '{original_host}' ({ip_obj}) is forbidden.")
    if ip_obj.is_private:
        raise ValueError(f"SSRF Protection: Private network address for '{original_host}' ({ip_obj}) is forbidden.")
    if ip_obj.is_link_local:
        raise ValueError(f"SSRF Protection: Link-local address for '{original_host}' ({ip_obj}) is forbidden.")
    if ip_obj.is_multicast:
        raise ValueError(f"SSRF Protection: Multicast address for '{original_host}' ({ip_obj}) is forbidden.")
    if ip_obj.is_unspecified or ip_obj.is_reserved:
        raise ValueError(f"SSRF Protection: Reserved address for '{original_host}' ({ip_obj}) is forbidden.")

    # Explicit check for cloud metadata IP 169.254.169.254
    if str(ip_obj) == "169.254.169.254":
        raise ValueError(f"SSRF Protection: Cloud metadata access ({ip_obj}) is strictly forbidden.")


def is_safe_redirect(redirect_url: str, base_url: str, allow_local: bool | None = None) -> bool:
    """Validate that a redirect target remains within the application boundary and is SSRF-safe."""
    try:
        validated = validate_target_url(redirect_url, allow_local=allow_local)
        base_parsed = urlparse(base_url)
        target_parsed = urlparse(validated)
        return (
            target_parsed.scheme.lower() == base_parsed.scheme.lower()
            and target_parsed.netloc.lower() == base_parsed.netloc.lower()
        )
    except Exception:
        return False
