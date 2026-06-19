"""将代理记录渲染为 Mihomo ``proxy-providers`` 格式的 YAML。

Render proxy records into Mihomo ``proxy-providers`` format YAML.
"""

from __future__ import annotations

from datetime import UTC, datetime

import yaml
from loguru import logger

from .mihomo_schema import normalize_proxy
from .models import ProxyRecord, RouteConfig
from .transform import apply_transform, repair_duplicate_names


class _QuotedString(str):
    """String scalar that must be emitted with double quotes."""


class _MihomoProviderDumper(yaml.SafeDumper):
    """YAML dumper for Mihomo provider payloads."""


def _quoted_string_representer(
    dumper: yaml.SafeDumper, value: _QuotedString
) -> yaml.nodes.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


_MihomoProviderDumper.add_representer(_QuotedString, _quoted_string_representer)


def _quote_proxy_strings(value: object) -> object:
    """Quote string values while preserving numeric, boolean, list, and map shapes."""
    if isinstance(value, str):
        return _QuotedString(value)
    if isinstance(value, list):
        return [_quote_proxy_strings(item) for item in value]
    if isinstance(value, tuple):
        return [_quote_proxy_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _quote_proxy_strings(item) for key, item in value.items()}
    return value


def _normalize_render_records(records: list[ProxyRecord]) -> list[ProxyRecord]:
    """Normalize records before rendering so provider YAML matches Mihomo schema."""
    normalized_records: list[ProxyRecord] = []
    for record in records:
        normalized, warnings = normalize_proxy(dict(record.data))
        if normalized is None:
            for warning in warnings:
                logger.warning("dropping invalid proxy before render: {warning}", warning=warning)
            continue
        normalized_records.append(ProxyRecord(source=record.source, data=normalized))
    return normalized_records


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
        transformed = apply_transform(
            records, filter_config=route.filter, rename_config=route.rename
        )
        normalized = _normalize_render_records(transformed)
        repaired = repair_duplicate_names(normalized)
        proxies = [_quote_proxy_strings(dict(record.data)) for record in repaired]
        payload = {"proxies": proxies}
        body = yaml.dump(
            payload,
            Dumper=_MihomoProviderDumper,
            allow_unicode=True,
            sort_keys=self.yaml_sort_keys,
        ).encode("utf-8")
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
