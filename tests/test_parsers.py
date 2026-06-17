import base64
import json

import pytest

from mihomo_proxy_manager.parsers import ParseError, parse_subscription
from mihomo_proxy_manager.parsers.share_links import _parse_ss
from mihomo_proxy_manager.parsers.yaml import validate_required_fields


def test_parse_yaml_provider_payload() -> None:
    body = b"""
proxies:
  - name: HK 01
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
    result = parse_subscription(body, source="airport_a", fmt="yaml", parse_error="fail")

    assert result.warnings == []
    assert result.records[0].source == "airport_a"
    assert result.records[0].data["name"] == "HK 01"


def test_parse_yaml_full_config() -> None:
    body = b"""
port: 7890
proxies:
  - name: JP 01
    type: ss
    server: example.com
    port: 443
    cipher: chacha20-ietf-poly1305
    password: secret
"""
    result = parse_subscription(body, source="airport_a", fmt="auto", parse_error="fail")

    assert result.records[0].data["type"] == "ss"


def test_required_field_validation() -> None:
    missing = validate_required_fields({"name": "bad", "type": "vmess", "server": "x"})
    assert "missing required field" in missing[0]


def test_plain_share_links() -> None:
    body = b"trojan://password@example.com:443?sni=example.com#TR%2001\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["name"] == "TR 01"
    assert result.records[0].data["type"] == "trojan"
    assert result.records[0].data["password"] == "password"


def test_ss_sip002_share_link() -> None:
    body = b"ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@example.com:443#SS%2001\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"
    assert proxy["server"] == "example.com"


def test_ss_share_link_maps_plugin_options() -> None:
    body = (
        b"ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@example.com:443"
        b"?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dwww.bing.com#SS%2001\n"
    )
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "http", "obfs-host": "www.bing.com"}


def test_vless_share_link() -> None:
    body = b"vless://00000000-0000-0000-0000-000000000000@example.com:443?encryption=none&security=tls&sni=example.com#VL%2001\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["type"] == "vless"
    assert result.records[0].data["uuid"] == "00000000-0000-0000-0000-000000000000"


def test_vless_share_link_maps_reality_and_transport_options() -> None:
    body = (
        b"vless://00000000-0000-0000-0000-000000000000@example.com:443"
        b"?encryption=none&security=reality&type=tcp&sni=example.com"
        b"&flow=xtls-rprx-vision&pbk=pubkey&sid=abcd&fp=chrome#VL%2001\n"
    )
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["network"] == "tcp"
    assert proxy["tls"] is True
    assert proxy["reality-opts"] == {"public-key": "pubkey", "short-id": "abcd"}
    assert proxy["client-fingerprint"] == "chrome"
    assert proxy["flow"] == "xtls-rprx-vision"


def test_trojan_share_link_maps_ws_options() -> None:
    body = (
        b"trojan://password@example.com:443"
        b"?type=ws&sni=example.com&host=cdn.example.com&path=%2Fws#TR%2001\n"
    )
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["network"] == "ws"
    assert proxy["ws-opts"] == {"path": "/ws", "headers": {"Host": "cdn.example.com"}}


def test_hysteria2_share_link() -> None:
    body = b"hysteria2://password@example.com:443?sni=example.com#HY2%2001\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["type"] == "hysteria2"
    assert result.records[0].data["password"] == "password"


def test_base64_share_links() -> None:
    vmess = {
        "v": "2",
        "ps": "VM 01",
        "add": "example.com",
        "port": "443",
        "id": "00000000-0000-0000-0000-000000000000",
        "aid": "0",
        "scy": "auto",
        "tls": "tls",
    }
    link = "vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode()
    encoded = base64.b64encode(link.encode())

    result = parse_subscription(encoded, source="airport_a", fmt="auto", parse_error="fail")

    assert result.records[0].data["type"] == "vmess"
    assert result.records[0].data["name"] == "VM 01"


def test_parse_error_skip_bad_nodes() -> None:
    body = b"not-a-node\ntrojan://password@example.com:443#TR%2001\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="skip")

    assert len(result.records) == 1
    assert result.warnings


def test_parse_error_fail_bad_nodes() -> None:
    with pytest.raises(ParseError):
        parse_subscription(b"not-a-node\n", source="airport_a", fmt="share-links", parse_error="fail")


def test_share_links_rejects_non_utf8_body() -> None:
    with pytest.raises(ParseError, match="UTF-8"):
        parse_subscription(b"\xff\xfe\xfd", source="airport_a", fmt="share-links", parse_error="fail")


def test_auto_rejects_invalid_base64() -> None:
    with pytest.raises(ParseError, match="base64"):
        parse_subscription(b"not-base64!!!", source="airport_a", fmt="auto", parse_error="fail")


def test_auto_includes_yaml_error_when_all_formats_fail() -> None:
    with pytest.raises(ParseError, match="YAML was not a valid subscription") as exc_info:
        parse_subscription(b"{", source="airport_a", fmt="auto", parse_error="skip")

    assert "base64" in str(exc_info.value)


def test_parse_yaml_rejects_non_utf8_body() -> None:
    with pytest.raises(ParseError, match="failed to parse YAML"):
        parse_subscription(b"\xff\xfe\xfd", source="airport_a", fmt="yaml", parse_error="fail")


def test_auto_yaml_validation_fail_does_not_fallback() -> None:
    body = b"""
proxies:
  - name: bad
    type: ss
    server: example.com
"""
    with pytest.raises(ParseError) as exc_info:
        parse_subscription(body, source="airport_a", fmt="auto", parse_error="fail")

    assert "missing required field" in str(exc_info.value)
    assert "base64" not in str(exc_info.value).lower()


def test_legacy_ss_link_with_ipv6_endpoint() -> None:
    payload = base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:secret@[2001:db8::1]:443").decode()
    link = f"ss://{payload}#SS%20IPv6"
    proxy = _parse_ss(link)

    assert proxy["server"] == "2001:db8::1"
    assert proxy["port"] == 443
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"


def test_share_links_warning_redacts_exception_detail() -> None:
    # A vmess link with an invalid base64 payload produces an exception message that
    # could contain the raw link/token. The warning must redact/truncate it.
    body = b"vmess://secret-token-that-should-not-leak\n"
    with pytest.raises(ParseError) as exc_info:
        parse_subscription(body, source="airport_a", fmt="share-links", parse_error="skip")

    message = str(exc_info.value)
    assert "secret-token-that-should-not-leak" not in message
    assert "failed to parse share link" in message


def test_legacy_ss_link_with_plugin_query() -> None:
    payload = base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:secret@example.com:443").decode()
    body = (
        f"ss://{payload}"
        "?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dwww.bing.com#SS%2001"
    ).encode()
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"
    assert proxy["server"] == "example.com"
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "http", "obfs-host": "www.bing.com"}


def test_parse_ss_legacy_with_query_directly() -> None:
    payload = base64.urlsafe_b64encode(b"aes-256-gcm:pass@192.0.2.1:8388").decode()
    link = f"ss://{payload}?plugin=obfs-local%3Bobfs%3Dtls#Legacy"
    proxy = _parse_ss(link)
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "tls"}
