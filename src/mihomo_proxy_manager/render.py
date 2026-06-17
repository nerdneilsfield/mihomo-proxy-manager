"""将代理记录渲染为 Mihomo ``proxy-providers`` 格式的 YAML。

Render proxy records into Mihomo ``proxy-providers`` format YAML.
"""

from __future__ import annotations

from datetime import UTC, datetime

import yaml

from .models import ProxyRecord, RouteConfig
from .transform import apply_transform, repair_duplicate_names


class ProviderRenderer:
    """将代理记录渲染为 Mihomo provider 格式 YAML 的渲染器。

    Renderer that converts proxy records into Mihomo provider format YAML.
    """

    def __init__(self, *, yaml_sort_keys: bool) -> None:
        """初始化 ProviderRenderer。

        Initialize ProviderRenderer.

        Args:
            yaml_sort_keys: 是否对 YAML 键排序 / Whether to sort YAML keys.
        """
        self.yaml_sort_keys = yaml_sort_keys

    def render_sync(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        """同步渲染路由输出为 YAML 字节流。

        Synchronously render route output as YAML byte stream.

        Args:
            route: 路由配置 / Route configuration.
            records: 代理记录列表 / List of proxy records.

        Returns:
            YAML 格式的字节流 / YAML formatted byte stream.
        """
        transformed = apply_transform(records, filter_config=route.filter, rename_config=route.rename)
        repaired = repair_duplicate_names(transformed)
        proxies = [dict(record.data) for record in repaired]
        payload = {"proxies": proxies}
        body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=self.yaml_sort_keys).encode("utf-8")
        if route.output.include_meta_comments:
            prefix = (
                f"# generated_at: {datetime.now(UTC).isoformat()}\n"
                f"# route: {route.name}\n"
                f"# sources: {len(route.sources)}\n"
                f"# nodes: {len(proxies)}\n"
            ).encode("utf-8")
            return prefix + body
        return body

    async def render(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        """异步渲染路由输出为 YAML 字节流。

        Asynchronously render route output as YAML byte stream.

        Args:
            route: 路由配置 / Route configuration.
            records: 代理记录列表 / List of proxy records.

        Returns:
            YAML 格式的字节流 / YAML formatted byte stream.
        """
        return self.render_sync(route, records)
