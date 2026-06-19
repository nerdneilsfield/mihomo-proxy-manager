"""YAML 渲染器测试，包括重命名、去重和元注释。

YAML renderer tests including renaming, deduplication, and meta comments.
"""

import yaml

from mihomo_proxy_manager.models import (
    ProxyRecord,
    RenameConfig,
    FilterConfig,
    RouteConfig,
    RouteOutputConfig,
)
from mihomo_proxy_manager.render import ProviderRenderer

REALITY_PUBLIC_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def route(include_meta_comments: bool = False) -> RouteConfig:
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
        output=RouteOutputConfig("provider", include_meta_comments),
        rename=RenameConfig(prefix="[phone] "),
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
