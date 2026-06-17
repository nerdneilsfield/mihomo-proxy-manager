from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class SecurityError(ValueError):
    pass


_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_SECRET_QUERY_KEYS = {"token", "secret", "key", "apikey", "api_key", "access_token"}
_AUTHORIZATION_RE = re.compile(
    r"(?i)(Authorization[=:]\s*)([^\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")

# Hostnames that are commonly used for private/loopback/link-local addresses.
# These are rejected even when DNS resolution is disabled to keep ``mpm check``
# fully offline.
_PRIVATE_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
    }
)
_PRIVATE_HOSTNAME_SUFFIXES = (".local", ".localdomain", ".internal", ".localhost")


def _is_public_ip(ip: _IpAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[_IpAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({ipaddress.ip_address(info[4][0]) for info in infos}, key=str)


def _is_blocked_hostname(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if lowered in _PRIVATE_HOSTNAMES:
        return True
    return any(lowered.endswith(suffix) for suffix in _PRIVATE_HOSTNAME_SUFFIXES)


def _parse_ip_literal(host: str) -> _IpAddress | None:
    """Best-effort parse of canonical and non-canonical IP literals.

    Handles trailing dots, decimal/hex/octal integers, dotted forms with fewer
    than four octets, and IPv6 addresses written as 32 hex digits. Returns the
    parsed address or ``None`` if the value does not look like an IP literal.
    """
    host = host.rstrip(".")

    # Treat a bare "0" (with optional trailing dot) as the unspecified IPv4
    # address. Python 3.14 no longer accepts "0" in ip_address(), and earlier
    # versions parsed it to 0.0.0.0, so handle it explicitly.
    if host == "0":
        return ipaddress.IPv4Address("0.0.0.0")

    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass

    # IPv6 address written as 32 hex digits (e.g. loopback as 32 zeros + 1).
    if len(host) == 32 and all(c in "0123456789abcdefABCDEF" for c in host):
        try:
            return ipaddress.ip_address(int(host, 16))
        except ValueError:
            pass

    # Hexadecimal integer forms such as 0x7f000001.
    if host.startswith(("0x", "0X")):
        try:
            return ipaddress.ip_address(int(host, 16))
        except ValueError:
            pass

    # Octal integer forms such as 017700000001.
    if host.startswith("0") and len(host) > 1 and host[1:].isdigit():
        try:
            return ipaddress.ip_address(int(host, 8))
        except ValueError:
            pass

    # Plain decimal integer forms such as 2130706433.
    if host.isdigit() and not host.startswith("0"):
        try:
            return ipaddress.ip_address(int(host))
        except ValueError:
            pass

    # Dotted forms supporting decimal/hex/octal per octet, including short
    # forms such as 127.1, 0x7f.1, or 0177.1.
    def _parse_octet(part: str) -> int:
        try:
            return int(part, 0)
        except ValueError:
            # int(part, 0) accepts explicit '0o' octal but may reject leading-zero
            # octal literals in newer Python versions; handle them explicitly.
            if part.startswith("0") and part.isdigit():
                return int(part, 8)
            raise

    parts = host.split(".")
    if 2 <= len(parts) <= 4:
        try:
            values = [_parse_octet(part) for part in parts]
        except ValueError:
            values = []
        if values and all(0 <= value <= 255 for value in values):
            if len(parts) == 4:
                return ipaddress.IPv4Address(".".join(str(value) for value in values))
            addr = 0
            for value in values[:-1]:
                addr = (addr << 8) | value
            addr = (addr << (8 * (5 - len(parts)))) | values[-1]
            return ipaddress.IPv4Address(addr)

    return None


_BASE64URL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def assert_safe_url(url: str, *, allow_private_network: bool, resolve_dns: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError(f"unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise SecurityError("URL host is required")
    if allow_private_network:
        return

    host = parsed.hostname.rstrip(".")

    ip_literal = _parse_ip_literal(host)
    if ip_literal is not None:
        if not _is_public_ip(ip_literal):
            raise SecurityError(f"URL resolves to non-public address: {ip_literal}")
        return

    if _is_blocked_hostname(host):
        raise SecurityError(f"URL host is blocked: {host}")

    if not resolve_dns:
        return

    ips = _resolve_host(host)
    for ip in ips:
        if not _is_public_ip(ip):
            raise SecurityError(f"URL resolves to non-public address: {ip}")


def has_path_entropy(path: str, *, min_bits: int) -> bool:
    token = path.rsplit("/", 1)[-1].split(".", 1)[0]
    if not token or not _BASE64URL_TOKEN_RE.fullmatch(token):
        return False
    return len(token) * 6 >= min_bits


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "***" if key.lower() in _SECRET_QUERY_KEYS else value))
    return urlunparse(parsed._replace(query=urlencode(query)))


def redact_secret(text: str, *, extra_secrets: list[str] | None = None) -> str:
    redacted = _AUTHORIZATION_RE.sub(r"\1***", text)
    redacted = _BEARER_RE.sub(r"Bearer ***", redacted)
    redacted = re.sub(
        r"([?&](?:token|secret|key|apikey|api_key|access_token)=)[^&\s]+",
        r"\1***",
        redacted,
    )
    for secret in extra_secrets or []:
        redacted = redacted.replace(secret, "***")
    return redacted
