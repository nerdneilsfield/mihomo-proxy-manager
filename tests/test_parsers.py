import base64
import json

import pytest

from mihomo_proxy_manager.parsers import ParseError, parse_subscription
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
