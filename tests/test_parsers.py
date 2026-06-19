"""订阅解析器测试，包括 YAML、share-links 和 base64 格式。

Subscription parser tests including YAML, share-links, and base64 formats.
"""

import base64
from collections.abc import Callable
from copy import deepcopy
import json
import random
from typing import Any, cast

import pytest

from mihomo_proxy_manager.parsers import ParseError, parse_subscription
from mihomo_proxy_manager.parsers.share_links import _parse_ss
from mihomo_proxy_manager.parsers.yaml import validate_required_fields
from mihomo_proxy_manager.mihomo_schema import (
    COMMON_PROXY_FIELDS,
    PROXY_SCHEMAS,
    SchemaValue,
)


def test_parse_yaml_provider_payload() -> None:
    """测试解析 YAML 提供者格式的订阅内容。

    Test parsing YAML provider format subscription content.
    """
    body = b"""
proxies:
  - name: HK 01
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="fail"
    )

    assert result.warnings == []
    assert result.records[0].source == "airport_a"
    assert result.records[0].data["name"] == "HK 01"


def test_parse_yaml_full_config() -> None:
    """测试解析完整 YAML 配置格式的订阅内容。

    Test parsing full YAML config format subscription content.
    """
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
    result = parse_subscription(
        body, source="airport_a", fmt="auto", parse_error="fail"
    )

    assert result.records[0].data["type"] == "ss"


def test_required_field_validation() -> None:
    """测试必填字段验证函数。

    Test the required field validation function.
    """
    missing = validate_required_fields({"name": "bad", "type": "vmess", "server": "x"})
    assert "missing required field" in missing[0]


@pytest.mark.parametrize(
    ("proxy_type", "missing_field", "proxy"),
    [
        (
            "ss",
            "password",
            {"name": "x", "type": "ss", "server": "s", "port": 443, "cipher": "aes"},
        ),
        ("vless", "uuid", {"name": "x", "type": "vless", "server": "s", "port": 443}),
        (
            "trojan",
            "password",
            {"name": "x", "type": "trojan", "server": "s", "port": 443},
        ),
        (
            "hysteria2",
            "password",
            {"name": "x", "type": "hysteria2", "server": "s", "port": 443},
        ),
        ("http", "port", {"name": "x", "type": "http", "server": "s"}),
        ("socks5", "port", {"name": "x", "type": "socks5", "server": "s"}),
    ],
)
def test_validate_required_fields_per_type(
    proxy_type: str, missing_field: str, proxy: dict[str, object]
) -> None:
    """测试按代理类型验证必填字段。

    Test required field validation per proxy type.

    Args:
        proxy_type: 代理类型 / Proxy type.
        missing_field: 缺少的字段名 / Missing field name.
        proxy: 代理配置字典 / Proxy config dict.
    """
    warnings = validate_required_fields(proxy)
    assert any(f"missing required field {missing_field!r}" in w for w in warnings)


@pytest.mark.parametrize(
    "proxy_type", ["ss", "vless", "trojan", "hysteria2", "http", "socks5"]
)
def test_validate_required_fields_complete_proxy_has_no_warnings(
    proxy_type: str,
) -> None:
    """测试完整的代理配置没有验证警告。

    Test that a complete proxy config has no validation warnings.

    Args:
        proxy_type: 代理类型 / Proxy type.
    """
    proxy: dict[str, object] = {
        "name": "x",
        "type": proxy_type,
        "server": "s",
        "port": 443,
    }
    if proxy_type in {"ss"}:
        proxy["cipher"] = "aes"
        proxy["password"] = "p"
    if proxy_type == "vless":
        proxy["uuid"] = "00000000-0000-0000-0000-000000000000"
    if proxy_type in {"trojan", "hysteria2"}:
        proxy["password"] = "p"
    assert validate_required_fields(proxy) == []


def test_plain_share_links() -> None:
    """测试解析普通 share-link 格式的订阅内容。

    Test parsing plain share-link format subscription content.
    """
    body = b"trojan://password@example.com:443?sni=example.com#TR%2001\n"
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    assert result.records[0].data["name"] == "TR 01"
    assert result.records[0].data["type"] == "trojan"
    assert result.records[0].data["password"] == "password"


def test_ss_sip002_share_link() -> None:
    """测试解析 SS SIP002 格式的 share-link。

    Test parsing SS SIP002 format share-link.
    """
    body = b"ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@example.com:443#SS%2001\n"
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"
    assert proxy["server"] == "example.com"


def test_ss_share_link_maps_plugin_options() -> None:
    """测试 SS share-link 映射插件选项。

    Test that SS share-link maps plugin options.
    """
    body = (
        b"ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@example.com:443"
        b"?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dwww.bing.com#SS%2001\n"
    )
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "http", "obfs-host": "www.bing.com"}


def test_vless_share_link() -> None:
    """测试解析 vless share-link。

    Test parsing vless share-link.
    """
    body = b"vless://00000000-0000-0000-0000-000000000000@example.com:443?encryption=none&security=tls&sni=example.com#VL%2001\n"
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    assert result.records[0].data["type"] == "vless"
    assert result.records[0].data["uuid"] == "00000000-0000-0000-0000-000000000000"


def test_vless_share_link_maps_reality_and_transport_options() -> None:
    """测试 vless share-link 映射 reality 和传输选项。

    Test that vless share-link maps reality and transport options.
    """
    body = (
        b"vless://00000000-0000-0000-0000-000000000000@example.com:443"
        b"?encryption=none&security=reality&type=tcp&sni=example.com"
        b"&flow=xtls-rprx-vision&pbk=pubkey&sid=abcd&fp=chrome#VL%2001\n"
    )
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["network"] == "tcp"
    assert proxy["tls"] is True
    assert proxy["reality-opts"] == {"public-key": "pubkey", "short-id": "abcd"}
    assert proxy["client-fingerprint"] == "chrome"
    assert proxy["flow"] == "xtls-rprx-vision"


def test_trojan_share_link_maps_ws_options() -> None:
    """测试 trojan share-link 映射 WebSocket 选项。

    Test that trojan share-link maps WebSocket options.
    """
    body = (
        b"trojan://password@example.com:443"
        b"?type=ws&sni=example.com&host=cdn.example.com&path=%2Fws#TR%2001\n"
    )
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["network"] == "ws"
    assert proxy["ws-opts"] == {"path": "/ws", "headers": {"Host": "cdn.example.com"}}


def test_hysteria2_share_link() -> None:
    """测试解析 hysteria2 share-link。

    Test parsing hysteria2 share-link.
    """
    body = b"hysteria2://password@example.com:443?sni=example.com#HY2%2001\n"
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    assert result.records[0].data["type"] == "hysteria2"
    assert result.records[0].data["password"] == "password"


def test_base64_share_links() -> None:
    """测试解析 base64 编码的 share-links。

    Test parsing base64-encoded share-links.
    """
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

    result = parse_subscription(
        encoded, source="airport_a", fmt="auto", parse_error="fail"
    )

    assert result.records[0].data["type"] == "vmess"
    assert result.records[0].data["name"] == "VM 01"


def test_parse_error_skip_bad_nodes() -> None:
    """测试 parse_error=skip 时跳过坏节点。

    Test that bad nodes are skipped when parse_error=skip.
    """
    body = b"not-a-node\ntrojan://password@example.com:443#TR%2001\n"
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="skip"
    )

    assert len(result.records) == 1
    assert result.warnings


def test_parse_error_fail_bad_nodes() -> None:
    """测试 parse_error=fail 时对坏节点抛出异常。

    Test that bad nodes raise an error when parse_error=fail.
    """
    with pytest.raises(ParseError):
        parse_subscription(
            b"not-a-node\n", source="airport_a", fmt="share-links", parse_error="fail"
        )


def test_share_links_rejects_non_utf8_body() -> None:
    """测试 share-links 解析拒绝非 UTF-8 内容。

    Test that share-links parsing rejects non-UTF-8 content.
    """
    with pytest.raises(ParseError, match="UTF-8"):
        parse_subscription(
            b"\xff\xfe\xfd", source="airport_a", fmt="share-links", parse_error="fail"
        )


def test_auto_rejects_invalid_base64() -> None:
    """测试 auto 模式拒绝无效的 base64 内容。

    Test that auto mode rejects invalid base64 content.
    """
    with pytest.raises(ParseError, match="base64"):
        parse_subscription(
            b"not-base64!!!", source="airport_a", fmt="auto", parse_error="fail"
        )


def test_auto_includes_yaml_error_when_all_formats_fail() -> None:
    """测试 auto 模式在所有格式失败时包含 YAML 错误信息。

    Test that auto mode includes YAML error when all formats fail.
    """
    with pytest.raises(
        ParseError, match="YAML was not a valid subscription"
    ) as exc_info:
        parse_subscription(b"{", source="airport_a", fmt="auto", parse_error="skip")

    assert "base64" in str(exc_info.value)


def test_parse_yaml_rejects_non_utf8_body() -> None:
    """测试 YAML 解析拒绝非 UTF-8 内容。

    Test that YAML parsing rejects non-UTF-8 content.
    """
    with pytest.raises(ParseError, match="failed to parse YAML"):
        parse_subscription(
            b"\xff\xfe\xfd", source="airport_a", fmt="yaml", parse_error="fail"
        )


def test_auto_yaml_validation_fail_does_not_fallback() -> None:
    """测试 auto 模式下 YAML 验证失败不会回退到其他格式。

    Test that YAML validation failure in auto mode does not fallback.
    """
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


@pytest.mark.parametrize("short_id", ["123", "xyz", "0b7caf92d4ffffff00"])
def test_yaml_drops_invalid_reality_short_id(short_id: str) -> None:
    """测试非法 Reality short-id 节点被跳过 / Test invalid Reality short-id nodes are skipped."""
    body = f"""
proxies:
  - name: bad reality
    type: vless
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    reality-opts:
      public-key: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
      short-id: "{short_id}"
  - name: good
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
""".encode()
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="skip"
    )

    assert [record.data["name"] for record in result.records] == ["good"]
    assert any(
        "short-id" in warning and "hex" in warning for warning in result.warnings
    )


def test_yaml_drops_unsupported_proxy_type() -> None:
    """测试不支持的代理类型被跳过 / Test unsupported proxy types are skipped."""
    body = b"""
proxies:
  - name: bad
    type: mystery
    server: example.com
    port: 443
  - name: good
    type: ss
    server: example.com
    port: 443
    cipher: chacha20-ietf-poly1305
    password: secret
"""
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="skip"
    )

    assert [record.data["name"] for record in result.records] == ["good"]
    assert any("unsupported proxy type" in warning for warning in result.warnings)


def test_yaml_repairs_coercible_scalar_and_nested_types() -> None:
    """测试可修复字段会转为 Mihomo schema 类型 / Test coercible fields normalize to Mihomo schema types."""
    body = b"""
proxies:
  - name: repaired
    type: vmess
    server: example.com
    port: "443"
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
    tls: "true"
    alpn: h2,http/1.1
    ws-opts:
      path: 123
      headers:
        Host: 456
      max-early-data: "2048"
    grpc-opts:
      grpc-service-name: 789
      ping-interval: "30"
"""
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="skip"
    )

    assert result.warnings == []
    proxy = result.records[0].data
    assert proxy["port"] == 443
    assert proxy["tls"] is True
    assert proxy["alpn"] == ["h2", "http/1.1"]
    assert proxy["ws-opts"] == {
        "path": "123",
        "headers": {"Host": "456"},
        "max-early-data": 2048,
    }
    assert proxy["grpc-opts"] == {
        "grpc-service-name": "789",
        "ping-interval": 30,
    }


def test_yaml_drops_unrepairable_nested_type() -> None:
    """测试不可修复嵌套字段会丢弃节点 / Test unrepairable nested fields drop the node."""
    body = b"""
proxies:
  - name: bad
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
    ws-opts:
      headers: not-a-map
  - name: good
    type: direct
"""
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="skip"
    )

    assert [record.data["name"] for record in result.records] == ["good"]
    assert any(
        "ws-opts.headers" in warning and "map" in warning for warning in result.warnings
    )


def test_yaml_preserves_unknown_fields_without_warning() -> None:
    """测试 Mihomo 未声明字段原样保留 / Test unknown fields are preserved."""
    body = b"""
proxies:
  - name: kept
    type: vless
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    not-a-mihomo-field: value
    ws-opts:
      path: /ws
      not-a-nested-field: nested-value
"""
    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="fail"
    )

    assert result.warnings == []
    proxy = result.records[0].data
    assert proxy["not-a-mihomo-field"] == "value"
    assert proxy["ws-opts"]["not-a-nested-field"] == "nested-value"


def _schema_value(kind: SchemaValue) -> Any:
    if isinstance(kind, dict):
        if "*" in kind:
            return [_schema_value(kind["*"])]
        return {field: _schema_value(child) for field, child in kind.items()}
    if kind == "string":
        return "text"
    if kind == "number":
        return "123"
    if kind == "bool":
        return 1
    if kind == "string-list":
        return "h2,http/1.1"
    if kind == "number-list":
        return ["1", 2]
    if kind == "string-map":
        return {"Host": 123}
    if kind == "string-list-map":
        return {"Host": "a,b"}
    if kind == "any-map":
        return {"raw": {"kept": True}}
    raise AssertionError(f"unhandled schema kind {kind!r}")


def _max_proxy(proxy_type: str) -> dict[str, Any]:
    schema = {**COMMON_PROXY_FIELDS, **PROXY_SCHEMAS[proxy_type]}
    proxy = {field: _schema_value(kind) for field, kind in schema.items()}
    proxy["type"] = proxy_type
    proxy["name"] = f"max-{proxy_type}"
    if "server" in schema:
        proxy["server"] = "example.com"
    if "port" in schema:
        proxy["port"] = "443"
    if "uuid" in schema:
        proxy["uuid"] = "00000000-0000-0000-0000-000000000000"
    if proxy_type == "wireguard":
        proxy["ip"] = "172.16.0.2/32"
    if "private-key" in schema:
        proxy["private-key"] = "private-key"
    if "public-key" in schema:
        proxy["public-key"] = "public-key"
    if "reality-opts" in schema:
        proxy["reality-opts"] = {
            "public-key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "short-id": "0a",
            "support-x25519mlkem768": 1,
            "unknown-reality": "kept",
        }
    if proxy_type == "ss":
        proxy["cipher"] = "chacha20-ietf-poly1305"
    if proxy_type == "ssr":
        proxy["cipher"] = "aes-256-cfb"
        proxy["obfs"] = "plain"
        proxy["protocol"] = "origin"
    if proxy_type == "vmess":
        proxy["cipher"] = "auto"
    if proxy_type == "hysteria":
        proxy["up"] = "10 Mbps"
        proxy["down"] = "20 Mbps"
    if proxy_type == "mieru":
        proxy["transport"] = "TCP"
    if proxy_type == "openvpn":
        proxy["ca"] = "-----BEGIN CERTIFICATE-----"
    proxy["unknown-top-level"] = "kept"
    return proxy


@pytest.mark.parametrize("proxy_type", sorted(PROXY_SCHEMAS))
def test_yaml_accepts_max_supported_config_for_every_mihomo_proxy(
    proxy_type: str,
) -> None:
    """测试每种 Mihomo 协议最大字段配置都支持 / Test max config for every Mihomo proxy."""
    payload = {"proxies": [_max_proxy(proxy_type)]}
    body = yaml_dump(payload)

    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="fail"
    )

    proxy = result.records[0].data
    assert result.warnings == []
    assert proxy["type"] == proxy_type
    assert proxy["unknown-top-level"] == "kept"
    if "port" in PROXY_SCHEMAS[proxy_type]:
        assert proxy["port"] == 443


def yaml_dump(payload: dict[str, Any]) -> bytes:
    import yaml

    return yaml.safe_dump(payload, allow_unicode=True).encode()


def _mutate_first_kind(value: Any, kind: SchemaValue, target: str, new: Any) -> bool:
    if kind == target:
        return True
    if not isinstance(kind, dict):
        return False
    if "*" in kind:
        if isinstance(value, list) and value:
            child = kind["*"]
            if child == target:
                cast(list[Any], value)[0] = new
                return True
            return _mutate_first_kind(value[0], child, target, new)
        return False
    if not isinstance(value, dict):
        return False
    value_map = cast(dict[str, Any], value)
    for field, child in kind.items():
        if field not in value_map:
            continue
        if child == target:
            value_map[field] = new
            return True
        if _mutate_first_kind(value_map[field], child, target, new):
            return True
    return False


def _set_first_schema_kind(proxy: dict[str, Any], target: str, new: Any) -> bool:
    schema = {**COMMON_PROXY_FIELDS, **PROXY_SCHEMAS[str(proxy["type"])]}
    for field, kind in schema.items():
        if field not in proxy:
            continue
        if kind == target:
            proxy[field] = new
            return True
        if _mutate_first_kind(proxy[field], kind, target, new):
            return True
    return False


ProxyVariant = tuple[str, Callable[[dict[str, Any]], object]]


def _add_unknown_nested(proxy: dict[str, Any]) -> None:
    ws_opts = proxy.get("ws-opts")
    if isinstance(ws_opts, dict):
        cast(dict[str, Any], ws_opts)["unknown-ws"] = "kept"
        return
    smux = proxy.setdefault("smux", {})
    assert isinstance(smux, dict)
    cast(dict[str, Any], smux)["unknown-smux"] = "kept"


def _set_csv_list(proxy: dict[str, Any]) -> None:
    if not _set_first_schema_kind(proxy, "string-list", "h2,http/1.1"):
        proxy["unknown-list"] = ["a", "b"]


def _set_string_map_numbers(proxy: dict[str, Any]) -> None:
    if not _set_first_schema_kind(proxy, "string-map", {"Host": 123}):
        proxy["unknown-map"] = {"Host": 123}


def _set_string_list_map_csv(proxy: dict[str, Any]) -> None:
    if not _set_first_schema_kind(proxy, "string-list-map", {"Host": "a,b"}):
        proxy["unknown-list-map"] = {"Host": "a,b"}


def _set_any_map_nested(proxy: dict[str, Any]) -> None:
    if not _set_first_schema_kind(proxy, "any-map", {"raw": {"kept": True}}):
        proxy["unknown-any"] = {"raw": {"kept": True}}


PROXY_VARIANTS: tuple[ProxyVariant, ...] = (
    ("max", lambda proxy: None),
    ("routing_mark_decimal_string", lambda proxy: proxy.update({"routing-mark": "42"})),
    ("routing_mark_hex_string", lambda proxy: proxy.update({"routing-mark": "0x10"})),
    ("common_bool_ints", lambda proxy: proxy.update({"tfo": 1, "mptcp": 0})),
    ("common_bool_texts", lambda proxy: proxy.update({"tfo": "yes", "mptcp": "off"})),
    (
        "common_string_numbers",
        lambda proxy: proxy.update({"interface-name": 123, "dialer-proxy": 456}),
    ),
    ("unknown_top_scalar", lambda proxy: proxy.update({"unknown-top-level": "kept"})),
    ("unknown_top_map", lambda proxy: proxy.update({"unknown-top-map": {"a": "b"}})),
    ("unknown_top_list", lambda proxy: proxy.update({"unknown-top-list": ["a", "b"]})),
    ("unknown_nested", _add_unknown_nested),
    (
        "smux_number_strings",
        lambda proxy: proxy.update(
            {
                "smux": {
                    "enabled": 1,
                    "max-connections": "8",
                    "min-streams": "1",
                    "max-streams": "16",
                }
            }
        ),
    ),
    (
        "smux_brutal",
        lambda proxy: proxy.update(
            {
                "smux": {
                    "enabled": 1,
                    "brutal-opts": {"enabled": 1, "up": 100, "down": 200},
                }
            }
        ),
    ),
    (
        "first_string_numeric",
        lambda proxy: _set_first_schema_kind(proxy, "string", 123),
    ),
    (
        "first_number_string",
        lambda proxy: _set_first_schema_kind(proxy, "number", "321"),
    ),
    (
        "first_number_float",
        lambda proxy: _set_first_schema_kind(proxy, "number", 321.0),
    ),
    ("first_bool_int", lambda proxy: _set_first_schema_kind(proxy, "bool", 1)),
    ("first_bool_text", lambda proxy: _set_first_schema_kind(proxy, "bool", "true")),
    ("first_string_list_csv", _set_csv_list),
    (
        "first_number_list_strings",
        lambda proxy: _set_first_schema_kind(proxy, "number-list", ["1", "2"]),
    ),
    ("first_string_map_numbers", _set_string_map_numbers),
    ("first_string_list_map_csv", _set_string_list_map_csv),
    ("first_any_map_nested", _set_any_map_nested),
    ("name_numeric_string", lambda proxy: proxy.update({"name": 1001})),
    (
        "server_numeric_string",
        lambda proxy: proxy.update({"server": 12345}) if "server" in proxy else None,
    ),
    (
        "port_string",
        lambda proxy: proxy.update({"port": "443"}) if "port" in proxy else None,
    ),
)


MATRIX_CASES = [
    pytest.param(proxy_type, variant_name, id=f"{proxy_type}-{variant_name}")
    for proxy_type in sorted(PROXY_SCHEMAS)
    for variant_name, _ in PROXY_VARIANTS
]

FUZZ_CASES = [
    pytest.param(proxy_type, seed, id=f"{proxy_type}-seed-{seed}")
    for proxy_type in sorted(PROXY_SCHEMAS)
    for seed in range(5)
]


@pytest.mark.parametrize(("proxy_type", "variant_name"), MATRIX_CASES)
def test_yaml_accepts_max_config_variants_for_every_mihomo_proxy(
    proxy_type: str, variant_name: str
) -> None:
    """测试所有 Mihomo 协议的大配置变体 / Test max-config variants for all Mihomo proxies."""
    variant = dict(PROXY_VARIANTS)[variant_name]
    proxy = deepcopy(_max_proxy(proxy_type))
    variant(proxy)
    body = yaml_dump({"proxies": [proxy]})

    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="fail"
    )

    normalized = result.records[0].data
    assert result.warnings == []
    assert normalized["type"] == proxy_type
    if "unknown-top-level" in proxy:
        assert normalized["unknown-top-level"] == proxy["unknown-top-level"]


def _fuzz_known_value(kind: SchemaValue, rng: random.Random) -> Any:
    if isinstance(kind, dict):
        value = _schema_value(kind)
        if isinstance(value, dict):
            cast(dict[str, Any], value)[f"unknown-{rng.randrange(1000)}"] = rng.choice(
                ["yes", "00123", {"nested": "kept"}, ["a", "b"]]
            )
        return value
    if kind == "string":
        return rng.choice(["00123", 123, True, "null", "off"])
    if kind == "number":
        return rng.choice(["123", "0x10", 123, 123.0])
    if kind == "bool":
        return rng.choice([True, False, 0, 1, "true", "false", "yes", "off"])
    if kind == "string-list":
        return rng.choice([["h2", 123], "h2,http/1.1"])
    if kind == "number-list":
        return rng.choice([[1, "2", "0x3"], [1.0, 2]])
    if kind == "string-map":
        return {"Host": rng.choice([123, "example.com", False])}
    if kind == "string-list-map":
        return {"Host": rng.choice(["a,b", ["a", 123]])}
    if kind == "any-map":
        return {"raw": rng.choice([{"kept": True}, ["a", "b"], "text"])}
    raise AssertionError(f"unhandled schema kind {kind!r}")


def _fuzz_proxy(proxy_type: str, seed: int) -> dict[str, Any]:
    rng = random.Random(f"{proxy_type}:{seed}")
    proxy = deepcopy(_max_proxy(proxy_type))
    schema = {**COMMON_PROXY_FIELDS, **PROXY_SCHEMAS[proxy_type]}
    fields = list(schema)
    rng.shuffle(fields)
    for field in fields[: max(1, min(8, len(fields)))]:
        proxy[field] = _fuzz_known_value(schema[field], rng)
    proxy["type"] = proxy_type
    proxy["name"] = f"fuzz-{proxy_type}-{seed}"
    if "server" in schema:
        proxy["server"] = rng.choice(["example.com", 12345])
    if "port" in schema:
        proxy["port"] = rng.choice(["443", "0x1bb", 443])
    if "uuid" in schema:
        proxy["uuid"] = "00000000-0000-0000-0000-000000000000"
    if proxy_type == "wireguard":
        proxy["ip"] = "172.16.0.2/32"
    if "private-key" in schema:
        proxy["private-key"] = "private-key"
    if "public-key" in schema:
        proxy["public-key"] = "public-key"
    if "reality-opts" in schema:
        proxy["reality-opts"] = {
            "public-key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "short-id": rng.choice(["", "0a", "0b7caf92d4"]),
            "unknown-reality": {"kept": True},
        }
    proxy[f"unknown-fuzz-{seed}"] = rng.choice(
        ["00123", False, {"nested": ["kept", 1]}, ["x", "y"]]
    )
    return proxy


@pytest.mark.parametrize(("proxy_type", "seed"), FUZZ_CASES)
def test_yaml_fuzzes_repairable_configs_for_every_mihomo_proxy(
    proxy_type: str, seed: int
) -> None:
    """测试所有协议可修复随机变体 / Test repairable fuzz variants for every proxy."""
    proxy = _fuzz_proxy(proxy_type, seed)
    body = yaml_dump({"proxies": [proxy]})

    result = parse_subscription(
        body, source="airport_a", fmt="yaml", parse_error="fail"
    )

    normalized = result.records[0].data
    assert result.warnings == []
    assert normalized["type"] == proxy_type
    assert normalized[f"unknown-fuzz-{seed}"] == proxy[f"unknown-fuzz-{seed}"]


def test_yaml_schema_validation_fail_raises_when_parse_error_fail() -> None:
    """测试 parse_error=fail 时 schema 验证错误会抛出 / Test schema validation raises in fail mode."""
    body = b"""
proxies:
  - name: bad
    type: vless
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    reality-opts:
      public-key: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
      short-id: xyz
"""
    with pytest.raises(ParseError, match="short-id"):
        parse_subscription(body, source="airport_a", fmt="yaml", parse_error="fail")


def test_legacy_ss_link_with_ipv6_endpoint() -> None:
    """测试解析带有 IPv6 端点的旧版 SS 链接。

    Test parsing legacy SS link with IPv6 endpoint.
    """
    payload = base64.urlsafe_b64encode(
        b"chacha20-ietf-poly1305:secret@[2001:db8::1]:443"
    ).decode()
    link = f"ss://{payload}#SS%20IPv6"
    proxy = _parse_ss(link)

    assert proxy["server"] == "2001:db8::1"
    assert proxy["port"] == 443
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"


def test_share_links_warning_redacts_exception_detail() -> None:
    """测试 share-links 解析警告对异常详情进行脱敏。

    Test that share-links parsing redacts exception details in warnings.
    """
    # A vmess link with an invalid base64 payload produces an exception message that
    # could contain the raw link/token. The warning must redact/truncate it.
    body = b"vmess://secret-token-that-should-not-leak\n"
    with pytest.raises(ParseError) as exc_info:
        parse_subscription(
            body, source="airport_a", fmt="share-links", parse_error="skip"
        )

    message = str(exc_info.value)
    assert "secret-token-that-should-not-leak" not in message
    assert "failed to parse share link" in message


def test_legacy_ss_link_with_plugin_query() -> None:
    """测试解析带有插件查询参数的旧版 SS 链接。

    Test parsing legacy SS link with plugin query parameters.
    """
    payload = base64.urlsafe_b64encode(
        b"chacha20-ietf-poly1305:secret@example.com:443"
    ).decode()
    body = (
        f"ss://{payload}"
        "?plugin=obfs-local%3Bobfs%3Dhttp%3Bobfs-host%3Dwww.bing.com#SS%2001"
    ).encode()
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"
    assert proxy["server"] == "example.com"
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "http", "obfs-host": "www.bing.com"}


def test_parse_ss_legacy_with_query_directly() -> None:
    """测试直接解析带有查询参数的旧版 SS 链接。

    Test parsing legacy SS link with query parameters directly.
    """
    payload = base64.urlsafe_b64encode(b"aes-256-gcm:pass@192.0.2.1:8388").decode()
    link = f"ss://{payload}?plugin=obfs-local%3Bobfs%3Dtls#Legacy"
    proxy = _parse_ss(link)
    assert proxy["plugin"] == "obfs-local"
    assert proxy["plugin-opts"] == {"obfs": "tls"}


def test_urlsafe_base64_share_links() -> None:
    """测试解析 URL-safe base64 编码的 share-links。

    Test parsing URL-safe base64 encoded share-links.
    """
    vmess = {
        "v": "2",
        "ps": "VM 02",
        "add": "example.com",
        "port": "443",
        "id": "00000000-0000-0000-0000-000000000000",
        "aid": "0",
        "scy": "auto",
        "tls": "tls",
    }
    link = "vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode()
    encoded = base64.urlsafe_b64encode(link.encode())

    result = parse_subscription(
        encoded, source="airport_a", fmt="auto", parse_error="fail"
    )

    assert result.records[0].data["type"] == "vmess"
    assert result.records[0].data["name"] == "VM 02"


def test_ss_legacy_share_link_falls_back_to_standard_base64() -> None:
    """测试 SS 旧版 share-link 回退到标准 base64 解码。

    Test that SS legacy share-link falls back to standard base64 decoding.
    """
    payload = base64.b64encode(b"aes-256-gcm:???@example.com:443").decode()
    assert "+" in payload or "/" in payload
    body = f"ss://{payload}#SS%2001".encode()
    result = parse_subscription(
        body, source="airport_a", fmt="share-links", parse_error="fail"
    )

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "aes-256-gcm"
    assert proxy["password"] == "???"
    assert proxy["server"] == "example.com"
