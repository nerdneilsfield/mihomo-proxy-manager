"""YAML 渲染器测试，包括重命名、去重和元注释。

YAML renderer tests including renaming, deduplication, and meta comments.
"""

import yaml

from mihomo_proxy_manager.models import ProxyRecord, RenameConfig, FilterConfig, RouteConfig, RouteOutputConfig
from mihomo_proxy_manager.render import ProviderRenderer


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
        [ProxyRecord("airport_a", {"name": "HK:01", "type": "vmess", "server": "example.com", "port": 443, "uuid": "id", "cipher": "auto"})],
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
            ProxyRecord("a", {"name": "HK", "type": "vmess"}),
            ProxyRecord("b", {"name": "HK", "type": "vmess"}),
            ProxyRecord("c", {"name": "HK #2", "type": "vmess"}),
        ],
    )

    names = [item["name"] for item in yaml.safe_load(body)["proxies"]]
    assert names == ["[phone] HK", "[phone] HK #3", "[phone] HK #2"]


def test_provider_renderer_includes_sources_in_meta_comments() -> None:
    """测试渲染器在元注释中包含源信息 / Test that the renderer includes source info in meta comments."""
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(include_meta_comments=True),
        [ProxyRecord("airport_a", {"name": "HK", "type": "vmess"})],
    )

    text = body.decode("utf-8")
    assert "# sources: 1" in text
    assert "# nodes: 1" in text
    assert "# route: phone" in text
