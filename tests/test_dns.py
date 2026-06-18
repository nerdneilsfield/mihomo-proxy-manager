import socket

import pytest
from datetime import timedelta

import httpx2 as httpx

from mihomo_proxy_manager.dns import (
    DnsClient,
    DnsEndpoint,
    DnsMessageError,
    DnsResolver,
    build_query,
    decode_addresses,
    parse_dns_endpoint,
    validate_dns_endpoint_runtime,
    validate_dns_endpoint_static,
)
from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.models import HttpConfig, ProxyRecord, SourceDnsConfig


def test_parse_tls_endpoint_with_certificate_servername() -> None:
    endpoint = parse_dns_endpoint("tls://1.1.1.1:853?servername=cloudflare-dns.com")

    assert endpoint == DnsEndpoint(
        scheme="tls",
        host="1.1.1.1",
        port=853,
        path="",
        servername="cloudflare-dns.com",
    )


def test_static_validation_rejects_private_dns_server() -> None:
    endpoint = parse_dns_endpoint("udp://127.0.0.1:53")

    with pytest.raises(ValueError, match="non-public"):
        validate_dns_endpoint_static(endpoint, allow_private_network=False)


def test_build_query_encodes_a_question() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)

    assert query[:2] == b"\x12\x34"
    assert b"\x07example\x03com\x00" in query
    assert query.endswith(b"\x00\x01\x00\x01")


def test_decode_a_response() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)
    response = (
        query[:2]
        + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        + query[12:]
        + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x5d\xb8\xd8\x22"
    )

    assert decode_addresses(response, "example.com", "A", transaction_id=0x1234) == [
        "93.184.216.34"
    ]


def test_decode_rejects_mismatched_transaction_id() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)
    response = b"\x99\x99" + query[2:]

    with pytest.raises(DnsMessageError, match="transaction"):
        decode_addresses(response, "example.com", "A", transaction_id=0x1234)


def test_read_name_rejects_reserved_label_type() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)
    # Replace the qname length byte 0x07 with 0x40 (reserved label type)
    corrupted = query[:12] + b"\x40" + query[13:]
    response = (
        corrupted[:2] + b"\x81\x80\x00\x01\x00\x00\x00\x00\x00\x00" + corrupted[12:]
    )
    with pytest.raises(DnsMessageError, match="reserved DNS label type"):
        decode_addresses(response, "example.com", "A", transaction_id=0x1234)


def test_static_validation_rejects_private_https_dns_server() -> None:
    endpoint = parse_dns_endpoint("https://127.0.0.1/dns-query")

    with pytest.raises(ValueError, match="non-public"):
        validate_dns_endpoint_static(endpoint, allow_private_network=False)


@pytest.mark.asyncio
async def test_doh_client_uses_safe_http_post() -> None:
    requests: list[httpx.Request] = []
    query = build_query("example.com", "A", transaction_id=0x1234)
    response_message = (
        query[:2]
        + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        + query[12:]
        + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x5d\xb8\xd8\x22"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/dns-message"
        return httpx.Response(200, content=response_message)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    safe_http = SafeHttpClient(
        http_client,
        HttpConfig(timedelta(seconds=30), "mihomo/1.19.5", 4096, 3),
    )
    client = DnsClient(safe_http=safe_http)

    result = await client.query(
        DnsEndpoint("https", "example.com", 443, "/dns-query"),
        "example.com",
        "A",
        timeout=timedelta(seconds=5),
        allow_private_network=False,
        transaction_id=0x1234,
    )

    assert result == ["93.184.216.34"]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_dns_client_rejects_oversized_doh_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4097)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    safe_http = SafeHttpClient(
        http_client,
        HttpConfig(timedelta(seconds=30), "mihomo/1.19.5", 8192, 3),
    )
    client = DnsClient(safe_http=safe_http)

    with pytest.raises(DnsMessageError, match="too large"):
        await client.query(
            DnsEndpoint("https", "example.com", 443, "/dns-query"),
            "example.com",
            "A",
            timeout=timedelta(seconds=5),
            allow_private_network=False,
            transaction_id=0x1234,
        )


class FakeDnsClient:
    def __init__(self, responses: dict[tuple[str, str, str], list[str] | Exception]):
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

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
        key = (endpoint.scheme, name, qtype)
        self.calls.append(key)
        result = self.responses.get(key, DnsMessageError("no answer"))
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_resolver_rewrites_server_and_preserves_existing_servername() -> None:
    client = FakeDnsClient({("udp", "example.com", "A"): ["93.184.216.34"]})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [
        ProxyRecord(
            "airport_a",
            {
                "name": "HK",
                "type": "vmess",
                "server": "example.com",
                "tls": True,
                "servername": "custom.example.com",
            },
        )
    ]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert warnings == []
    assert resolved[0].data["server"] == "93.184.216.34"
    assert resolved[0].data["servername"] == "custom.example.com"
    assert records[0].data["server"] == "example.com"


@pytest.mark.asyncio
async def test_resolver_fills_missing_tls_servername_and_ws_host() -> None:
    client = FakeDnsClient({("udp", "example.com", "A"): ["93.184.216.34"]})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [
        ProxyRecord(
            "airport_a",
            {
                "name": "HK",
                "type": "vmess",
                "server": "example.com",
                "tls": True,
                "network": "ws",
                "ws-opts": {"path": "/ws"},
            },
        )
    ]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    data = resolved[0].data
    assert warnings == []
    assert data["server"] == "93.184.216.34"
    assert data["servername"] == "example.com"
    assert data["ws-opts"]["headers"]["Host"] == "example.com"


@pytest.mark.asyncio
async def test_resolver_failover_uses_second_server() -> None:
    client = FakeDnsClient(
        {
            ("udp", "example.com", "A"): DnsMessageError("first failed"),
            ("tcp", "example.com", "A"): ["93.184.216.34"],
        }
    )
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(
        True,
        ("udp://1.1.1.1:53", "tcp://8.8.8.8:53"),
        timedelta(seconds=5),
        "keep",
    )

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert warnings == []
    assert resolved[0].data["server"] == "93.184.216.34"
    assert ("udp", "example.com", "A") in client.calls
    assert ("tcp", "example.com", "A") in client.calls
    assert ("udp", "example.com", "AAAA") not in client.calls


@pytest.mark.asyncio
async def test_resolver_queries_aaaa_when_enable_ipv6_is_true() -> None:
    client = FakeDnsClient(
        {
            ("udp", "example.com", "A"): DnsMessageError("first failed"),
            ("udp", "example.com", "AAAA"): DnsMessageError("first failed"),
            ("tcp", "example.com", "A"): ["93.184.216.34"],
        }
    )
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(
        True,
        ("udp://1.1.1.1:53", "tcp://8.8.8.8:53"),
        timedelta(seconds=5),
        "keep",
        enable_ipv6=True,
    )

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert warnings == []
    assert resolved[0].data["server"] == "93.184.216.34"
    assert ("udp", "example.com", "A") in client.calls
    assert ("udp", "example.com", "AAAA") in client.calls
    assert ("tcp", "example.com", "A") in client.calls


@pytest.mark.asyncio
async def test_resolver_drop_policy_removes_failed_records() -> None:
    client = FakeDnsClient({})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "drop")

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert resolved == []
    assert len(warnings) == 1
    assert "HK" in warnings[0]


@pytest.mark.asyncio
async def test_validate_runtime_returns_pinned_ips_for_udp() -> None:
    endpoint = parse_dns_endpoint("udp://8.8.8.8:53")
    addresses = await validate_dns_endpoint_runtime(
        endpoint, allow_private_network=False
    )
    assert "8.8.8.8" in addresses


@pytest.mark.asyncio
async def test_validate_runtime_rejects_private_ip_for_udp() -> None:
    endpoint = parse_dns_endpoint("udp://127.0.0.1:53")
    with pytest.raises(ValueError, match="non-public"):
        await validate_dns_endpoint_runtime(endpoint, allow_private_network=False)


@pytest.mark.asyncio
async def test_validate_runtime_rejects_private_ipv6_for_udp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = parse_dns_endpoint("udp://[fd00::1]:53")

    def fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object):
        return [(socket.AF_INET6, socket.SOCK_DGRAM, 0, "", (host, port, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="non-public"):
        await validate_dns_endpoint_runtime(endpoint, allow_private_network=False)


@pytest.mark.asyncio
async def test_resolver_warning_includes_error_type_for_empty_str_exc() -> None:
    """When str(exc) is empty (e.g. httpx.ConnectTimeout), warning must still carry repr."""

    class _EmptyStrError(Exception):
        def __str__(self) -> str:
            return ""

    class _EmptyStrClient:
        async def query(
            self,
            endpoint,
            name,
            qtype,
            *,
            timeout,
            allow_private_network,
            transaction_id=None,
        ):
            raise _EmptyStrError()

    resolver = DnsResolver(client=_EmptyStrClient(), allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    _resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert len(warnings) == 1
    assert "_EmptyStrError" in warnings[0]


@pytest.mark.asyncio
async def test_resolver_deduplicates_servers_with_same_hostname() -> None:
    """Multiple nodes sharing the same server hostname resolve it only once."""
    client = FakeDnsClient(
        {
            ("udp", "example.com", "A"): ["93.184.216.34"],
            ("udp", "other.com", "A"): ["1.2.3.4"],
        }
    )
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [
        ProxyRecord("airport_a", {"name": "HK 01", "server": "example.com"}),
        ProxyRecord("airport_a", {"name": "HK 02", "server": "example.com"}),
        ProxyRecord("airport_a", {"name": "JP 01", "server": "other.com"}),
    ]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    resolved, warnings = await resolver.resolve_records(
        records, config, source="airport_a"
    )

    assert warnings == []
    assert len(resolved) == 3
    assert resolved[0].data["server"] == "93.184.216.34"
    assert resolved[1].data["server"] == "93.184.216.34"
    assert resolved[2].data["server"] == "1.2.3.4"
    # Only 2 unique hostnames queried, each once for A
    a_calls = [c for c in client.calls if c[2] == "A"]
    assert len(a_calls) == 2
