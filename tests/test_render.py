"""YAML 渲染器测试，包括重命名、去重和元注释。

YAML renderer tests including renaming, deduplication, and meta comments.
"""

import base64
import json
from typing import Literal
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


def xray_route(encoding: Literal["base64", "plain"] = "base64") -> RouteConfig:
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
    import_response: Literal["redirect", "plain"] = "redirect",
    import_target: Literal["app-scheme", "universal-link"] = "app-scheme",
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


def surfboard_route() -> RouteConfig:
    """Create a route for surfboard renderer tests."""
    return RouteConfig(
        name="surfboard",
        path="/surfboard",
        sources=("airport_a",),
        require_all_sources=False,
        output=RouteOutputConfig(format="surfboard"),
        rename=RenameConfig(),
        filter=FilterConfig(),
    )


def surfboard_request(companion: str | None = None) -> RenderRequest:
    """Create a Surfboard render request with SS and VMess nodes."""
    return RenderRequest(
        surfboard_route(),
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
                    "name": "VMess 01",
                    "type": "vmess",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "cipher": "auto",
                    "tls": True,
                    "network": "ws",
                    "ws-opts": {
                        "path": "/ws",
                        "headers": {"Host": "example.com"},
                    },
                },
            ),
        ],
        companion_public_urls={"nodes": "https://mpm.example.com/surfboard-nodes"},
        companion=companion,
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


def test_xray_uri_renderer_hysteria2_uri() -> None:
    """Test xray-uri renders Hysteria2 URI scheme."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HY2 01",
                        "type": "hysteria2",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "real.example.com",
                        "skip-cert-verify": True,
                        "obfs": "salamander",
                        "obfs-password": "obfs-secret",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert text.startswith("hysteria2://secret@example.com:443/?")
    assert "sni=real.example.com" in text
    assert "insecure=1" in text
    assert "obfs=salamander" in text
    assert "obfs-password=obfs-secret" in text
    assert text.endswith("#HY2%2001\n")


def test_xray_uri_renderer_plain_includes_all_supported_protocols() -> None:
    """Test xray-uri emits every renderer-supported URI protocol."""
    response = XrayUriRenderer().render(
        RenderRequest(
            xray_route(encoding="plain"),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "ss.example.com",
                        "port": 8388,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "secret",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "trojan.example.com",
                        "port": 443,
                        "password": "secret",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS 01",
                        "type": "vless",
                        "server": "vless.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess 01",
                        "type": "vmess",
                        "server": "vmess.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HY2 01",
                        "type": "hysteria2",
                        "server": "hy2.example.com",
                        "port": 443,
                        "password": "secret",
                    },
                ),
            ],
        )
    )

    lines = response.body.decode("utf-8").splitlines()
    assert response.status_code == 200
    assert len(lines) == 5
    assert lines[0].startswith("ss://")
    assert lines[1].startswith("trojan://")
    assert lines[2].startswith("vless://")
    assert lines[3].startswith("vmess://")
    assert lines[4].startswith("hysteria2://")


def test_xray_uri_renderer_rejects_shadowsocks_plugin_fields() -> None:
    """Test xray-uri skips unsupported Shadowsocks SIP002 plugin fields."""
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

    assert response.status_code == 422
    assert response.body == b"no supported nodes for xray-uri output"
    assert any(
        "unsupported Shadowsocks field plugin" in warning
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
                        "password": 'p#%,"/',
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
                        "udp-relay": True,
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
                        "udp-relay": False,
                    },
                ),
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.media_type == "text/plain; charset=utf-8"
    assert (
        "shadowsocks=example.com:443, method=chacha20-ietf-poly1305, "
        "password=password, udp-relay=true, tag=SS 01"
    ) in text
    assert (
        "trojan=example.com:443, password=secret, over-tls=true, "
        "tls-host=example.com, tls-verification=true, "
        "udp-relay=false, tag=Trojan 01"
    ) in text
    assert response.status_code == 200


def test_quantumult_x_renderer_includes_all_supported_protocols() -> None:
    """Test Quantumult X emits every renderer-supported server protocol."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "ss.example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess 01",
                        "type": "vmess",
                        "server": "vmess.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS 01",
                        "type": "vless",
                        "server": "vless.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "trojan.example.com",
                        "port": 443,
                        "password": "secret",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP 01",
                        "type": "http",
                        "server": "http.example.com",
                        "port": 80,
                        "username": "user",
                        "password": "pass",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS5 01",
                        "type": "socks5",
                        "server": "socks5.example.com",
                        "port": 1080,
                        "username": "user",
                        "password": "pass",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS 01",
                        "type": "anytls",
                        "server": "anytls.example.com",
                        "port": 443,
                        "password": "secret",
                        "tls": True,
                    },
                ),
            ],
        )
    )

    lines = response.body.decode("utf-8").splitlines()
    assert response.status_code == 200
    assert len(lines) == 7
    assert lines[0].startswith("shadowsocks=ss.example.com:443")
    assert lines[1].startswith("vmess=vmess.example.com:443")
    assert lines[2].startswith("vless=vless.example.com:443")
    assert lines[3].startswith("trojan=trojan.example.com:443")
    assert lines[4].startswith("http=http.example.com:80")
    assert lines[5].startswith("socks5=socks5.example.com:1080")
    assert lines[6].startswith("anytls=anytls.example.com:443")


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


def test_quantumult_x_rejects_comma_delimited_scalar_values() -> None:
    """Test quantumult-x skips values requiring unsupported comma escaping."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS Comma",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "pa,ss",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.warnings
    assert any("password" in warning for warning in response.warnings)


def test_quantumult_x_rejects_ipv6_host_until_supported() -> None:
    """Test quantumult-x skips IPv6 hosts instead of rendering ambiguous hostport."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS IPv6",
                        "type": "ss",
                        "server": "2001:db8::1",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.warnings
    assert any("server" in warning for warning in response.warnings)


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


def test_quantumult_x_ss_tls_uses_over_tls_obfs() -> None:
    """Test Shadowsocks TLS maps to Quantumult X obfs=over-tls."""
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
    assert "obfs=over-tls" in text
    assert "obfs-host=example.com" in text


def test_quantumult_x_ss_simple_obfs_uses_documented_fields() -> None:
    """Test Shadowsocks simple-obfs maps to Quantumult X obfs fields."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS Obfs",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                        "obfs": "http",
                        "obfs-host": "apple.com",
                        "obfs-uri": "/resource/file",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert (
        "shadowsocks=example.com:443, method=chacha20-ietf-poly1305, "
        "password=password, obfs=http, obfs-host=apple.com, "
        "obfs-uri=/resource/file, tag=SS Obfs"
    ) in text


def test_quantumult_x_trojan_ws_uses_wss_obfs() -> None:
    """Test Trojan WebSocket maps to Quantumult X obfs=wss."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan WSS",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "network": "ws",
                        "ws-opts": {
                            "path": "/path",
                            "headers": {"Host": "example.com"},
                        },
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert "obfs=wss" in text
    assert "obfs-uri=/path" in text
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
            "https://mpm.example.com/qx, tag=MPM, update-interval=86400, enabled=true"
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
            quantumult_x_route(import_response="plain", import_target="universal-link"),
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


def test_quantumult_x_vless_reality_vision_uses_documented_fields() -> None:
    """Test QX VLESS Reality Vision maps to documented fields."""
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
                        "tls": True,
                        "servername": "apple.com",
                        "udp-relay": True,
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                        "flow": "xtls-rprx-vision",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "vless=example.com:443, method=none, "
        "password=00000000-0000-0000-0000-000000000000, "
        "obfs=over-tls, obfs-host=apple.com, "
        f"reality-base64-pubkey={REALITY_PUBLIC_KEY}, "
        "reality-hex-shortid=0123456789abcdef, "
        "vless-flow=xtls-rprx-vision, udp-relay=true, tag=Reality"
    ) in text


def test_quantumult_x_vless_reality_without_vision() -> None:
    """Test QX VLESS Reality can render without Vision flow."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Reality Only",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "reality-opts": {"public-key": REALITY_PUBLIC_KEY},
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert f"reality-base64-pubkey={REALITY_PUBLIC_KEY}" in text
    assert "vless-flow=" not in text


def test_quantumult_x_vmess_and_trojan_reality_use_documented_fields() -> None:
    """Test QX VMess and Trojan Reality fields are preserved."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess Reality",
                        "type": "vmess",
                        "server": "vmess.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                        "tls": True,
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan Reality",
                        "type": "trojan",
                        "server": "trojan.example.com",
                        "port": 443,
                        "password": "secret",
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                    },
                ),
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "vmess=vmess.example.com:443, method=none, "
        "password=00000000-0000-0000-0000-000000000000, "
        "obfs=over-tls, "
        f"reality-base64-pubkey={REALITY_PUBLIC_KEY}, "
        "reality-hex-shortid=0123456789abcdef, tag=VMess Reality"
    ) in text
    assert (
        "trojan=trojan.example.com:443, password=secret, over-tls=true, "
        "tls-host=trojan.example.com, "
        f"reality-base64-pubkey={REALITY_PUBLIC_KEY}, "
        "reality-hex-shortid=0123456789abcdef, tag=Trojan Reality"
    ) in text


def test_quantumult_x_rejects_reality_without_public_key() -> None:
    """Test QX skips malformed Reality options."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Bad Reality",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "reality-opts": {"short-id": "0123456789abcdef"},
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.body == b"no supported nodes for quantumult-x output"
    assert any("reality-opts.public-key" in warning for warning in response.warnings)


def test_quantumult_x_rejects_unsupported_vless_flow() -> None:
    """Test QX skips unsupported VLESS flow values."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Bad Flow",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "reality-opts": {"public-key": REALITY_PUBLIC_KEY},
                        "flow": "xtls-rprx-direct",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.body == b"no supported nodes for quantumult-x output"


def test_quantumult_x_http_plain_renders_documented_fields() -> None:
    """Test QX HTTP proxy emits documented fields without TLS."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP Plain",
                        "type": "http",
                        "server": "example.com",
                        "port": 80,
                        "username": "user",
                        "password": "pass",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "http=example.com:80, username=user, password=pass, tag=HTTP Plain" in text
    assert "over-tls=" not in text
    assert "tls-host=" not in text


def test_quantumult_x_http_tls_renders_over_tls_and_tls_host() -> None:
    """Test QX HTTPS proxy maps tls to over-tls and tls-host."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTPS",
                        "type": "http",
                        "server": "example.com",
                        "port": 443,
                        "username": "user",
                        "password": "pass",
                        "tls": True,
                        "servername": "sni.example.com",
                        "skip-cert-verify": True,
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "http=example.com:443, username=user, password=pass, "
        "over-tls=true, tls-host=sni.example.com, tls-verification=false, "
        "tag=HTTPS"
    ) in text


def test_quantumult_x_http_without_credentials_omits_username_password() -> None:
    """Test QX HTTP proxy omits username/password when absent."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP No Auth",
                        "type": "http",
                        "server": "example.com",
                        "port": 8080,
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "http=example.com:8080, tag=HTTP No Auth" in text
    assert "username=" not in text
    assert "password=" not in text


def test_quantumult_x_socks5_plain_renders_documented_fields() -> None:
    """Test QX SOCKS5 proxy emits documented fields without TLS."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS5 Plain",
                        "type": "socks5",
                        "server": "example.com",
                        "port": 1080,
                        "username": "user",
                        "password": "pass",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "socks5=example.com:1080, username=user, password=pass, tag=SOCKS5 Plain"
    ) in text
    assert "over-tls=" not in text
    assert "tls-host=" not in text


def test_quantumult_x_socks5_tls_renders_over_tls_and_tls_host() -> None:
    """Test QX SOCKS5-TLS proxy maps tls to over-tls and tls-host."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS5 TLS",
                        "type": "socks5",
                        "server": "example.com",
                        "port": 1080,
                        "username": "user",
                        "password": "pass",
                        "tls": True,
                        "sni": "sni.example.com",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "socks5=example.com:1080, username=user, password=pass, "
        "over-tls=true, tls-host=sni.example.com, tag=SOCKS5 TLS"
    ) in text


def test_quantumult_x_anytls_renders_with_password_and_tls() -> None:
    """Test QX AnyTLS proxy emits password and over-tls=true."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS",
                        "type": "anytls",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "sni.example.com",
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert (
        "anytls=example.com:443, password=secret, "
        "over-tls=true, tls-host=sni.example.com, tag=AnyTLS"
    ) in text
    assert "username=" not in text


def test_quantumult_x_anytls_without_password_is_skipped() -> None:
    """Test QX skips AnyTLS proxy missing required password."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS No Pass",
                        "type": "anytls",
                        "server": "example.com",
                        "port": 443,
                        "tls": True,
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.body == b"no supported nodes for quantumult-x output"


def test_quantumult_x_http_reality_preserves_public_key() -> None:
    """Test QX HTTP Reality preserves reality-base64-pubkey and short-id."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP Reality",
                        "type": "http",
                        "server": "example.com",
                        "port": 443,
                        "username": "user",
                        "password": "pass",
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "over-tls=true" in text
    assert f"reality-base64-pubkey={REALITY_PUBLIC_KEY}" in text
    assert "reality-hex-shortid=0123456789abcdef" in text


def test_quantumult_x_socks5_reality_preserves_public_key() -> None:
    """Test QX SOCKS5 Reality preserves reality-base64-pubkey and short-id."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS5 Reality",
                        "type": "socks5",
                        "server": "example.com",
                        "port": 443,
                        "username": "user",
                        "password": "pass",
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "over-tls=true" in text
    assert f"reality-base64-pubkey={REALITY_PUBLIC_KEY}" in text
    assert "reality-hex-shortid=0123456789abcdef" in text


def test_quantumult_x_anytls_reality_preserves_public_key() -> None:
    """Test QX AnyTLS Reality preserves reality-base64-pubkey and short-id."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS Reality",
                        "type": "anytls",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "reality-opts": {
                            "public-key": REALITY_PUBLIC_KEY,
                            "short-id": "0123456789abcdef",
                        },
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "over-tls=true" in text
    assert f"reality-base64-pubkey={REALITY_PUBLIC_KEY}" in text
    assert "reality-hex-shortid=0123456789abcdef" in text


def test_quantumult_x_http_udp_relay_is_preserved() -> None:
    """Test QX HTTP proxy preserves udp-relay field."""
    response = build_renderer_registry()["quantumult-x"].render(
        RenderRequest(
            quantumult_x_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP UDP",
                        "type": "http",
                        "server": "example.com",
                        "port": 80,
                        "username": "user",
                        "password": "pass",
                        "udp-relay": True,
                    },
                )
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "udp-relay=true" in text


def test_surfboard_full_profile_contains_main_auto_proxy_groups() -> None:
    """Test Surfboard full profile contains required sections and groups."""
    response = build_renderer_registry()["surfboard"].render(surfboard_request())

    text = response.body.decode("utf-8")
    assert response.media_type == "text/plain; charset=utf-8"
    assert "[General]" in text
    assert "[Proxy]" in text
    assert "[Proxy Group]" in text
    assert "Main = select, Auto, Proxy, DIRECT" in text
    assert (
        "Auto = url-test, SS 01, VMess 01, "
        "policy-path=https://mpm.example.com/surfboard-nodes, "
        "policy-regex-filter=.*, "
        "url=http://www.gstatic.com/generate_204, interval=600, "
        "tolerance=100, timeout=5"
    ) in text
    assert (
        "Proxy = select, SS 01, VMess 01, "
        "policy-path=https://mpm.example.com/surfboard-nodes, "
        "policy-regex-filter=.*"
    ) in text
    assert "[Rule]" in text
    assert "FINAL,Main" in text


def test_surfboard_nodes_companion_omits_section_header() -> None:
    """Test Surfboard nodes companion emits only proxy lines."""
    response = build_renderer_registry()["surfboard"].render(
        surfboard_request(companion="nodes")
    )

    text = response.body.decode("utf-8")
    assert "[Proxy]" not in text
    assert text.startswith(
        "SS 01 = ss, example.com, 443, "
        "encrypt-method=chacha20-ietf-poly1305, password=password"
    )
    assert (
        "VMess 01 = vmess, example.com, 443, "
        "username=00000000-0000-0000-0000-000000000000"
    ) in text


def test_surfboard_renderer_skips_unsupported_nodes() -> None:
    """Test Surfboard rejects unsupported node types."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS 01",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                    },
                )
            ],
            companion_public_urls={"nodes": "https://mpm.example.com/surfboard-nodes"},
        )
    )

    assert response.status_code == 422
    assert response.media_type == "text/plain; charset=utf-8"
    assert b"no supported nodes for surfboard output" in response.body
    assert response.warnings


def test_surfboard_renderer_warns_when_unknown_node_is_dropped() -> None:
    """Test Surfboard warns when normalization drops an unknown node."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
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
                        "name": "Unknown 01",
                        "type": "unknown",
                        "server": "example.com",
                        "port": 443,
                    },
                ),
            ],
            companion_public_urls={"nodes": "https://mpm.example.com/surfboard-nodes"},
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "SS 01 = ss, example.com, 443" in text
    assert response.warnings
    assert any(
        "unsupported proxy type unknown" in warning for warning in response.warnings
    )


def test_surfboard_rejects_comma_delimited_credentials() -> None:
    """Test Surfboard skips credentials requiring unsupported comma escaping."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS Comma",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "pa,ss",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.warnings
    assert any("password" in warning for warning in response.warnings)


def test_surfboard_vmess_tls_uses_sni_and_skip_cert_verify() -> None:
    """Test Surfboard VMess TLS maps SNI and cert verification fields."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess TLS",
                        "type": "vmess",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                        "tls": True,
                        "servername": "example.com",
                        "skip-cert-verify": True,
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert "sni=example.com" in text
    assert "skip-cert-verify=true" in text
    assert "tls-host" not in text


def test_surfboard_vmess_ws_tls_uses_documented_option_order() -> None:
    """Test Surfboard VMess output follows the documented option order."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess WS TLS",
                        "type": "vmess",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                        "tls": True,
                        "network": "ws",
                        "ws-opts": {
                            "path": "/ws",
                            "headers": {"Host": "example.com"},
                        },
                        "udp-relay": True,
                        "servername": "example.com",
                        "skip-cert-verify": False,
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert (
        "VMess WS TLS = vmess, example.com, 443, "
        "username=00000000-0000-0000-0000-000000000000, "
        "udp-relay=true, ws=true, tls=true, ws-path=/ws, "
        "ws-headers=Host:example.com, skip-cert-verify=false, "
        "sni=example.com, vmess-aead=true"
    ) in text


def test_surfboard_hysteria2_only_node_renders_profile_line() -> None:
    """Test Surfboard renders Hysteria2 following the documented profile format."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HY2 01",
                        "type": "hysteria2",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "down": "100 Mbps",
                        "ports": "1234,5000-6000",
                        "hop-interval": "30",
                        "sni": "example.com",
                        "skip-cert-verify": True,
                        "obfs": "salamander",
                        "obfs-password": "obfs-secret",
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "HY2 01 = hysteria2, example.com, 443" in text
    assert "password=secret" in text
    assert "download-bandwidth=100 Mbps" in text
    assert "port-hopping=1234;5000-6000" in text
    assert "port-hopping-interval=30" in text
    assert "skip-cert-verify=true" in text
    assert "sni=example.com" in text
    assert "salamander-password=obfs-secret" in text
    assert not any("hysteria2" in w and "skipping" in w for w in response.warnings)


def test_surfboard_full_profile_includes_all_client_compatible_protocols() -> None:
    """Test Surfboard profile includes every client-compatible protocol."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "ss.example.com",
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
                        "server": "trojan.example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "trojan.example.com",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VMess 01",
                        "type": "vmess",
                        "server": "vmess.example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "cipher": "auto",
                        "tls": True,
                        "network": "ws",
                        "ws-opts": {
                            "path": "/ws",
                            "headers": {"Host": "vmess.example.com"},
                        },
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HY2 01",
                        "type": "hysteria2",
                        "server": "hy2.example.com",
                        "port": 443,
                        "password": "hy2-secret",
                        "down": "100 Mbps",
                        "sni": "hy2.example.com",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Snell 01",
                        "type": "snell",
                        "server": "snell.example.com",
                        "port": 443,
                        "psk": "psk-secret",
                        "version": 4,
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS 01",
                        "type": "anytls",
                        "server": "anytls.example.com",
                        "port": 443,
                        "password": "anytls-secret",
                        "sni": "anytls.example.com",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP 01",
                        "type": "http",
                        "server": "http.example.com",
                        "port": 8080,
                        "username": "user",
                        "password": "pass",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS 01",
                        "type": "socks5",
                        "server": "socks.example.com",
                        "port": 1080,
                        "username": "user",
                        "password": "pass",
                    },
                ),
            ],
            companion_public_urls={"nodes": "https://mpm.example.com/surfboard-nodes"},
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "SS 01 = ss, ss.example.com, 443" in text
    assert "Trojan 01 = trojan, trojan.example.com, 443" in text
    assert "VMess 01 = vmess, vmess.example.com, 443" in text
    assert "HY2 01 = hysteria2, hy2.example.com, 443" in text
    assert "Snell 01 = snell, snell.example.com, 443" in text
    assert "AnyTLS 01 = anytls, anytls.example.com, 443" in text
    assert "HTTP 01 = http, http.example.com, 8080" in text
    assert "SOCKS 01 = socks5, socks.example.com, 1080" in text
    assert (
        "Auto = url-test, SS 01, Trojan 01, VMess 01, HY2 01, Snell 01, "
        "AnyTLS 01, HTTP 01, SOCKS 01, "
        "policy-path=https://mpm.example.com/surfboard-nodes"
    ) in text
    assert (
        "Proxy = select, SS 01, Trojan 01, VMess 01, HY2 01, Snell 01, "
        "AnyTLS 01, HTTP 01, SOCKS 01, "
        "policy-path=https://mpm.example.com/surfboard-nodes"
    ) in text


def test_surfboard_hysteria2_mixed_with_supported_nodes_is_rendered() -> None:
    """Test Surfboard renders Hysteria2 alongside supported nodes."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
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
                        "name": "HY2 Minimal",
                        "type": "hysteria2",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "down-speed": 50,
                        "udp-relay": False,
                    },
                ),
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "SS 01 = ss, example.com, 443" in text
    assert "HY2 Minimal = hysteria2, example.com, 443" in text
    assert "download-bandwidth=50" in text
    assert "udp-relay=false" in text
    assert not any("hysteria2" in w and "skipping" in w for w in response.warnings)


def test_surfboard_trojan_ws_maps_path_and_multi_headers() -> None:
    """Test Surfboard Trojan WS maps path and all headers."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan WS",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "udp-relay": False,
                        "skip-cert-verify": True,
                        "sni": "example.com",
                        "network": "ws",
                        "ws-opts": {
                            "path": "/ws",
                            "headers": {
                                "Host": "example.com",
                                "X-Test": "1",
                            },
                        },
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert (
        "Trojan WS = trojan, example.com, 443, password=secret, "
        "udp-relay=false, skip-cert-verify=true, sni=example.com, "
        "ws=true, ws-path=/ws, ws-headers=Host:example.com|X-Test:1"
    ) in text


def test_surfboard_ss_obfs_maps_supported_fields() -> None:
    """Test Surfboard Shadowsocks obfs fields are preserved."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS Obfs",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                        "udp-relay": False,
                        "obfs": "http",
                        "obfs-host": "example.com",
                        "obfs-uri": "/obfs",
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert (
        "SS Obfs = ss, example.com, 443, "
        "encrypt-method=chacha20-ietf-poly1305, password=password, "
        "udp-relay=false, obfs=http, obfs-host=example.com, obfs-uri=/obfs"
    ) in text


def test_surfboard_ss_plugin_unsupported_fails_when_only_node() -> None:
    """Test Surfboard skips unsupported Shadowsocks plugin fields."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
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
                        "plugin": "v2ray-plugin",
                    },
                )
            ],
        )
    )

    assert response.status_code == 422
    assert response.warnings
    assert any(
        "unsupported Shadowsocks field plugin" in warning
        for warning in response.warnings
    )


def test_surfboard_ss_plugin_unsupported_warns_when_mixed() -> None:
    """Test Surfboard warns but keeps supported nodes when SS plugin is mixed."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
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
                        "name": "SS Plugin",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                        "plugin-opts": {"mode": "websocket"},
                    },
                ),
            ],
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "SS 01 = ss, example.com, 443" in text
    assert response.warnings
    assert any(
        "unsupported Shadowsocks field plugin-opts" in warning
        for warning in response.warnings
    )


def test_surfboard_renderer_sanitizes_node_names() -> None:
    """Test Surfboard labels avoid comma/control characters."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
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
            companion_public_urls={"nodes": "https://mpm.example.com/surfboard-nodes"},
        )
    )

    text = response.body.decode("utf-8")
    proxy_line = next(line for line in text.splitlines() if line.startswith("HK "))
    auto_line = next(line for line in text.splitlines() if line.startswith("Auto ="))
    assert proxy_line.startswith("HK 01 = ss,")
    assert "HK, 01" not in proxy_line
    assert "HK, 01" not in auto_line
    assert "\n" not in proxy_line


def test_surfboard_snell_renders_with_psk_and_obfs() -> None:
    """Test Surfboard Snell maps psk, version, obfs-opts, and udp-relay."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Snell 01",
                        "type": "snell",
                        "server": "snell.example.com",
                        "port": 443,
                        "psk": "psk-secret",
                        "version": 4,
                        "udp-relay": True,
                        "obfs-opts": {
                            "mode": "tls",
                            "host": "snell.example.com",
                            "uri": "/snell",
                        },
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "Snell 01 = snell, snell.example.com, 443" in text
    assert "psk=psk-secret" in text
    assert "version=4" in text
    assert "udp-relay=true" in text
    assert "obfs=tls" in text
    assert "obfs-host=snell.example.com" in text
    assert "obfs-uri=/snell" in text


def test_surfboard_anytls_renders_positional_password() -> None:
    """Test Surfboard AnyTLS renders positional password, sni, and reuse."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "AnyTLS 01",
                        "type": "anytls",
                        "server": "anytls.example.com",
                        "port": 443,
                        "password": "anytls-secret",
                        "sni": "anytls.example.com",
                        "skip-cert-verify": True,
                        "reuse": True,
                    },
                )
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "AnyTLS 01 = anytls, anytls.example.com, 443, anytls-secret" in text
    assert "skip-cert-verify=true" in text
    assert "sni=anytls.example.com" in text
    assert "reuse=true" in text


def test_surfboard_http_renders_plain_and_tls() -> None:
    """Test Surfboard HTTP uses 'http' for plain and 'https' for TLS."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTP 01",
                        "type": "http",
                        "server": "http.example.com",
                        "port": 8080,
                        "username": "user",
                        "password": "pass",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "HTTPS 01",
                        "type": "http",
                        "server": "https.example.com",
                        "port": 443,
                        "username": "user2",
                        "password": "pass2",
                        "tls": True,
                        "sni": "https.example.com",
                        "skip-cert-verify": True,
                    },
                ),
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "HTTP 01 = http, http.example.com, 8080, user, pass" in text
    assert "HTTPS 01 = https, https.example.com, 443, user2, pass2" in text
    assert "sni=https.example.com" in text
    assert "skip-cert-verify=true" in text


def test_surfboard_socks5_renders_plain_and_tls() -> None:
    """Test Surfboard SOCKS5 uses 'socks5' for plain and 'socks5-tls' for TLS."""
    response = build_renderer_registry()["surfboard"].render(
        RenderRequest(
            surfboard_route(),
            [
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS 01",
                        "type": "socks5",
                        "server": "socks.example.com",
                        "port": 1080,
                        "username": "user",
                        "password": "pass",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SOCKS TLS 01",
                        "type": "socks5",
                        "server": "socks-tls.example.com",
                        "port": 443,
                        "username": "user2",
                        "password": "pass2",
                        "tls": True,
                        "sni": "socks-tls.example.com",
                        "skip-cert-verify": True,
                    },
                ),
            ],
            companion="nodes",
        )
    )

    text = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "SOCKS 01 = socks5, socks.example.com, 1080, user, pass" in text
    assert "SOCKS TLS 01 = socks5-tls, socks-tls.example.com, 443, user2, pass2" in text
    assert "sni=socks-tls.example.com" in text
    assert "skip-cert-verify=true" in text


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
