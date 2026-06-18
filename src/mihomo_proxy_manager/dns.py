"""DNS endpoint parsing, wire codec, clients, and proxy node resolution."""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .security import SecurityError, assert_safe_url

QTYPE = {"A": 1, "AAAA": 28}
RTYPE = {1: "A", 28: "AAAA", 5: "CNAME"}


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
            raise ValueError(f"DNS server resolves to non-public address: {exc}") from exc
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
        question_type, question_class = struct.unpack("!HH", message[offset : offset + 4])
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
