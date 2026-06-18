"""DNS endpoint parsing, wire codec, clients, and proxy node resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
import ssl
import struct
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlparse

from loguru import logger

from .fetcher import SafeHttpClient
from .models import ProxyRecord, SourceDnsConfig
from .security import SecurityError, assert_safe_url, redact_secret

QTYPE = {"A": 1, "AAAA": 28}
RTYPE = {1: "A", 28: "AAAA", 5: "CNAME"}

DNS_UDP_MAX_SIZE = 512
DNS_MESSAGE_MAX_SIZE = 4096
DNS_WARNING_LIMIT = 100
DNS_SERVER_CONCURRENCY = 16


class DnsMessageError(ValueError):
    """Raised for malformed or unusable DNS messages."""


@dataclass(frozen=True)
class DnsEndpoint:
    scheme: str
    host: str
    port: int
    path: str
    servername: str | None = None


def parse_dns_endpoint(value: str) -> DnsEndpoint:
    parsed = urlparse(value)
    if parsed.scheme not in {"udp", "tcp", "tls", "https"}:
        raise ValueError(f"unsupported DNS server scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("DNS server host is required")
    if parsed.scheme == "https":
        port = parsed.port or 443
        path = parsed.path or "/dns-query"
    else:
        if parsed.port is None:
            raise ValueError("DNS server port is required")
        port = parsed.port
        path = ""
    query = parse_qs(parsed.query)
    servername = query.get("servername", [None])[-1]
    return DnsEndpoint(parsed.scheme, parsed.hostname, port, path, servername)


def validate_dns_endpoint_static(
    endpoint: DnsEndpoint, *, allow_private_network: bool
) -> None:
    if endpoint.scheme == "https":
        try:
            assert_safe_url(
                f"https://{endpoint.host}:{endpoint.port}{endpoint.path}",
                allow_private_network=allow_private_network,
                resolve_dns=False,
            )
        except SecurityError as exc:
            raise ValueError(
                f"DNS server resolves to non-public address: {exc}"
            ) from exc
        return
    try:
        assert_safe_url(
            f"https://{endpoint.host}/",
            allow_private_network=allow_private_network,
            resolve_dns=False,
        )
    except SecurityError as exc:
        raise ValueError(f"DNS server resolves to non-public address: {exc}") from exc


def _encode_name(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    if not labels or any(not label for label in labels):
        raise DnsMessageError("invalid domain name")
    output = bytearray()
    for label in labels:
        try:
            encoded = label.encode("idna")
        except (UnicodeError, ValueError) as exc:
            raise DnsMessageError(f"invalid domain label: {label!r}") from exc
        if len(encoded) > 63:
            raise DnsMessageError("domain label too long")
        output.append(len(encoded))
        output.extend(encoded)
    output.append(0)
    return bytes(output)


def build_query(name: str, qtype: str, *, transaction_id: int) -> bytes:
    question = _encode_name(name) + struct.pack("!HH", QTYPE[qtype], 1)
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    return header + question


def _read_name(message: bytes, offset: int, *, depth: int = 0) -> tuple[str, int]:
    if depth > 20:
        raise DnsMessageError("too many compression pointers")
    labels: list[str] = []
    while True:
        if offset >= len(message):
            raise DnsMessageError("name exceeds message")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise DnsMessageError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            pointed, _ = _read_name(message, pointer, depth=depth + 1)
            labels.append(pointed)
            offset += 2
            break
        if length & 0xC0 != 0:
            raise DnsMessageError("reserved DNS label type")
        if length == 0:
            offset += 1
            break
        offset += 1
        label = message[offset : offset + length]
        if len(label) != length:
            raise DnsMessageError("truncated label")
        try:
            labels.append(label.decode("idna"))
        except (UnicodeError, ValueError) as exc:
            raise DnsMessageError("invalid domain label in response") from exc
        offset += length
    name = ".".join(item for item in labels if item)
    return name, offset


def decode_addresses(
    message: bytes, name: str, qtype: str, *, transaction_id: int
) -> list[str]:
    if len(message) < 12:
        raise DnsMessageError("truncated DNS response")
    (
        response_id,
        flags,
        qdcount,
        ancount,
        _nscount,
        _arcount,
    ) = struct.unpack("!HHHHHH", message[:12])
    if response_id != transaction_id:
        raise DnsMessageError("transaction id mismatch")
    if flags & 0x0200:
        raise DnsMessageError("truncated DNS response")
    rcode = flags & 0x000F
    if rcode:
        raise DnsMessageError(f"dns rcode {rcode}")
    offset = 12
    for _ in range(qdcount):
        qname, offset = _read_name(message, offset)
        if offset + 4 > len(message):
            raise DnsMessageError("truncated question")
        question_type, question_class = struct.unpack(
            "!HH", message[offset : offset + 4]
        )
        offset += 4
        if qname.rstrip(".").lower() != name.rstrip(".").lower():
            raise DnsMessageError("question name mismatch")
        if question_type != QTYPE[qtype] or question_class != 1:
            raise DnsMessageError("question type mismatch")
    addresses: list[str] = []
    for _ in range(ancount):
        _answer_name, offset = _read_name(message, offset)
        if offset + 10 > len(message):
            raise DnsMessageError("truncated answer")
        answer_type, answer_class, _ttl, rdlength = struct.unpack(
            "!HHIH", message[offset : offset + 10]
        )
        offset += 10
        rdata = message[offset : offset + rdlength]
        if len(rdata) != rdlength:
            raise DnsMessageError("truncated rdata")
        offset += rdlength
        if answer_class != 1:
            continue
        if answer_type == 1 and qtype == "A" and rdlength == 4:
            addresses.append(str(ipaddress.IPv4Address(rdata)))
        elif answer_type == 28 and qtype == "AAAA" and rdlength == 16:
            addresses.append(str(ipaddress.IPv6Address(rdata)))
    if not addresses:
        raise DnsMessageError("no usable addresses")
    return addresses


async def validate_dns_endpoint_runtime(
    endpoint: DnsEndpoint, *, allow_private_network: bool
) -> list[str]:
    """Validate the endpoint and return resolved IP addresses for connect-time pinning.

    For non-HTTPS schemes, returns the deduplicated list of resolved IPs. Each IP is
    checked against the public-network policy. The caller MUST pin the connection to
    one of these IPs to prevent DNS rebinding (resolve once, connect to a different IP).

    For HTTPS scheme, returns an empty list because connection safety is delegated to
    SafeHttpClient (which performs its own per-hop URL safety check).
    """
    validate_dns_endpoint_static(endpoint, allow_private_network=allow_private_network)
    if endpoint.scheme == "https":
        return []
    infos = await asyncio.to_thread(socket.getaddrinfo, endpoint.host, endpoint.port)
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = str(info[4][0])
        if ip in seen:
            continue
        seen.add(ip)
        if not allow_private_network:
            try:
                parsed_ip = ipaddress.ip_address(ip)
            except ValueError as exc:
                raise ValueError(f"unparseable DNS server address: {ip!r}") from exc
            if parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local:
                raise ValueError(f"DNS server resolves to non-public address: {ip}")
            if (
                parsed_ip.is_multicast
                or parsed_ip.is_reserved
                or parsed_ip.is_unspecified
            ):
                raise ValueError(f"DNS server resolves to non-public address: {ip}")
        addresses.append(ip)
    if not addresses:
        raise ValueError(f"DNS server has no usable address: {endpoint.host!r}")
    return addresses


class DnsClient:
    def __init__(self, *, safe_http: SafeHttpClient) -> None:
        self.safe_http = safe_http

    async def query(
        self,
        endpoint: DnsEndpoint,
        name: str,
        qtype: str,
        *,
        timeout: timedelta,
        allow_private_network: bool,
        transaction_id: int | None = None,
    ) -> list[str]:
        resolved_ips = await validate_dns_endpoint_runtime(
            endpoint, allow_private_network=allow_private_network
        )
        tid = transaction_id if transaction_id is not None else secrets.randbelow(65536)
        query = build_query(name, qtype, transaction_id=tid)
        if endpoint.scheme == "https":
            response = await self._query_https(
                endpoint,
                query,
                timeout,
                allow_private_network=allow_private_network,
            )
        elif endpoint.scheme == "udp":
            response = await self._query_udp(endpoint, query, timeout, resolved_ips)
        elif endpoint.scheme == "tcp":
            response = await self._query_tcp(
                endpoint, query, timeout, tls=False, resolved_ips=resolved_ips
            )
        elif endpoint.scheme == "tls":
            response = await self._query_tcp(
                endpoint, query, timeout, tls=True, resolved_ips=resolved_ips
            )
        else:
            raise ValueError(f"unsupported DNS server scheme: {endpoint.scheme!r}")
        return decode_addresses(response, name, qtype, transaction_id=tid)

    async def _query_https(
        self,
        endpoint: DnsEndpoint,
        query: bytes,
        timeout: timedelta,
        *,
        allow_private_network: bool,
    ) -> bytes:
        response = await self.safe_http.request(
            "POST",
            f"https://{endpoint.host}:{endpoint.port}{endpoint.path}",
            headers={
                "Content-Type": "application/dns-message",
                "Accept": "application/dns-message",
            },
            timeout=timeout.total_seconds(),
            allow_private_network=allow_private_network,
            body=query,
        )
        response.raise_for_status()
        if len(response.content) > DNS_MESSAGE_MAX_SIZE:
            raise DnsMessageError("DNS response too large")
        return response.content

    async def _query_udp(
        self,
        endpoint: DnsEndpoint,
        query: bytes,
        timeout: timedelta,
        resolved_ips: list[str],
    ) -> bytes:
        loop = asyncio.get_running_loop()
        last_error: Exception | None = None
        for ip in resolved_ips:
            try:
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: _DnsDatagramProtocol(query),
                    remote_addr=(ip, endpoint.port),
                )
            except OSError as exc:
                last_error = exc
                continue
            try:
                return await asyncio.wait_for(
                    protocol.response, timeout.total_seconds()
                )
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
            finally:
                transport.close()
        if last_error is not None:
            raise last_error
        raise DnsMessageError("no DNS server IP available")

    async def _query_tcp(
        self,
        endpoint: DnsEndpoint,
        query: bytes,
        timeout: timedelta,
        *,
        tls: bool,
        resolved_ips: list[str],
    ) -> bytes:
        ssl_context = None
        server_hostname = None
        if tls:
            ssl_context = ssl.create_default_context()
            server_hostname = endpoint.servername or endpoint.host
        last_error: Exception | None = None
        for ip in resolved_ips:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        ip,
                        endpoint.port,
                        ssl=ssl_context,
                        server_hostname=server_hostname,
                    ),
                    timeout.total_seconds(),
                )
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
                continue
            try:
                writer.write(struct.pack("!H", len(query)) + query)
                await asyncio.wait_for(writer.drain(), timeout.total_seconds())
                size_bytes = await asyncio.wait_for(
                    reader.readexactly(2), timeout.total_seconds()
                )
                size = struct.unpack("!H", size_bytes)[0]
                if size > DNS_MESSAGE_MAX_SIZE:
                    raise DnsMessageError("DNS response too large")
                return await asyncio.wait_for(
                    reader.readexactly(size), timeout.total_seconds()
                )
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError, ssl.SSLError):
                    pass
        if last_error is not None:
            raise last_error
        raise DnsMessageError("no DNS server IP available")


class _DnsDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, query: bytes) -> None:
        self.query = query
        self.response: asyncio.Future[bytes] = (
            asyncio.get_running_loop().create_future()
        )

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        transport.sendto(self.query)

    def datagram_received(self, data: bytes, addr: object) -> None:
        if len(data) > DNS_UDP_MAX_SIZE:
            if not self.response.done():
                self.response.set_exception(DnsMessageError("DNS response too large"))
        elif not self.response.done():
            self.response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.response.done():
            self.response.set_exception(exc)


class DnsClientProtocol(Protocol):
    async def query(
        self,
        endpoint: DnsEndpoint,
        name: str,
        qtype: str,
        *,
        timeout: timedelta,
        allow_private_network: bool,
        transaction_id: int | None = None,
    ) -> list[str]:
        raise NotImplementedError


class DnsResolverProtocol(Protocol):
    async def resolve_records(
        self,
        records: list[ProxyRecord],
        config: SourceDnsConfig,
        *,
        source: str,
    ) -> tuple[list[ProxyRecord], list[str]]: ...


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_tls_like(data: dict[str, object]) -> bool:
    return data.get("tls") is True or data.get("security") in {"tls", "reality"}


def _is_ws(data: dict[str, object]) -> bool:
    return data.get("network") == "ws"


def _preserve_host_metadata(data: dict[str, object], original_host: str) -> None:
    if _is_tls_like(data) and "servername" not in data and "sni" not in data:
        data["servername"] = original_host
    if _is_ws(data):
        ws_opts_raw = data.get("ws-opts")
        if not isinstance(ws_opts_raw, dict):
            ws_opts_raw = {}
            data["ws-opts"] = ws_opts_raw
        ws_opts = cast(dict[str, Any], ws_opts_raw)
        headers_raw = ws_opts.get("headers")
        if not isinstance(headers_raw, dict):
            headers_raw = {}
            ws_opts["headers"] = headers_raw
        headers = cast(dict[str, Any], headers_raw)
        headers.setdefault("Host", original_host)


class DnsResolver:
    def __init__(
        self, *, client: DnsClientProtocol, allow_private_network: bool
    ) -> None:
        self.client = client
        self.allow_private_network = allow_private_network

    async def resolve_records(
        self,
        records: list[ProxyRecord],
        config: SourceDnsConfig,
        *,
        source: str,
    ) -> tuple[list[ProxyRecord], list[str]]:
        if not config.enabled:
            return records, []
        endpoints = [parse_dns_endpoint(value) for value in config.servers]
        unique_servers = self._collect_unique_servers(records)
        logger.debug(
            "dns resolve start: source={source} nodes={nodes} unique_servers={unique} endpoints={endpoints} failure={failure}",
            source=source,
            nodes=len(records),
            unique=len(unique_servers),
            endpoints=len(endpoints),
            failure=config.failure,
        )
        warnings: list[str] = []
        query_semaphore = asyncio.Semaphore(DNS_SERVER_CONCURRENCY)
        resolutions = await self._resolve_unique_servers(
            unique_servers, endpoints, config, source, query_semaphore
        )
        if config.failure == "fail":
            for server in unique_servers:
                ip, err = resolutions[server]
                if ip is None:
                    raise RuntimeError(
                        f"dns resolution failed: source={source!r} "
                        f"server={server!r} error={err}"
                    )
        kept: list[ProxyRecord] = []
        warned_servers: set[str] = set()
        for record in records:
            outcome = self._apply_resolution(
                record, resolutions, config, source, warnings, warned_servers
            )
            if outcome is not None:
                kept.append(outcome)
        if len(warnings) > DNS_WARNING_LIMIT:
            omitted = len(warnings) - DNS_WARNING_LIMIT
            warnings = warnings[:DNS_WARNING_LIMIT] + [
                f"dns warning limit reached for source {source!r}; omitted {omitted} warnings"
            ]
        logger.info(
            "dns resolve done: source={source} input={input} kept={kept} dropped={dropped} warnings={warnings}",
            source=source,
            input=len(records),
            kept=len(kept),
            dropped=len(records) - len(kept),
            warnings=len(warnings),
        )
        return kept, warnings

    @staticmethod
    def _collect_unique_servers(records: list[ProxyRecord]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for record in records:
            server = record.data.get("server")
            if not isinstance(server, str) or not server or _is_ip_literal(server):
                continue
            if server in seen:
                continue
            seen.add(server)
            ordered.append(server)
        return ordered

    async def _resolve_unique_servers(
        self,
        servers: list[str],
        endpoints: list[DnsEndpoint],
        config: SourceDnsConfig,
        source: str,
        query_semaphore: asyncio.Semaphore,
    ) -> dict[str, tuple[str | None, str]]:
        """Resolve each unique server hostname concurrently.

        Returns mapping ``server -> (resolved_ip_or_None, error_message)``.
        Concurrency budget is enforced at the query level via ``query_semaphore``,
        so total in-flight DNS queries stay bounded regardless of how many
        servers fan out simultaneously.
        """

        async def resolve_one(server: str) -> tuple[str, str | None, str]:
            ip, err = await self._resolve_server(
                server, endpoints, config, source, query_semaphore
            )
            return server, ip, err

        results = await asyncio.gather(*(resolve_one(server) for server in servers))
        return {server: (ip, err) for server, ip, err in results}

    async def _resolve_server(
        self,
        server: str,
        endpoints: list[DnsEndpoint],
        config: SourceDnsConfig,
        source: str,
        query_semaphore: asyncio.Semaphore,
    ) -> tuple[str | None, str]:
        """Resolve one hostname.

        Walks endpoints in configured order (preserves user-declared priority)
        and returns the first IP found. Within a single endpoint, A and AAAA
        are queried concurrently when ``enable_ipv6`` is true; A wins if
        available, AAAA is the fallback.
        """
        last_error = "no DNS server returned an address"
        ipv6 = config.enable_ipv6
        for endpoint in endpoints:
            ip, err = await self._query_endpoint(
                endpoint, server, ipv6, config, source, query_semaphore
            )
            if ip is not None:
                return ip, ""
            if err:
                last_error = err
        return None, last_error

    async def _query_endpoint(
        self,
        endpoint: DnsEndpoint,
        server: str,
        ipv6: bool,
        config: SourceDnsConfig,
        source: str,
        query_semaphore: asyncio.Semaphore,
    ) -> tuple[str | None, str]:
        """Query A (and optionally AAAA) on a single endpoint with A preference."""

        async def run_one(qtype: str) -> tuple[str | None, str]:
            async with query_semaphore:
                try:
                    addresses = await self.client.query(
                        endpoint,
                        server,
                        qtype,
                        timeout=config.timeout,
                        allow_private_network=self.allow_private_network,
                    )
                except Exception as exc:
                    exc_str = str(exc)
                    if not exc_str:
                        exc_str = repr(exc)
                    err = redact_secret(exc_str)[:200]
                    logger.debug(
                        "dns query failed: source={source} server={server} scheme={scheme} qtype={qtype} error={error}",
                        source=source,
                        server=server,
                        scheme=endpoint.scheme,
                        qtype=qtype,
                        error=err,
                    )
                    return None, err
                if addresses:
                    logger.debug(
                        "dns query ok: source={source} server={server} scheme={scheme} qtype={qtype} ip={ip}",
                        source=source,
                        server=server,
                        scheme=endpoint.scheme,
                        qtype=qtype,
                        ip=addresses[0],
                    )
                    return addresses[0], ""
                return None, "empty answer"

        if not ipv6:
            return await run_one("A")
        a_task = asyncio.create_task(run_one("A"))
        aaaa_task = asyncio.create_task(run_one("AAAA"))
        try:
            a_ip, a_err = await a_task
            if a_ip is not None:
                aaaa_task.cancel()
                return a_ip, ""
            aaaa_ip, aaaa_err = await aaaa_task
            if aaaa_ip is not None:
                return aaaa_ip, ""
            return None, a_err or aaaa_err or "no DNS server returned an address"
        finally:
            for task in (a_task, aaaa_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(a_task, aaaa_task, return_exceptions=True)

    def _apply_resolution(
        self,
        record: ProxyRecord,
        resolutions: dict[str, tuple[str | None, str]],
        config: SourceDnsConfig,
        source: str,
        warnings: list[str],
        warned_servers: set[str],
    ) -> ProxyRecord | None:
        server = record.data.get("server")
        if not isinstance(server, str) or not server or _is_ip_literal(server):
            return record
        resolved = resolutions.get(server)
        if resolved is None:
            return record
        ip, last_error = resolved
        if ip is not None:
            data = deepcopy(record.data)
            _preserve_host_metadata(data, server)
            data["server"] = ip
            return ProxyRecord(record.source, data)
        proxy_name = str(record.data.get("name", "<unnamed>"))[:120]
        warning = (
            f"dns resolution failed: source={source!r} proxy={proxy_name!r} "
            f"server={server!r} error={last_error}"
        )
        if server not in warned_servers:
            logger.warning(
                "dns resolution failed: source={source} server={server} failure={failure}",
                source=source,
                server=server,
                failure=config.failure,
            )
            warned_servers.add(server)
        if config.failure == "keep":
            warnings.append(warning)
            return record
        # failure == "drop" or "fail" (fail already raised in resolve_records)
        warnings.append(warning)
        return None
