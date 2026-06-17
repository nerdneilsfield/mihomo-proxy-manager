from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class SecurityError(ValueError):
    pass


_SECRET_QUERY_KEYS = {"token", "secret", "key", "apikey", "api_key", "access_token"}
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+\-/=]+")


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({ipaddress.ip_address(info[4][0]) for info in infos}, key=str)


_BASE64URL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def assert_safe_url(url: str, *, allow_private_network: bool, resolve_dns: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError(f"unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise SecurityError("URL host is required")
    if allow_private_network:
        return
    try:
        ips = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        if not resolve_dns:
            return
        ips = _resolve_host(parsed.hostname)
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
    redacted = _BEARER_RE.sub("Bearer ***", text)
    redacted = re.sub(r"(?i)(Authorization=)([^\s]+)(\s+Bearer\s+[^\s]+)?", r"\1***", redacted)
    redacted = re.sub(r"([?&](?:token|secret|key|apikey|api_key|access_token)=)[^&\s]+", r"\1***", redacted)
    for secret in extra_secrets or []:
        redacted = redacted.replace(secret, "***")
    return redacted
