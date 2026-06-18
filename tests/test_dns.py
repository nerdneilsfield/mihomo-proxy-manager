import pytest

from mihomo_proxy_manager.dns import (
    DnsEndpoint,
    DnsMessageError,
    build_query,
    decode_addresses,
    parse_dns_endpoint,
    validate_dns_endpoint_static,
)


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
        corrupted[:2]
        + b"\x81\x80\x00\x01\x00\x00\x00\x00\x00\x00"
        + corrupted[12:]
    )
    with pytest.raises(DnsMessageError, match="reserved DNS label type"):
        decode_addresses(response, "example.com", "A", transaction_id=0x1234)


def test_static_validation_rejects_private_https_dns_server() -> None:
    endpoint = parse_dns_endpoint("https://127.0.0.1/dns-query")

    with pytest.raises(ValueError, match="non-public"):
        validate_dns_endpoint_static(endpoint, allow_private_network=False)
