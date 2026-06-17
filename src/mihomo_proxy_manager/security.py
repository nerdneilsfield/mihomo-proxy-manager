"""URL 安全检查（SSRF 防护）、路径熵验证和敏感信息脱敏。

URL safety checks (SSRF prevention), path entropy validation, and secret redaction.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class SecurityError(ValueError):
    """安全相关错误，用于 URL 检查失败等情况。

    Security-related error for URL check failures and similar cases.
    """

    pass


_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_SECRET_QUERY_KEYS = {"token", "secret", "key", "apikey", "api_key", "access_token"}
_AUTHORIZATION_RE = re.compile(
    r"(?i)(Authorization[=:]\s*)(Bearer\s+\S+|Basic\s+\S+|\S+)"
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
    """检查 IP 地址是否为公网地址。

    Check whether an IP address is a public address.

    Args:
        ip: IP 地址 / IP address.

    Returns:
        如果是公网地址返回 True / True if the address is public.
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[_IpAddress]:
    """解析主机名为 IP 地址列表。

    Resolve a hostname to a list of IP addresses.

    Args:
        host: 主机名或 IP 字符串 / Hostname or IP string.

    Returns:
        IP 地址列表 / List of IP addresses.
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({ipaddress.ip_address(info[4][0]) for info in infos}, key=str)


def _is_blocked_hostname(hostname: str) -> bool:
    """检查主机名是否在阻止列表中。

    Check if a hostname is in the blocked list.

    Args:
        hostname: 主机名 / Hostname.

    Returns:
        如果被阻止返回 True / True if blocked.
    """
    lowered = hostname.lower().rstrip(".")
    if lowered in _PRIVATE_HOSTNAMES:
        return True
    return any(lowered.endswith(suffix) for suffix in _PRIVATE_HOSTNAME_SUFFIXES)


def _parse_ip_literal(host: str) -> _IpAddress | None:
    """Best-effort parse of canonical and non-canonical IP literals.

    Handles trailing dots, decimal/hex/octal integers, dotted forms with fewer
    than four octets, and IPv6 addresses written as 32 hex digits. Returns the
    parsed address or ``None`` if the value does not look like an IP literal.

    尽力解析规范和非规范的 IP 字面量。
    处理尾部点号、十进制/十六进制/八进制整数、少于四个八位组的点分形式、
    以及以 32 位十六进制数字编写的 IPv6 地址。返回解析后的地址，
    如果值看起来不像 IP 字面量则返回 ``None``。

    Args:
        host: 主机名字符串 / Hostname string.

    Returns:
        解析后的 IP 地址，或 None / Parsed IP address, or None.
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


def assert_safe_url(
    url: str, *, allow_private_network: bool, resolve_dns: bool = True
) -> None:
    """验证 URL 是否安全，防止 SSRF 攻击。

    Assert that a URL is safe, preventing SSRF attacks.

    .. warning::
        ``resolve_dns=True`` performs a synchronous ``socket.getaddrinfo`` and
        will block the event loop. From async code call
        :func:`assert_safe_url_async` instead.

    Args:
        url: 待验证的 URL / URL to validate.
        allow_private_network: 是否允许私有网络地址 / Whether to allow private network addresses.
        resolve_dns: 是否执行 DNS 解析（默认 True） / Whether to perform DNS resolution (default True).

    Raises:
        SecurityError: 如果 URL 不安全 / If the URL is not safe.
    """
    parsed = _check_static(url, allow_private_network=allow_private_network)
    if parsed is None or not resolve_dns:
        return
    host = parsed.hostname.rstrip(".")  # type: ignore[union-attr]
    ips = _resolve_host(host)
    for ip in ips:
        if not _is_public_ip(ip):
            raise SecurityError(f"URL resolves to non-public address: {ip}")


async def assert_safe_url_async(
    url: str, *, allow_private_network: bool, resolve_dns: bool = True
) -> None:
    """异步版本的 :func:`assert_safe_url`，将 DNS 解析卸载到线程池。

    Async variant of :func:`assert_safe_url` that offloads the blocking
    ``getaddrinfo`` call to the default executor so the event loop is not
    stalled while resolving subscription hosts.

    Args:
        url: 待验证的 URL / URL to validate.
        allow_private_network: 是否允许私有网络地址 / Whether to allow private network addresses.
        resolve_dns: 是否执行 DNS 解析（默认 True） / Whether to perform DNS resolution.

    Raises:
        SecurityError: 如果 URL 不安全 / If the URL is not safe.
    """
    parsed = _check_static(url, allow_private_network=allow_private_network)
    if parsed is None or not resolve_dns:
        return
    host = parsed.hostname.rstrip(".")  # type: ignore[union-attr]
    ips = await asyncio.to_thread(_resolve_host, host)
    for ip in ips:
        if not _is_public_ip(ip):
            raise SecurityError(f"URL resolves to non-public address: {ip}")


def _check_static(url: str, *, allow_private_network: bool):
    """共享的同步检查路径，仅返回需要解析 DNS 的已解析 URL 对象。

    Shared synchronous portion of URL validation. Returns the parsed URL only
    when a DNS lookup is still required to finish validation; returns ``None``
    when validation has been completed (e.g. private addresses allowed, IP
    literal, or blocked hostname).
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError(f"unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise SecurityError("URL host is required")
    if allow_private_network:
        return None

    host = parsed.hostname.rstrip(".")

    ip_literal = _parse_ip_literal(host)
    if ip_literal is not None:
        if not _is_public_ip(ip_literal):
            raise SecurityError(f"URL resolves to non-public address: {ip_literal}")
        return None

    if _is_blocked_hostname(host):
        raise SecurityError(f"URL host is blocked: {host}")

    return parsed


def has_path_entropy(path: str, *, min_bits: int) -> bool:
    """检查路径的最后一段是否具有足够的熵值。

    Check whether the last segment of a path has sufficient entropy.

    Args:
        path: URL 路径 / URL path.
        min_bits: 最小熵位数 / Minimum entropy bits.

    Returns:
        如果熵值足够返回 True / True if entropy is sufficient.
    """
    token = path.rsplit("/", 1)[-1].split(".", 1)[0]
    if not token or not _BASE64URL_TOKEN_RE.fullmatch(token):
        return False
    return len(token) * 6 >= min_bits


def redact_url(url: str) -> str:
    """脱敏 URL 中的敏感查询参数。

    Redact sensitive query parameters in a URL.

    Args:
        url: 原始 URL / Original URL.

    Returns:
        脱敏后的 URL / Redacted URL.
    """
    parsed = urlparse(url)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "***" if key.lower() in _SECRET_QUERY_KEYS else value))
    return urlunparse(parsed._replace(query=urlencode(query)))


def redact_secret(text: str, *, extra_secrets: list[str] | None = None) -> str:
    """脱敏文本中的敏感信息，包括 Authorization、Bearer token 和自定义密钥。

    Redact sensitive information in text, including Authorization, Bearer tokens, and custom secrets.

    Args:
        text: 原始文本 / Original text.
        extra_secrets: 额外的敏感字符串列表 / Additional sensitive strings to redact.

    Returns:
        脱敏后的文本 / Redacted text.
    """
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
