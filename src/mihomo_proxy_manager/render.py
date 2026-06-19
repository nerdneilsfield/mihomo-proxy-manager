"""将代理记录渲染为 Mihomo ``proxy-providers`` 格式的 YAML。

Render proxy records into Mihomo ``proxy-providers`` format YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, Sequence, cast

import yaml
from loguru import logger

from .mihomo_schema import normalize_proxy
from .models import ProxyRecord, RouteConfig
from .transform import apply_transform, repair_duplicate_names

SourceRecord = ProxyRecord


class _QuotedString(str):
    """String scalar that must be emitted with double quotes."""


class _MihomoProviderDumper(yaml.SafeDumper):
    """YAML dumper for Mihomo provider payloads."""


def _quoted_string_representer(
    dumper: yaml.SafeDumper, value: _QuotedString
) -> yaml.nodes.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


_MihomoProviderDumper.add_representer(_QuotedString, _quoted_string_representer)


@dataclass(frozen=True)
class RenderRequest:
    """Route render request shared by output format renderers."""

    route: RouteConfig
    records: Sequence[SourceRecord]
    request_base_url: str | None = None


@dataclass(frozen=True)
class RenderResponse:
    """Rendered route response body and HTTP metadata."""

    body: bytes
    media_type: str = "text/yaml; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


class RouteRenderer(Protocol):
    """Protocol for route output renderers."""

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render a route request into a response."""


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
                logger.warning(
                    "dropping invalid proxy before render: {warning}", warning=warning
                )
            continue
        normalized_records.append(ProxyRecord(source=record.source, data=normalized))
    return normalized_records


def prepare_render_records(
    route: RouteConfig, records: Sequence[SourceRecord]
) -> list[dict[str, object]]:
    """Apply route transform, normalization, and name dedupe before rendering."""
    transformed = apply_transform(
        list(records), filter_config=route.filter, rename_config=route.rename
    )
    normalized = _normalize_render_records(transformed)
    repaired = repair_duplicate_names(normalized)
    return [
        cast(dict[str, object], _quote_proxy_strings(dict(record.data)))
        for record in repaired
    ]


class ProviderRenderer:
    """将代理记录渲染为 Mihomo provider 格式 YAML 的渲染器。

    Renderer that converts proxy records into Mihomo provider format YAML.
    """

    def __init__(self, *, yaml_sort_keys: bool = False) -> None:
        """初始化 ProviderRenderer。

        Initialize ProviderRenderer.

        Args:
            yaml_sort_keys: 是否对 YAML 键排序 / Whether to sort YAML keys.
        """
        self.yaml_sort_keys = yaml_sort_keys

    def render_sync(self, route: RouteConfig, records: Sequence[SourceRecord]) -> bytes:
        """同步渲染路由输出为 YAML 字节流。

        Synchronously render route output as YAML byte stream.

        Args:
            route: 路由配置 / Route configuration.
            records: 代理记录列表 / List of proxy records.

        Returns:
            YAML 格式的字节流 / YAML formatted byte stream.
        """
        proxies = prepare_render_records(route, records)
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

    async def render(self, route: RouteConfig, records: Sequence[SourceRecord]) -> bytes:
        """异步渲染路由输出为 YAML 字节流。

        Asynchronously render route output as YAML byte stream.

        Args:
            route: 路由配置 / Route configuration.
            records: 代理记录列表 / List of proxy records.

        Returns:
            YAML 格式的字节流 / YAML formatted byte stream.
        """
        return self.render_sync(route, records)


@dataclass(frozen=True)
class ProviderRouteRenderer:
    """RouteRenderer adapter for existing provider YAML rendering."""

    renderer: ProviderRenderer

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render provider output and wrap it in RenderResponse."""
        return RenderResponse(
            body=self.renderer.render_sync(request.route, request.records)
        )


def build_renderer_registry() -> dict[str, RouteRenderer]:
    """Build route renderer registry keyed by route output format."""
    return {"provider": ProviderRouteRenderer(ProviderRenderer())}
