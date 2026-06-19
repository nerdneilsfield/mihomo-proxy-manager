"""将代理记录渲染为 Mihomo ``proxy-providers`` 格式的 YAML。

Render proxy records into Mihomo ``proxy-providers`` format YAML.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Mapping, Protocol, Sequence, cast
from urllib.parse import quote, urlencode

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
    main_public_url: str = ""
    companion_public_urls: Mapping[str, str] = field(default_factory=dict)
    companion: str | None = None


@dataclass(frozen=True)
class RenderResponse:
    """Rendered route response body and HTTP metadata."""

    body: bytes
    media_type: str = "text/yaml; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    status_code: int = 200
    warnings: tuple[str, ...] = ()


class RouteRenderer(Protocol):
    """Protocol for route output renderers."""

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        """Return companion paths served by this renderer for the route."""

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


def _string(value: object) -> str:
    """Convert proxy values to strings for URI components."""
    if value is None:
        return ""
    return str(value)


def _boolish(value: object) -> bool:
    """Interpret common proxy config truthy values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _uri_host(host: str) -> str:
    """Bracket IPv6 hosts in URI authority."""
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


def _hostport(data: dict[str, object]) -> str | None:
    """Build URI authority host:port."""
    host = _string(data.get("server"))
    port = _string(data.get("port"))
    if not host or not port:
        return None
    return f"{_uri_host(host)}:{port}"


def _encoded_name(data: dict[str, object]) -> str:
    """Return percent-encoded URI fragment name."""
    return quote(_string(data.get("name")), safe="")


def _query_string(params: dict[str, str]) -> str:
    """Encode non-empty URI query params."""
    return urlencode({key: value for key, value in params.items() if value != ""})


def _client_fingerprint_params(data: dict[str, object]) -> dict[str, str]:
    """Map Mihomo client-fingerprint to xray URI query params."""
    fingerprint = _string(data.get("client-fingerprint"))
    if not fingerprint:
        return {}
    return {"fp": fingerprint}


def _network_params(data: dict[str, object]) -> dict[str, str]:
    """Map common network transport fields to xray URI query params."""
    params: dict[str, str] = {}
    network = _string(data.get("network"))
    if network in {"tcp", "ws", "grpc", "http", "h2"}:
        params["type"] = network
    if network == "ws":
        ws_opts = data.get("ws-opts")
        if isinstance(ws_opts, dict):
            path = _string(ws_opts.get("path"))
            if path:
                params["path"] = path
            headers = ws_opts.get("headers")
            if isinstance(headers, dict):
                host = _string(headers.get("Host") or headers.get("host"))
                if host:
                    params["host"] = host
    if network == "grpc":
        grpc_opts = data.get("grpc-opts")
        if isinstance(grpc_opts, dict):
            service_name = _string(grpc_opts.get("grpc-service-name"))
            if service_name:
                params["serviceName"] = service_name
    return params


def _has_security_critical_field(data: dict[str, object]) -> str | None:
    """Return first unmapped security-critical field present on a proxy."""
    security_critical_fields = (
        "reality-opts",
        "ech-opts",
        "flow",
        "fingerprint",
        "certificate",
        "certificate-pinning",
        "pinned-cert",
        "fingerprint-sha256",
    )
    for field_name in security_critical_fields:
        if field_name in data and data[field_name] not in (None, "", False):
            return field_name
    return None


def _skip_warning(data: dict[str, object], reason: str) -> str:
    """Build a consistent skip warning for xray-uri rendering."""
    name = _string(data.get("name")) or "<unnamed>"
    return f"skipping {name}: {reason}"


def _render_ss_uri(data: dict[str, object]) -> str | None:
    """Render Shadowsocks proxy as xray-compatible URI."""
    hostport = _hostport(data)
    method = _string(data.get("cipher") or data.get("method"))
    password = _string(data.get("password"))
    if not hostport or not method or not password:
        return None
    userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode("utf-8"))
    encoded_userinfo = userinfo.decode("ascii").rstrip("=")
    return f"ss://{encoded_userinfo}@{hostport}#{_encoded_name(data)}"


def _render_trojan_uri(data: dict[str, object]) -> str | None:
    """Render Trojan proxy as xray-compatible URI."""
    hostport = _hostport(data)
    password = _string(data.get("password"))
    if not hostport or not password:
        return None
    params: dict[str, str] = {}
    sni = _string(data.get("sni") or data.get("servername"))
    if sni:
        params["sni"] = sni
    if _boolish(data.get("skip-cert-verify")):
        params["allowInsecure"] = "1"
    params.update(_client_fingerprint_params(data))
    params.update(_network_params(data))
    query = _query_string(params)
    query_part = f"?{query}" if query else ""
    return (
        f"trojan://{quote(password, safe='')}@{hostport}"
        f"{query_part}#{_encoded_name(data)}"
    )


def _render_vless_uri(data: dict[str, object]) -> str | None:
    """Render VLESS proxy as xray-compatible URI."""
    hostport = _hostport(data)
    uuid = _string(data.get("uuid"))
    if not hostport or not uuid:
        return None
    params: dict[str, str] = {"encryption": "none"}
    if _boolish(data.get("tls")):
        params["security"] = "tls"
    sni = _string(data.get("servername") or data.get("sni"))
    if sni:
        params["sni"] = sni
    params.update(_client_fingerprint_params(data))
    params.update(_network_params(data))
    query = _query_string(params)
    query_part = f"?{query}" if query else ""
    return f"vless://{quote(uuid, safe='')}@{hostport}{query_part}#{_encoded_name(data)}"


def _render_vmess_uri(data: dict[str, object]) -> str | None:
    """Render VMess proxy as v2rayN base64 JSON URI."""
    host = _string(data.get("server"))
    port = _string(data.get("port"))
    uuid = _string(data.get("uuid"))
    if not host or not port or not uuid:
        return None
    network = _string(data.get("network")) or "tcp"
    payload = {
        "v": "2",
        "ps": _string(data.get("name")),
        "add": host,
        "port": port,
        "id": uuid,
        "aid": _string(data.get("alterId") or data.get("alter-id") or "0"),
        "net": network,
        "type": "none",
        "host": "",
        "path": "",
        "tls": "tls" if _boolish(data.get("tls")) else "",
        "sni": _string(data.get("servername") or data.get("sni")),
    }
    ws_opts = data.get("ws-opts")
    if network == "ws" and isinstance(ws_opts, dict):
        payload["path"] = _string(ws_opts.get("path"))
        headers = ws_opts.get("headers")
        if isinstance(headers, dict):
            payload["host"] = _string(headers.get("Host") or headers.get("host"))
    fingerprint = _string(data.get("client-fingerprint"))
    if fingerprint:
        payload["fp"] = fingerprint
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"vmess://{encoded}"


class XrayUriRenderer:
    """Render route records as v2rayN-compatible xray URI subscriptions."""

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        """Return companion paths for xray-uri output."""
        return ()

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render xray-uri output and collect skipped-node warnings."""
        proxies = prepare_render_records(request.route, request.records)
        warnings: list[str] = []
        uris: list[str] = []
        renderers = {
            "ss": _render_ss_uri,
            "trojan": _render_trojan_uri,
            "vless": _render_vless_uri,
            "vmess": _render_vmess_uri,
        }

        for proxy in proxies:
            critical_field = _has_security_critical_field(proxy)
            if critical_field is not None:
                warnings.append(
                    _skip_warning(
                        proxy,
                        f"unsupported security-critical field {critical_field}",
                    )
                )
                continue
            proxy_type = _string(proxy.get("type")).lower()
            renderer = renderers.get(proxy_type)
            if renderer is None:
                warnings.append(
                    _skip_warning(proxy, f"unsupported proxy type {proxy_type}")
                )
                continue
            uri = renderer(proxy)
            if uri is None:
                warnings.append(_skip_warning(proxy, "missing required URI fields"))
                continue
            uris.append(uri)

        if not uris:
            return RenderResponse(
                body=b"no supported nodes for xray-uri output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )

        payload = ("\n".join(uris) + "\n").encode("utf-8")
        if request.route.output.encoding == "base64":
            payload = base64.b64encode(payload)
        return RenderResponse(
            body=payload,
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )

    render_sync = render


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

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        """Return companion paths for provider output."""
        return ()

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render provider output and wrap it in RenderResponse."""
        return RenderResponse(
            body=self.renderer.render_sync(request.route, request.records)
        )


def build_renderer_registry(*, yaml_sort_keys: bool = False) -> dict[str, RouteRenderer]:
    """Build route renderer registry keyed by route output format."""
    return {
        "provider": ProviderRouteRenderer(
            ProviderRenderer(yaml_sort_keys=yaml_sort_keys)
        ),
        "xray-uri": XrayUriRenderer(),
    }
