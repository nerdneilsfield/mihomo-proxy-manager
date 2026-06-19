"""将代理记录渲染为 Mihomo ``proxy-providers`` 格式的 YAML。

Render proxy records into Mihomo ``proxy-providers`` format YAML.
"""

from __future__ import annotations

import base64
import json
import re
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


def _qx_value(value: object) -> str:
    """Convert a Quantumult X field value to a single-line scalar."""
    return _string(value).replace("\r", " ").replace("\n", " ")


def _qx_clean_label(value: object) -> str:
    """Sanitize comma/control-sensitive Quantumult X label text."""
    cleaned = re.sub(r"[\x00-\x1f\x7f,]+", " ", _string(value))
    return " ".join(cleaned.split())


def _qx_hostport(data: dict[str, object]) -> str | None:
    """Build Quantumult X host:port endpoint."""
    host = _string(data.get("server"))
    port = _string(data.get("port"))
    if not host or not port:
        return None
    return f"{host}:{port}"


def _qx_tag(data: dict[str, object]) -> str:
    """Build Quantumult X tag segment."""
    return f"tag={_qx_clean_label(data.get('name'))}"


def _qx_ws_segments(data: dict[str, object], obfs: str) -> list[str]:
    """Build Quantumult X websocket obfs segments."""
    segments = [f"obfs={obfs}"]
    ws_opts = data.get("ws-opts")
    if isinstance(ws_opts, dict):
        path = _string(ws_opts.get("path"))
        if path:
            segments.append(f"obfs-uri={_qx_value(path)}")
        headers = ws_opts.get("headers")
        if isinstance(headers, dict):
            host = _string(headers.get("Host") or headers.get("host"))
            if host:
                segments.append(f"obfs-host={_qx_value(host)}")
    return segments


def _qx_ss_obfs_segments(data: dict[str, object]) -> list[str] | None:
    """Build Quantumult X Shadowsocks obfs segments."""
    network = _string(data.get("network"))
    tls_enabled = _boolish(data.get("tls"))
    if not network or network == "tcp":
        if not tls_enabled:
            return []
        segments = ["obfs=tls"]
        tls_host = _string(data.get("servername") or data.get("sni"))
        if tls_host:
            segments.append(f"obfs-host={_qx_value(tls_host)}")
        return segments
    if network == "ws":
        return _qx_ws_segments(data, "wss" if tls_enabled else "ws")
    return None


def _qx_vmess_vless_obfs_segments(data: dict[str, object]) -> list[str] | None:
    """Build Quantumult X VMess/VLESS obfs segments."""
    network = _string(data.get("network"))
    tls_enabled = _boolish(data.get("tls"))
    if not network or network == "tcp":
        if not tls_enabled:
            return []
        segments = ["obfs=over-tls"]
        tls_host = _string(data.get("servername") or data.get("sni"))
        if tls_host:
            segments.append(f"obfs-host={_qx_value(tls_host)}")
        return segments
    if network == "ws":
        return _qx_ws_segments(data, "wss" if tls_enabled else "ws")
    if network == "grpc":
        segments = ["obfs=grpc"]
        grpc_opts = data.get("grpc-opts")
        if isinstance(grpc_opts, dict):
            service_name = _string(grpc_opts.get("grpc-service-name"))
            if service_name:
                segments.append(f"obfs-uri={_qx_value(service_name)}")
        return segments
    return None


def _qx_trojan_transport_segments(data: dict[str, object]) -> list[str] | None:
    """Build simple Quantumult X Trojan transport segments."""
    network = _string(data.get("network"))
    if not network or network == "tcp":
        return []
    if network == "ws":
        return _qx_ws_segments(data, "ws")
    if network == "grpc":
        segments = ["obfs=grpc"]
        grpc_opts = data.get("grpc-opts")
        if isinstance(grpc_opts, dict):
            service_name = _string(grpc_opts.get("grpc-service-name"))
            if service_name:
                segments.append(f"obfs-uri={_qx_value(service_name)}")
        return segments
    return None


def _qx_trojan_tls_segments(data: dict[str, object], *, default_host: str) -> list[str]:
    """Build Quantumult X Trojan TLS segments."""
    segments: list[str] = []
    tls_host = _string(data.get("servername") or data.get("sni") or default_host)
    if tls_host:
        segments.append(f"tls-host={_qx_value(tls_host)}")
    if "skip-cert-verify" in data:
        verification = "false" if _boolish(data.get("skip-cert-verify")) else "true"
        segments.append(f"tls-verification={verification}")
    return segments


def _has_unsupported_qx_ss_plugin(data: dict[str, object]) -> str | None:
    """Return first Shadowsocks plugin/obfs field Quantumult X renderer skips."""
    for field_name in (
        "plugin",
        "plugin-opts",
        "obfs",
        "obfs-host",
        "obfs-uri",
        "obfs-opts",
    ):
        if field_name in data and data[field_name] not in (None, "", False):
            return field_name
    return None


def _render_qx_ss(data: dict[str, object]) -> str | None:
    """Render Shadowsocks proxy as Quantumult X server_remote line."""
    hostport = _qx_hostport(data)
    method = _string(data.get("cipher") or data.get("method"))
    password = _string(data.get("password"))
    if not hostport or not method or not password:
        return None
    obfs_segments = _qx_ss_obfs_segments(data)
    if obfs_segments is None:
        return None
    segments = [
        f"shadowsocks={hostport}",
        f"method={_qx_value(method)}",
        f"password={_qx_value(password)}",
    ]
    segments.extend(obfs_segments)
    segments.append(_qx_tag(data))
    return ", ".join(segments)


def _render_qx_trojan(data: dict[str, object]) -> str | None:
    """Render Trojan proxy as Quantumult X server_remote line."""
    hostport = _qx_hostport(data)
    password = _string(data.get("password"))
    host = _string(data.get("server"))
    if not hostport or not password:
        return None
    segments = [
        f"trojan={hostport}",
        f"password={_qx_value(password)}",
        "over-tls=true",
    ]
    segments.extend(_qx_trojan_tls_segments(data, default_host=host))
    transport = _qx_trojan_transport_segments(data)
    if transport is None:
        return None
    segments.extend(transport)
    segments.append(_qx_tag(data))
    return ", ".join(segments)


def _render_qx_vless(data: dict[str, object]) -> str | None:
    """Render VLESS proxy as Quantumult X server_remote line."""
    hostport = _qx_hostport(data)
    uuid = _string(data.get("uuid"))
    if not hostport or not uuid:
        return None
    transport = _qx_vmess_vless_obfs_segments(data)
    if transport is None:
        return None
    segments = [
        f"vless={hostport}",
        "method=none",
        f"password={_qx_value(uuid)}",
    ]
    segments.extend(transport)
    segments.append(_qx_tag(data))
    return ", ".join(segments)


def _render_qx_vmess(data: dict[str, object]) -> str | None:
    """Render VMess proxy as Quantumult X server_remote line."""
    hostport = _qx_hostport(data)
    uuid = _string(data.get("uuid"))
    if not hostport or not uuid:
        return None
    transport = _qx_vmess_vless_obfs_segments(data)
    if transport is None:
        return None
    segments = [
        f"vmess={hostport}",
        "method=none",
        f"password={_qx_value(uuid)}",
    ]
    segments.extend(transport)
    segments.append(_qx_tag(data))
    return ", ".join(segments)


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


class QuantumultXRenderer:
    """Render route records as Quantumult X server_remote output."""

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        """Return Quantumult X import companion path when enabled."""
        if route.output.import_link:
            return (f"{route.path}-import",)
        return ()

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render Quantumult X output or import companion response."""
        if request.companion == "import":
            return self._render_import(request)

        proxies = prepare_render_records(request.route, request.records)
        warnings: list[str] = []
        lines: list[str] = []
        renderers = {
            "ss": _render_qx_ss,
            "trojan": _render_qx_trojan,
            "vless": _render_qx_vless,
            "vmess": _render_qx_vmess,
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
            if proxy_type == "ss":
                unsupported_ss_field = _has_unsupported_qx_ss_plugin(proxy)
                if unsupported_ss_field is not None:
                    warnings.append(
                        _skip_warning(
                            proxy,
                            f"unsupported Shadowsocks field {unsupported_ss_field}",
                        )
                    )
                    continue
            renderer = renderers.get(proxy_type)
            if renderer is None:
                warnings.append(
                    _skip_warning(proxy, f"unsupported proxy type {proxy_type}")
                )
                continue
            line = renderer(proxy)
            if line is None:
                network = _string(proxy.get("network"))
                reason = (
                    f"unsupported transport {network}"
                    if network and network not in {"tcp", "ws", "grpc"}
                    else "missing required Quantumult X fields"
                )
                warnings.append(_skip_warning(proxy, reason))
                continue
            lines.append(line)

        if not lines:
            return RenderResponse(
                body=b"no supported nodes for quantumult-x output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )

        return RenderResponse(
            body=("\n".join(lines) + "\n").encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )

    def _render_import(self, request: RenderRequest) -> RenderResponse:
        """Render Quantumult X remote resource import response."""
        resource_tag = (
            _qx_clean_label(request.route.output.resource_tag)
            or _qx_clean_label(request.route.name)
            or "MPM"
        )
        payload = {
            "server_remote": [
                f"{request.main_public_url}, tag={resource_tag}, "
                "update-interval=86400, enabled=true"
            ]
        }
        encoded = quote(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            safe="",
        )
        if request.route.output.import_target == "universal-link":
            target = (
                "https://quantumult.app/x/open-app/add-resource"
                f"?remote-resource={encoded}"
            )
        else:
            target = f"quantumult-x:///add-resource?remote-resource={encoded}"

        if request.route.output.import_response == "plain":
            return RenderResponse(
                body=f"{target}\n".encode("utf-8"),
                media_type="text/plain; charset=utf-8",
            )
        return RenderResponse(
            body=b"",
            media_type="text/plain; charset=utf-8",
            headers={"Location": target},
            status_code=302,
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
            body=self.renderer.render_sync(request.route, request.records),
            media_type="application/yaml; charset=utf-8",
        )


def build_renderer_registry(*, yaml_sort_keys: bool = False) -> dict[str, RouteRenderer]:
    """Build route renderer registry keyed by route output format."""
    return {
        "provider": ProviderRouteRenderer(
            ProviderRenderer(yaml_sort_keys=yaml_sort_keys)
        ),
        "xray-uri": XrayUriRenderer(),
        "quantumult-x": QuantumultXRenderer(),
    }
