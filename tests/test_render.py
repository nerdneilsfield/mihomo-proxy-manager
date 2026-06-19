"""YAML 渲染器测试，包括重命名、去重和元注释。

YAML renderer tests including renaming, deduplication, and meta comments.
"""

import base64
import json
from urllib.parse import unquote

import yaml

from mihomo_proxy_manager.models import (
    ProxyRecord,
    RenameConfig,
    FilterConfig,
    RouteConfig,
    RouteOutputConfig,
)
from mihomo_proxy_manager.render import ProviderRenderer, XrayUriRenderer
from mihomo_proxy_manager.render import (
    RenderRequest,
    build_renderer_registry,
    prepare_render_records,
)

REALITY_PUBLIC_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def route(
    include_meta_comments: bool = False,
    output: RouteOutputConfig | None = None,
) -> RouteConfig:
    """创建测试用路由配置。

    Create a route config for testing.

    Args:
        include_meta_comments: 是否包含元注释 / Whether to include meta comments.

    Returns:
        RouteConfig: 路由配置对象 / Route config object.
    """
    return RouteConfig(
        name="phone",
        path="/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
        sources=("airport_a",),
        require_all_sources=False,
        output=output
        or RouteOutputConfig(
            format="provider", include_meta_comments=include_meta_comments
        ),
        rename=RenameConfig(prefix="[phone] "),
        filter=FilterConfig(),
    )


def xray_route(encoding: str = "base64") -> RouteConfig:
    """Create a route for xray-uri renderer tests."""
    return RouteConfig(
        name="xray",
        path="/xray",
        sources=("airport_a",),
        require_all_sources=False,
        output=RouteOutputConfig(format="xray-uri", encoding=encoding),
        rename=RenameConfig(),
        filter=FilterConfig(),
    )


def quantumult_x_route(
    *,
    import_response: str = "redirect",
    import_target: str = "app-scheme",
    resource_tag: str | None = None,
) -> RouteConfig:
    """Create a route for quantumult-x renderer tests."""
    return RouteConfig(
        name="qx",
        path="/qx",
        sources=("airport_a",),
        require_all_sources=False,
        output=RouteOutputConfig(
            format="quantumult-x",
            import_response=import_response,
            import_target=import_target,
            resource_tag=resource_tag,
        ),
        rename=RenameConfig(),
        filter=FilterConfig(),
    )


def test_provider_renderer_preserves_fields_and_strips_internal_metadata() -> None:
    """测试渲染器保留字段并移除内部元数据 / Test that the renderer preserves fields and strips internal metadata."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [
            ProxyRecord(
                "airport_a",
                {
                    "name": "HK:01",
                    "type": "vmess",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "cipher": "auto",
                },
            )
        ],
    )

    loaded = yaml.safe_load(body)
    proxy = loaded["proxies"][0]
    assert proxy["name"] == "[phone] HK:01"
    assert proxy["server"] == "example.com"
    assert "source" not in proxy


def test_provider_renderer_repairs_duplicate_names() -> None:
    """测试渲染器修复重复的名称 / Test that the renderer repairs duplicate names."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [
            ProxyRecord("a", {"name": "HK", "type": "direct"}),
            ProxyRecord("b", {"name": "HK", "type": "direct"}),
            ProxyRecord("c", {"name": "HK #2", "type": "direct"}),
        ],
    )

    names = [item["name"] for item in yaml.safe_load(body)["proxies"]]
    assert names == ["[phone] HK", "[phone] HK #3", "[phone] HK #2"]


def test_provider_renderer_includes_sources_in_meta_comments() -> None:
    """测试渲染器在元注释中包含源信息 / Test that the renderer includes source info in meta comments."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(include_meta_comments=True),
        [ProxyRecord("airport_a", {"name": "HK", "type": "direct"})],
    )

    text = body.decode("utf-8")
    assert "# sources: 1" in text
    assert "# nodes: 1" in text
    assert "# route: phone" in text


def test_provider_renderer_quotes_string_identity_fields() -> None:
    """测试身份、凭据、域名和路径字段使用双引号渲染 / Test identity, secret, host, and path fields render with double quotes."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [
            ProxyRecord(
                "airport_a",
                {
                    "name": "HK",
                    "type": "vless",
                    "server": "1.2.3.4",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "tls": True,
                    "servername": "github.com",
                    "client-fingerprint": "chrome",
                    "flow": "xtls-rprx-vision",
                    "reality-opts": {
                        "public-key": REALITY_PUBLIC_KEY,
                        "short-id": "0a1b2c3d",
                    },
                    "ws-opts": {
                        "path": "/ray",
                        "headers": {"Host": "example.com"},
                    },
                    "grpc-opts": {"grpc-service-name": "svc"},
                },
            ),
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS",
                    "type": "ss",
                    "server": "example.net",
                    "port": 8388,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "secret",
                },
            ),
        ],
    )

    text = body.decode("utf-8")
    assert 'name: "[phone] HK"' in text
    assert 'server: "1.2.3.4"' in text
    assert "port: 443" in text
    assert 'uuid: "00000000-0000-0000-0000-000000000000"' in text
    assert 'cipher: "chacha20-ietf-poly1305"' in text
    assert "tls: true" in text
    assert 'servername: "github.com"' in text
    assert 'client-fingerprint: "chrome"' in text
    assert 'flow: "xtls-rprx-vision"' in text
    assert f'public-key: "{REALITY_PUBLIC_KEY}"' in text
    assert 'short-id: "0a1b2c3d"' in text
    assert 'path: "/ray"' in text
    assert 'Host: "example.com"' in text
    assert 'grpc-service-name: "svc"' in text


def test_provider_renderer_normalizes_and_drops_invalid_records() -> None:
    """测试渲染前按 Mihomo schema 修复并丢弃坏节点 / Test render normalizes and drops invalid nodes."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [
            ProxyRecord(
                "airport_a",
                {
                    "name": "kept",
                    "type": "vless",
                    "server": "example.com",
                    "port": "443",
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "tls": "true",
                    "reality-opts": {
                        "public-key": REALITY_PUBLIC_KEY,
                        "short-id": "0b7caf92d4",
                    },
                },
            ),
            ProxyRecord(
                "airport_a",
                {
                    "name": "dropped",
                    "type": "vless",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "reality-opts": {
                        "public-key": REALITY_PUBLIC_KEY,
                        "short-id": "xyz",
                    },
                },
            ),
        ],
    )

    loaded = yaml.safe_load(body)
    assert len(loaded["proxies"]) == 1
    proxy = loaded["proxies"][0]
    assert proxy["name"] == "[phone] kept"
    assert proxy["port"] == 443
    assert proxy["tls"] is True
    text = body.decode("utf-8")
    assert 'short-id: "0b7caf92d4"' in text


def test_provider_route_renderer_matches_provider_renderer_bytes() -> None:
    """测试渲染器注册表的 provider 适配器保持现有字节输出 / Test provider adapter keeps existing bytes."""
    test_route = route()
    records = [ProxyRecord("airport_a", {"name": "HK", "type": "direct"})]

    registry = build_renderer_registry()
    response = registry["provider"].render(RenderRequest(test_route, records))

    assert "provider" in registry
    assert response.body == ProviderRenderer().render_sync(test_route, records)
    assert response.media_type == "application/yaml; charset=utf-8"
    assert response.headers == {}


def test_xray_uri_renderer_base64_subscription_defaults_to_base64() -> None:
    """测试 xray-uri 默认 base64 订阅 / Test xray-uri default base64 subscription."""
    records = [
        ProxyRecord(
            "airport_a",
            {
                "name": "SS 01",
                "type": "ss",
                "server": "example.net",
                "port": 8388,
                "cipher": "chacha20-ietf-poly1305",
                "password": "secret",
            },
        ),
        ProxyRecord(
            "airport_a",
            {
                "name": "VLESS 01",
                "type": "vless",
                "server": "example.com",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000000",
                "tls": True,
                "servername": "example.com",
            },
        ),
    ]

    response = build_renderer_registry()["xray-uri"].render(
        RenderRequest(xray_route(), records)
    )

    decoded = base64.urlsafe_b64decode(response.body).decode("utf-8")
    assert response.media_type == "text/plain; charset=utf-8"
    assert response.status_code == 200
    assert "ss://" in decoded
    assert "vless://00000000-0000-0000-0000-000000000000@example.com:443" in decoded
    assert "#VLESS%2001" in decoded


def test_xray_uri_renderer_plain_trojan_query() -> None:
    """测试 xray-uri plain trojan 输出 / Test xray-uri plain trojan output."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "example.com",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert text.startswith("trojan://secret@example.com:443?")
    assert "sni=example.com" in text
    assert text.endswith("#Trojan%2001\n")
    assert response.media_type == "text/plain; charset=utf-8"


def test_xray_uri_renderer_allows_shadowsocks_plugin_fields() -> None:
    """Test xray-uri does not apply Quantumult X-only SS plugin guard."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS Plugin",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                        "plugin": "obfs",
                        "plugin-opts": {"mode": "tls"},
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert text.startswith("ss://")
    assert not any(
        "unsupported Shadowsocks field" in warning
        for warning in response.warnings
    )


def test_xray_uri_renderer_preserves_vless_client_fingerprint() -> None:
    """测试 VLESS client-fingerprint 映射为 fp / Test VLESS client-fingerprint maps to fp."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS Chrome",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "client-fingerprint": "chrome",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert response.warnings == ()
    assert "fp=chrome" in text


def test_xray_uri_renderer_escapes_userinfo_fragment_and_brackets_ipv6() -> None:
    """测试 xray-uri 转义 userinfo、fragment，并为 IPv6 加括号。"""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": '名字 #%,"/',
                        "type": "trojan",
                        "server": "2001:db8::1",
                        "port": 443,
                        "password": "p#%,\"/",
                    },
                )
            ],
        )
    )

    assert (
        response.body.decode("utf-8")
        == "trojan://p%23%25%2C%22%2F@[2001:db8::1]:443#%E5%90%8D%E5%AD%97%20%23%25%2C%22%2F\n"
    )


def test_xray_uri_renderer_rejects_unmapped_security_critical_fields() -> None:
    """测试未映射安全关键字段会被拒绝 / Test unmapped security-critical fields are rejected."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Reality",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0a1b2c3d",
                        },
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.media_type == "text/plain; charset=utf-8"
    assert response.body == b"no supported nodes for xray-uri output"
    assert any("reality-opts" in warning for warning in response.warnings)


def test_xray_uri_renderer_rejects_certificate_pinning_like_fields() -> None:
    """测试证书钉扎类字段会被拒绝 / Test certificate pinning-like fields are rejected."""
    for field_name in ("fingerprint", "certificate"):
        response = XrayUriRenderer().render(
            RenderRequest(
                xray_route(encoding="plain"),
                [
                    ProxyRecord(
                        "airport_a",
                        {
                            "name": f"Unsafe {field_name}",
                            "type": "vless",
                            "server": "example.com",
                            "port": 443,
                            "uuid": "00000000-0000-0000-0000-000000000000",
                            "tls": True,
                            field_name: "sha256/example",
                        },
                    )
                ],
            )
        )

        assert response.status_code == 422
        assert response.body == b"no supported nodes for xray-uri output"
        assert any(field_name in warning for warning in response.warnings)


def test_quantumult_x_renderer_outputs_server_lines() -> None:
    """Test quantumult-x server_remote lines for SS and Trojan nodes."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "example.com",
                        "skip-cert-verify": False,
                    },
                ),
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.media_type == "text/plain; charset=utf-8"
    assert (
        "shadowsocks=example.com:443, method=chacha20-ietf-poly1305, "
        "password=password, tag=SS 01"
    ) in text
    assert (
        "trojan=example.com:443, password=secret, over-tls=true, "
        "tls-host=example.com, tls-verification=true, tag=Trojan 01"
    ) in text
    assert response.status_code == 200


def test_quantumult_x_renderer_sanitizes_node_tag() -> None:
    """Test quantumult-x server line tag avoids comma/control characters."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HK, 01\n",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    tag_segment = text.split("tag=", 1)[1]
    assert tag_segment == "HK 01\n"
    assert "HK, 01" not in tag_segment
    assert "\n" not in tag_segment.rstrip("\n")


def test_quantumult_x_vless_tcp_tls_uses_obfs_over_tls() -> None:
    """Test VLESS TCP TLS maps to Quantumult X obfs=over-tls."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS TLS",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "servername": "example.com",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert "obfs=over-tls" in text
    assert "obfs-host=example.com" in text
    assert "over-tls=true" not in text


def test_quantumult_x_vmess_ws_tls_uses_wss_obfs() -> None:
    """Test VMess WS TLS maps to Quantumult X obfs=wss."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess WSS",
                        "type": "vmess",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                        "tls": True,
                        "servername": "example.com",
                        "network": "ws",
                        "ws-opts": {
                            "path": "/ws",
                            "headers": {"Host": "example.com"},
                        },
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert "obfs=wss" in text
    assert "obfs-host=example.com" in text
    assert "obfs-uri=/ws" in text
    assert "over-tls=true" not in text


def test_quantumult_x_ss_tls_uses_tls_obfs() -> None:
    """Test Shadowsocks TLS maps to Quantumult X obfs=tls."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS TLS",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                        "tls": True,
                        "servername": "example.com",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert "obfs=tls" in text
    assert "obfs-host=example.com" in text


def test_quantumult_x_import_redirect_response() -> None:
    """Test quantumult-x app-scheme import redirect response."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(resource_tag="MPM"),
            [],
            main_public_url="https://mpm.example.com/qx",
            companion="import",
        )
    )

    assert response.status_code == 302
    assert response.body == b""
    assert response.headers["Location"].startswith(
        "quantumult-x:///add-resource?remote-resource="
    )
    encoded = response.headers["Location"].split("remote-resource=", 1)[1]
    payload = json.loads(unquote(encoded))
    assert payload == {
        "server_remote": [
            "https://mpm.example.com/qx, tag=MPM, update-interval=86400, "
            "enabled=true"
        ]
    }


def test_quantumult_x_import_sanitizes_comma_and_control_chars() -> None:
    """Test import resource tag avoids comma/control characters."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(resource_tag="MPM, Phones\n"),
            [],
            main_public_url="https://mpm.example.com/qx",
            companion="import",
        )
    )

    encoded = response.headers["Location"].split("remote-resource=", 1)[1]
    payload = json.loads(unquote(encoded))
    server_remote = payload["server_remote"][0]
    assert "tag=MPM Phones" in server_remote
    assert "MPM, Phones" not in server_remote
    assert "\n" not in server_remote


def test_quantumult_x_import_plain_universal_link_response() -> None:
    """Test quantumult-x universal-link import plain response."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(
                import_response="plain", import_target="universal-link"
            ),
            [],
            main_public_url="https://mpm.example.com/qx",
            companion="import",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert text.startswith(
        "https://quantumult.app/x/open-app/add-resource?remote-resource="
    )
    assert "remote-resource=" in text
    assert text.endswith("\n")


def test_quantumult_x_renderer_skips_unsupported_reality_node() -> None:
    """Test quantumult-x rejects unsupported Reality/flow fields."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Reality",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "reality-opts": {"public-key": REALITY_PUBLIC_KEY},
                        "flow": "xtls-rprx-vision",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.body == b"no supported nodes for quantumult-x output"
    assert response.warnings


def test_prepare_render_records_preserves_filtering_and_renaming() -> None:
    """测试共享准备流程保留过滤、重命名、标准化与去重 / Test shared preparation keeps filter, rename, normalize, and dedupe."""
    test_route = RouteConfig(
        name="phone",
        path="/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
        sources=("airport_a",),
        require_all_sources=False,
        output=RouteOutputConfig(format="provider"),
        rename=RenameConfig(prefix="[phone] "),
        filter=FilterConfig(include="HK"),
    )
    records = [
        ProxyRecord("airport_a", {"name": "HK", "type": "direct"}),
        ProxyRecord("airport_a", {"name": "HK", "type": "direct"}),
        ProxyRecord("airport_a", {"name": "TW", "type": "direct"}),
    ]

    proxies = prepare_render_records(test_route, records)

    assert [proxy["name"] for proxy in proxies] == ["[phone] HK", "[phone] HK #2"]
