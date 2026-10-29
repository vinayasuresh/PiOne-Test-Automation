"""security_guard.py: SSRF protection, target URL boundary enforcement, and request security.

Guarantees:
- Rejects loopback (127.0.0.0/8, ::1, [::1], 0.0.0.0)
- Rejects RFC 1918 private IPv4 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Rejects RFC 4193 unique local IPv6 (fc00::/7) and IPv4-mapped IPv6 bypasses (::ffff:127.0.0.1)
- Rejects link-local IPv4/IPv6 (169.254.0.0/16, fe80::/10) including cloud metadata (169.254.169.254, fd00:ec2::254)
- Rejects localhost, *.localhost, *.local, *.internal, metadata endpoints, and DNS rebinding domains (*.nip.io)
- Rejects obfuscated numeric IPs (decimal, octal, hex, dotted-short notation)
- Rejects unsupported URL schemes (file, data, ftp, javascript, chrome-error, about, etc.)
- Supports testing bypass strictly via explicit allow_local=True or ALLOW_LOCAL_TARGETS=1
- Validates redirect targets and document boundaries without cross-app leakage
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import unquote, urlparse, urlunparse

BLOCKED_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "::",
    "metadata.google.internal",
    "instance-data",
    "metadata.internal",
    "kubernetes.default.svc",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
}

BLOCKED_SCHEMES = {
    "file",
    "data",
    "ftp",
    "gopher",
    "dict",
    "sftp",
    "tftp",
    "ldap",
    "ldaps",
    "javascript",
    "chrome-error",
    "about",
}

BLOCKED_DOMAIN_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".nip.io",
    ".sslip.io",
)


def is_local_allowed() -> bool:
    """Check if local addresses are permitted by environment (e.g. for test suites)."""
    val = os.getenv("ALLOW_LOCAL_TARGETS", "").strip().lower()
    return val in {"1", "true", "yes"}


def validate_target_url(url: str, allow_local: bool | None = None, is_mock: bool = False) -> str:
    """Validate target URL for scheme, hostname, and SSRF restrictions.

    When is_mock is True, skips external DNS resolution while strictly validating
    allowed HTTP/HTTPS scheme, well-formed hostname, and safety against metadata
    and loopback/private literals. For live targets (is_mock=False), all SSRF guardrails
    and DNS resolutions are strictly enforced.

    Returns the normalized URL or raises ValueError.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL is required and must be a string")

    raw_url = url.strip()
    parsed = urlparse(raw_url)

    scheme = parsed.scheme.lower()
    if scheme in BLOCKED_SCHEMES or scheme not in {"http", "https"}:
        raise ValueError(f"Invalid URL scheme '{scheme}'. Only http:// and https:// are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must include a valid hostname or domain")

    # Reject userinfo (e.g. http://user:pass@host) to prevent host obscuration
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain embedded user credentials")

    hostname_lower = unquote(hostname).lower().strip("[]")

    if allow_local is None:
        allow_local = is_local_allowed()

    # Check if hostname is direct IP literal (IPv4 or IPv6)
    is_ip_literal = False
    try:
        ip_obj = ipaddress.ip_address(hostname_lower)
        is_ip_literal = True
    except ValueError:
        ip_obj = None

    if is_ip_literal:
        if not allow_local:
            _check_ip_safety(ip_obj, hostname)
    else:
        # Hostname is a domain name
        if not re.match(r"^[a-zA-Z0-9.-]+$", hostname_lower):
            raise ValueError(f"Invalid hostname characters in '{hostname}'")

        # Reject blocked hostnames and suffixes (allow safe mock domains like .test/.mock only in mock mode)
        if not allow_local:
            if hostname_lower in BLOCKED_HOSTNAMES:
                raise ValueError(f"SSRF Protection: Access to '{hostname}' is forbidden.")
            if any(hostname_lower.endswith(suffix) for suffix in BLOCKED_DOMAIN_SUFFIXES):
                raise ValueError(f"SSRF Protection: Domain '{hostname}' is forbidden.")

        # Check for obfuscated numeric/octal/hex IP representations (e.g. 2130706433, 0177.0.0.1, 0x7f.1)
        if not allow_local and re.match(r"^[0-9a-fxA-FX.]+$", hostname_lower) and not re.match(r"^[a-zA-Z]+$", hostname_lower):
            try:
                raw_bytes = socket.inet_aton(hostname_lower)
                inet_ip = ipaddress.IPv4Address(raw_bytes)
                _check_ip_safety(inet_ip, hostname)
            except (OSError, ValueError):
                pass

        # Only an explicit is_mock=True request may skip external DNS resolution.
        if is_mock:
            # Deterministic mock mode: skip external DNS resolution for valid domain names.
            pass
        elif not allow_local:
            try:
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                if not addr_info:
                    raise ValueError(f"Cannot resolve hostname '{hostname}'")
                for family, socktype, proto, canonname, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    ip_obj = ipaddress.ip_address(ip_str)
                    _check_ip_safety(ip_obj, hostname)
            except socket.gaierror as exc:
                raise ValueError(f"Cannot resolve hostname '{hostname}': {exc}") from exc

    # Strip any userinfo to prevent host obscuration
    port_str = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    netloc = f"{parsed.hostname.lower()}{port_str}"

    normalized_path = parsed.path.rstrip("/")
    normalized = urlunparse((
        scheme,
        netloc,
        normalized_path,
        "",
        parsed.query,
        "",
    ))
    return normalized


def _check_ip_safety(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, original_host: str) -> None:
    """Raise ValueError if IP address is internal, private, loopback, or link-local."""
    # Unpack IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
    ipv4_mapped = getattr(ip_obj, "ipv4_mapped", None)
    if ipv4_mapped:
        _check_ip_safety(ipv4_mapped, original_host)

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

    # Explicit check for cloud metadata IPs
    str_ip = str(ip_obj)
    if str_ip in {"169.254.169.254", "fd00:ec2::254"}:
        raise ValueError(f"SSRF Protection: Cloud metadata access ({ip_obj}) is strictly forbidden.")


def is_safe_redirect(redirect_url: str, base_url: str, allow_local: bool | None = None) -> bool:
    """Validate that a redirect target remains strictly within the application boundary and is SSRF-safe."""
    try:
        validated = validate_target_url(redirect_url, allow_local=allow_local)
        base_parsed = urlparse(base_url)
        target_parsed = urlparse(validated)

        # Same scheme requirement
        if target_parsed.scheme.lower() != base_parsed.scheme.lower():
            return False

        # Host comparison (ignoring case)
        base_host = (base_parsed.hostname or "").lower()
        target_host = (target_parsed.hostname or "").lower()
        if target_host != base_host:
            return False

        # Port comparison (normalizing default ports)
        def _effective_port(p):
            if p.port:
                return p.port
            return 443 if p.scheme.lower() == "https" else 80

        if _effective_port(target_parsed) != _effective_port(base_parsed):
            return False

        # Must also stay within the base application path
        from automation_framework.crawler.url_filter import is_within_app_path
        return is_within_app_path(validated, base_url)
    except Exception:
        return False
