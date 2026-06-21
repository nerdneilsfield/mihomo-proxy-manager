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


def _profile_scalar_issue(value: object) -> str | None:
    """Return why a comma-delimited profile scalar cannot be represented."""
    text = _string(value)
    if any(ch in text for ch in ("\r", "\n", ",")):
        return "comma/control character"
    if re.search(r"[\x00-\x1f\x7f]", text):
        return "control character"
    return None


def _qx_clean_label(value: object) -> str:
    """Sanitize comma/control-sensitive Quantumult X label text."""
    cleaned = re.sub(r"[\x00-\x1f\x7f,]+", " ", _string(value))
    return " ".join(cleaned.split())


def _sb_value(value: object) -> str:
    """Convert a Surfboard field value to a safe comma-delimited scalar."""
    cleaned = re.sub(r"[\x00-\x1f\x7f,]+", " ", _string(value))
    return " ".join(cleaned.split())


def _sb_label(value: object) -> str:
    """Sanitize Surfboard node and group labels."""
    return _sb_value(value)


def _sb_bool(value: object) -> str:
    """Render Surfboard boolean token value."""
    return "true" if _boolish(value) else "false"


def _sb_ws_headers(data: dict[str, object]) -> str:
    """Render Surfboard WebSocket header token."""
    ws_opts = data.get("ws-opts")
    if not isinstance(ws_opts, dict):
        return ""
    headers = ws_opts.get("headers")
    if not isinstance(headers, dict):
        return ""
    segments: list[str] = []
    for key, value in headers.items():
        header_name = _sb_value(key)
        header_value = _sb_value(value)
        if header_name and header_value:
            segments.append(f"{header_name}:{header_value}")
    return "|".join(segments)


def _sb_ws_path(data: dict[str, object]) -> str:
    """Return Surfboard WebSocket path."""
    ws_opts = data.get("ws-opts")
    if isinstance(ws_opts, dict):
        return _sb_value(ws_opts.get("path"))
    return _sb_value(data.get("ws-path"))


def _sb_base_segments(data: dict[str, object], proxy_type: str) -> list[str] | None:
    """Build shared Surfboard proxy line prefix."""
    name = _sb_label(data.get("name"))
    host = _sb_value(data.get("server"))
    port = _sb_value(data.get("port"))
    if not name or not host or not port:
        return None
    return [f"{name} = {proxy_type}", host, port]


def _sb_ws_segments(data: dict[str, object]) -> list[str]:
    """Build Surfboard WebSocket option segments."""
    segments = ["ws=true"]
    ws_path = _sb_ws_path(data)
    if ws_path:
        segments.append(f"ws-path={ws_path}")
    ws_headers = _sb_ws_headers(data)
    if ws_headers:
        segments.append(f"ws-headers={ws_headers}")
    return segments


def _sb_udp_relay_segment(data: dict[str, object]) -> str | None:
    """Build Surfboard udp-relay segment when source explicitly sets UDP."""
    if "udp-relay" not in data and "udp" not in data:
        return None
    return f"udp-relay={_sb_bool(data.get('udp-relay', data.get('udp')))}"


def _has_unsupported_sb_ss_plugin(data: dict[str, object]) -> str | None:
    """Return unsupported Shadowsocks plugin field for Surfboard."""
    for field_name in ("plugin", "plugin-opts"):
        if field_name in data and data[field_name] not in (None, "", False):
            return field_name
    return None


def _has_unrepresentable_sb_scalar(data: dict[str, object]) -> str | None:
    """Return a Surfboard scalar field that needs unsupported escaping."""
    scalar_fields = (
        "server",
        "port",
        "cipher",
        "method",
        "password",
        "uuid",
        "username",
        "psk",
        "version",
        "reuse",
        "sni",
        "servername",
        "obfs",
        "obfs-host",
        "obfs-uri",
        "obfs-password",
        "hop-interval",
        "down",
        "down-speed",
        "ws-path",
    )
    for field_name in scalar_fields:
        if field_name in data and _profile_scalar_issue(data[field_name]) is not None:
            return field_name
    obfs_opts = data.get("obfs-opts")
    if isinstance(obfs_opts, dict):
        for obfs_key in ("mode", "host", "uri", "obfs", "obfs-host", "obfs-uri"):
            if _profile_scalar_issue(obfs_opts.get(obfs_key)) is not None:
                return f"obfs-opts.{obfs_key}"
    ws_opts = data.get("ws-opts")
    if isinstance(ws_opts, dict):
        if _profile_scalar_issue(ws_opts.get("path")) is not None:
            return "ws-opts.path"
        headers = ws_opts.get("headers")
        if isinstance(headers, dict):
            for key, value in headers.items():
                if (
                    _profile_scalar_issue(key) is not None
                    or _profile_scalar_issue(value) is not None
                ):
                    return "ws-opts.headers"
    return None


def _prepare_surfboard_records(
    route: RouteConfig, records: Sequence[SourceRecord]
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    """Prepare Surfboard records while preserving skip warnings."""
    transformed = apply_transform(
        list(records), filter_config=route.filter, rename_config=route.rename
    )
    normalized_records: list[ProxyRecord] = []
    warnings: list[str] = []
    supported_types = {
        "ss",
        "trojan",
        "vmess",
        "hysteria2",
        "snell",
        "anytls",
        "http",
        "socks5",
    }
    for record in transformed:
        data = dict(record.data)
        critical_field = _has_security_critical_field(data)
        if critical_field is not None:
            warnings.append(
                _skip_warning(
                    data,
                    f"unsupported security-critical field {critical_field}",
                )
            )
            continue
        proxy_type = _string(data.get("type")).lower()
        if proxy_type not in supported_types:
            warnings.append(_skip_warning(data, f"unsupported proxy type {proxy_type}"))
            continue
        if proxy_type == "ss":
            unsupported_ss_field = _has_unsupported_sb_ss_plugin(data)
            if unsupported_ss_field is not None:
                warnings.append(
                    _skip_warning(
                        data,
                        f"unsupported Shadowsocks field {unsupported_ss_field}",
                    )
                )
                continue
        unrepresentable_field = _has_unrepresentable_sb_scalar(data)
        if unrepresentable_field is not None:
            warnings.append(
                _skip_warning(
                    data,
                    f"unsupported comma/control character in {unrepresentable_field}",
                )
            )
            continue
        normalized, normalize_warnings = normalize_proxy(data)
        if normalized is None:
            for warning in normalize_warnings:
                logger.warning(
                    "dropping invalid proxy before render: {warning}",
                    warning=warning,
                )
            continue
        normalized_records.append(ProxyRecord(source=record.source, data=normalized))
    repaired = repair_duplicate_names(normalized_records)
    proxies = [
        cast(dict[str, object], _quote_proxy_strings(dict(record.data)))
        for record in repaired
    ]
    return proxies, tuple(warnings)


def _render_sb_ss(data: dict[str, object]) -> str | None:
    """Render Shadowsocks proxy as a Surfboard proxy line."""
    segments = _sb_base_segments(data, "ss")
    method = _sb_value(data.get("cipher") or data.get("method"))
    password = _sb_value(data.get("password"))
    if segments is None or not method or not password:
        return None
    segments.extend([f"encrypt-method={method}", f"password={password}"])
    udp_relay = _sb_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
    obfs = _sb_value(data.get("obfs"))
    if obfs:
        segments.append(f"obfs={obfs}")
    obfs_host = _sb_value(data.get("obfs-host"))
    if obfs_host:
        segments.append(f"obfs-host={obfs_host}")
    obfs_uri = _sb_value(data.get("obfs-uri"))
    if obfs_uri:
        segments.append(f"obfs-uri={obfs_uri}")
    return ", ".join(segments)


def _render_sb_vmess(data: dict[str, object]) -> str | None:
    """Render VMess proxy as a Surfboard proxy line."""
    segments = _sb_base_segments(data, "vmess")
    uuid = _sb_value(data.get("uuid"))
    if segments is None or not uuid:
        return None
    segments.append(f"username={uuid}")
    udp_relay = _sb_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
    network = _sb_value(data.get("network")).lower()
    if network == "ws":
        segments.append("ws=true")
    elif network and network != "tcp":
        return None
    tls_enabled = _boolish(data.get("tls"))
    if tls_enabled:
        segments.append("tls=true")
    if network == "ws":
        ws_path = _sb_ws_path(data)
        if ws_path:
            segments.append(f"ws-path={ws_path}")
        ws_headers = _sb_ws_headers(data)
        if ws_headers:
            segments.append(f"ws-headers={ws_headers}")
    if tls_enabled:
        if "skip-cert-verify" in data:
            segments.append(
                f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}"
            )
        tls_host = _sb_value(data.get("servername") or data.get("sni"))
        if tls_host:
            segments.append(f"sni={tls_host}")
    alter_id = _string(data.get("alterId"))
    vmess_aead = "false" if alter_id and alter_id != "0" else "true"
    segments.append(f"vmess-aead={vmess_aead}")
    return ", ".join(segments)


def _render_sb_trojan(data: dict[str, object]) -> str | None:
    """Render Trojan proxy as a Surfboard proxy line."""
    segments = _sb_base_segments(data, "trojan")
    password = _sb_value(data.get("password"))
    if segments is None or not password:
        return None
    segments.append(f"password={password}")
    udp_relay = _sb_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
    if "skip-cert-verify" in data:
        segments.append(f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}")
    sni = _sb_value(data.get("sni") or data.get("servername"))
    if sni:
        segments.append(f"sni={sni}")
    network = _sb_value(data.get("network")).lower()
    if network == "ws":
        segments.extend(_sb_ws_segments(data))
    elif network and network != "tcp":
        return None
    return ", ".join(segments)


def _render_sb_hysteria2(data: dict[str, object]) -> str | None:
    """Render Hysteria2 proxy as a Surfboard proxy line.

    Field mapping follows the Surfboard hysteria2 documentation:
    {proxy name} = hysteria2, {server}, {port}, password={password},
    download-bandwidth={bandwidth}, port-hopping={hopping},
    port-hopping-interval={interval}, skip-cert-verify={skip},
    sni={sni}, salamander-password={salamander}, udp-relay={udp}
    """
    segments = _sb_base_segments(data, "hysteria2")
    password = _sb_value(
        data.get("password") or data.get("auth") or data.get("auth-str")
    )
    if segments is None or not password:
        return None
    segments.append(f"password={password}")
    download_bandwidth = _sb_value(data.get("down") or data.get("down-speed"))
    if download_bandwidth:
        segments.append(f"download-bandwidth={download_bandwidth}")
    port_hopping_raw = _string(data.get("ports"))
    if port_hopping_raw:
        port_hopping = _sb_value(port_hopping_raw.replace(",", ";"))
        segments.append(f"port-hopping={port_hopping}")
    hop_interval = _sb_value(data.get("hop-interval"))
    if hop_interval:
        segments.append(f"port-hopping-interval={hop_interval}")
    if "skip-cert-verify" in data:
        segments.append(f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}")
    sni = _sb_value(data.get("sni") or data.get("servername"))
    if sni:
        segments.append(f"sni={sni}")
    obfs = _sb_value(data.get("obfs"))
    obfs_password = _sb_value(data.get("obfs-password"))
    if obfs == "salamander" and obfs_password:
        segments.append(f"salamander-password={obfs_password}")
    udp_relay = _sb_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
    return ", ".join(segments)


def _render_sb_snell(data: dict[str, object]) -> str | None:
    """Render Snell proxy as a Surfboard proxy line.

    Format: {name} = snell, {server}, {port}, psk={psk}, version={version},
    udp-relay={udp}, obfs={obfs}, obfs-host={obfs-host}, obfs-uri={obfs-uri}
    """
    segments = _sb_base_segments(data, "snell")
    psk = _sb_value(data.get("psk"))
    if segments is None or not psk:
        return None
    segments.append(f"psk={psk}")
    version = _sb_value(data.get("version"))
    if version:
        segments.append(f"version={version}")
    udp_relay = _sb_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
    obfs_opts = data.get("obfs-opts")
    if isinstance(obfs_opts, dict):
        obfs = _sb_value(obfs_opts.get("mode") or obfs_opts.get("obfs"))
        if obfs:
            segments.append(f"obfs={obfs}")
        obfs_host = _sb_value(obfs_opts.get("host") or obfs_opts.get("obfs-host"))
        if obfs_host:
            segments.append(f"obfs-host={obfs_host}")
        obfs_uri = _sb_value(obfs_opts.get("uri") or obfs_opts.get("obfs-uri"))
        if obfs_uri:
            segments.append(f"obfs-uri={obfs_uri}")
    return ", ".join(segments)


def _render_sb_anytls(data: dict[str, object]) -> str | None:
    """Render AnyTLS proxy as a Surfboard proxy line.

    Format: {name} = anytls, {server}, {port}, {password}, skip-cert-verify={skip},
    sni={sni}, reuse={reuse}
    """
    segments = _sb_base_segments(data, "anytls")
    password = _sb_value(data.get("password"))
    if segments is None or not password:
        return None
    segments.append(password)
    if "skip-cert-verify" in data:
        segments.append(f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}")
    sni = _sb_value(data.get("sni") or data.get("servername"))
    if sni:
        segments.append(f"sni={sni}")
    if "reuse" in data:
        segments.append(f"reuse={_sb_bool(data.get('reuse'))}")
    return ", ".join(segments)


def _render_sb_http(data: dict[str, object]) -> str | None:
    """Render HTTP/HTTPS proxy as a Surfboard proxy line.

    Format: {name} = {http|https}, {server}, {port}, {username}, {password},
    skip-cert-verify={skip}, sni={sni}
    """
    tls_enabled = _boolish(data.get("tls"))
    protocol = "https" if tls_enabled else "http"
    segments = _sb_base_segments(data, protocol)
    if segments is None:
        return None
    username = _sb_value(data.get("username"))
    password = _sb_value(data.get("password"))
    segments.append(username or "")
    segments.append(password or "")
    if tls_enabled:
        if "skip-cert-verify" in data:
            segments.append(
                f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}"
            )
        sni = _sb_value(data.get("sni") or data.get("servername"))
        if sni:
            segments.append(f"sni={sni}")
    return ", ".join(segments)


def _render_sb_socks5(data: dict[str, object]) -> str | None:
    """Render SOCKS5/SOCKS5-TLS proxy as a Surfboard proxy line.

    Format: {name} = {socks5|socks5-tls}, {server}, {port}, {username}, {password},
    skip-cert-verify={skip}, sni={sni}
    """
    tls_enabled = _boolish(data.get("tls"))
    protocol = "socks5-tls" if tls_enabled else "socks5"
    segments = _sb_base_segments(data, protocol)
    if segments is None:
        return None
    username = _sb_value(data.get("username"))
    password = _sb_value(data.get("password"))
    segments.append(username or "")
    segments.append(password or "")
    if tls_enabled:
        if "skip-cert-verify" in data:
            segments.append(
                f"skip-cert-verify={_sb_bool(data.get('skip-cert-verify'))}"
            )
        sni = _sb_value(data.get("sni") or data.get("servername"))
        if sni:
            segments.append(f"sni={sni}")
    return ", ".join(segments)


def _qx_hostport(data: dict[str, object]) -> str | None:
    """Build Quantumult X host:port endpoint."""
    host = _string(data.get("server"))
    port = _string(data.get("port"))
    if not host or not port:
        return None
    if ":" in host:
        return None
    return f"{host}:{port}"


def _qx_tag(data: dict[str, object]) -> str:
    """Build Quantumult X tag segment."""
    return f"tag={_qx_clean_label(data.get('name'))}"


def _qx_udp_relay_segment(data: dict[str, object]) -> str | None:
    """Build Quantumult X udp-relay segment when source explicitly sets UDP."""
    if "udp-relay" not in data and "udp" not in data:
        return None
    return f"udp-relay={_qx_value(_sb_bool(data.get('udp-relay', data.get('udp'))))}"


def _qx_reality_opts(data: dict[str, object]) -> dict[str, object] | None:
    """Return Reality options when source provides a mapping."""
    reality_opts = data.get("reality-opts")
    if isinstance(reality_opts, dict):
        return cast(dict[str, object], reality_opts)
    return None


def _qx_has_reality(data: dict[str, object]) -> bool:
    """Return whether the proxy carries QX-renderable Reality options."""
    reality_opts = _qx_reality_opts(data)
    return bool(reality_opts and _string(reality_opts.get("public-key")))


def _qx_reality_segments(data: dict[str, object]) -> list[str]:
    """Build Quantumult X Reality parameter segments."""
    reality_opts = _qx_reality_opts(data)
    if not reality_opts:
        return []
    public_key = _string(reality_opts.get("public-key"))
    if not public_key:
        return []
    segments = [f"reality-base64-pubkey={_qx_value(public_key)}"]
    short_id = _string(reality_opts.get("short-id"))
    if short_id:
        segments.append(f"reality-hex-shortid={_qx_value(short_id)}")
    return segments


def _qx_flow_segment(data: dict[str, object], proxy_type: str) -> str | None:
    """Build Quantumult X VLESS flow segment."""
    flow = _string(data.get("flow"))
    if flow == "xtls-rprx-vision" and proxy_type == "vless":
        return "vless-flow=xtls-rprx-vision"
    return None


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
    explicit_obfs = _string(data.get("obfs"))
    if explicit_obfs:
        segments = [f"obfs={_qx_value(explicit_obfs)}"]
        obfs_host = _string(
            data.get("obfs-host") or data.get("servername") or data.get("sni")
        )
        if obfs_host:
            segments.append(f"obfs-host={_qx_value(obfs_host)}")
        obfs_uri = _string(data.get("obfs-uri"))
        if obfs_uri:
            segments.append(f"obfs-uri={_qx_value(obfs_uri)}")
        if explicit_obfs == "over-tls" and "skip-cert-verify" in data:
            verification = "false" if _boolish(data.get("skip-cert-verify")) else "true"
            segments.append(f"tls-verification={verification}")
        return segments
    network = _string(data.get("network"))
    tls_enabled = _boolish(data.get("tls")) or _qx_has_reality(data)
    if not network or network == "tcp":
        if not tls_enabled:
            return []
        segments = ["obfs=over-tls"]
        tls_host = _string(data.get("servername") or data.get("sni"))
        if tls_host:
            segments.append(f"obfs-host={_qx_value(tls_host)}")
        if "skip-cert-verify" in data:
            verification = "false" if _boolish(data.get("skip-cert-verify")) else "true"
            segments.append(f"tls-verification={verification}")
        return segments
    if network == "ws":
        return _qx_ws_segments(data, "wss" if tls_enabled else "ws")
    return None


def _qx_vmess_vless_obfs_segments(data: dict[str, object]) -> list[str] | None:
    """Build Quantumult X VMess/VLESS obfs segments."""
    network = _string(data.get("network"))
    tls_enabled = _boolish(data.get("tls")) or _qx_has_reality(data)
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
        return _qx_ws_segments(data, "wss")
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
        "obfs-opts",
    ):
        if field_name in data and data[field_name] not in (None, "", False):
            return field_name
    return None


def _qx_security_issue(data: dict[str, object], proxy_type: str) -> str | None:
    """Return a security-critical QX field that cannot be rendered safely."""
    for field_name in (
        "ech-opts",
        "fingerprint",
        "certificate",
        "certificate-pinning",
        "pinned-cert",
        "fingerprint-sha256",
    ):
        if field_name in data and data[field_name] not in (None, "", False):
            return f"unsupported security-critical field {field_name}"
    reality_value = data.get("reality-opts")
    if reality_value not in (None, "", False):
        if not isinstance(reality_value, dict):
            return "unsupported security-critical field reality-opts"
        if not _string(reality_value.get("public-key")):
            return "unsupported security-critical field reality-opts.public-key"
        explicit_obfs = _string(data.get("obfs"))
        if (
            proxy_type == "ss"
            and explicit_obfs
            and explicit_obfs
            not in {
                "over-tls",
                "wss",
            }
        ):
            return f"unsupported Reality obfs {explicit_obfs}"
        if _string(data.get("network")) == "grpc":
            return "unsupported Reality transport grpc"
    flow = _string(data.get("flow"))
    if flow:
        if proxy_type != "vless":
            return f"unsupported flow for {proxy_type}"
        if flow != "xtls-rprx-vision":
            return f"unsupported flow {flow}"
    return None


def _has_unrepresentable_qx_scalar(data: dict[str, object]) -> str | None:
    """Return a Quantumult X scalar field that needs unsupported escaping."""
    if ":" in _string(data.get("server")):
        return "server"
    scalar_fields = (
        "server",
        "port",
        "cipher",
        "method",
        "password",
        "uuid",
        "sni",
        "servername",
        "obfs",
        "obfs-host",
        "obfs-uri",
        "flow",
    )
    for field_name in scalar_fields:
        if field_name in data and _profile_scalar_issue(data[field_name]) is not None:
            return field_name
    reality_opts = _qx_reality_opts(data)
    if reality_opts is not None:
        for field_name in ("public-key", "short-id"):
            if _profile_scalar_issue(reality_opts.get(field_name)) is not None:
                return f"reality-opts.{field_name}"
    ws_opts = data.get("ws-opts")
    if isinstance(ws_opts, dict):
        if _profile_scalar_issue(ws_opts.get("path")) is not None:
            return "ws-opts.path"
        headers = ws_opts.get("headers")
        if isinstance(headers, dict):
            for key, value in headers.items():
                if (
                    _profile_scalar_issue(key) is not None
                    or _profile_scalar_issue(value) is not None
                ):
                    return "ws-opts.headers"
    grpc_opts = data.get("grpc-opts")
    if (
        isinstance(grpc_opts, dict)
        and _profile_scalar_issue(grpc_opts.get("grpc-service-name")) is not None
    ):
        return "grpc-opts.grpc-service-name"
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
    segments.extend(_qx_reality_segments(data))
    udp_relay = _qx_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
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
    segments.extend(_qx_reality_segments(data))
    udp_relay = _qx_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
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
    segments.extend(_qx_reality_segments(data))
    flow = _qx_flow_segment(data, "vless")
    if flow:
        segments.append(flow)
    udp_relay = _qx_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
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
    method = _string(data.get("cipher") or data.get("method") or "none")
    if method == "auto":
        method = "none"
    segments = [
        f"vmess={hostport}",
        f"method={_qx_value(method)}",
        f"password={_qx_value(uuid)}",
    ]
    segments.extend(transport)
    segments.extend(_qx_reality_segments(data))
    alter_id = _string(data.get("alterId") or data.get("alter-id"))
    if alter_id and alter_id != "0":
        segments.append("aead=false")
    udp_relay = _qx_udp_relay_segment(data)
    if udp_relay:
        segments.append(udp_relay)
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


def _has_unsupported_xray_ss_plugin(data: dict[str, object]) -> str | None:
    """Return unsupported Shadowsocks SIP002 plugin field for xray-uri."""
    for field_name in ("plugin", "plugin-opts"):
        if field_name in data and data[field_name] not in (None, "", False):
            return field_name
    return None


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


def _render_hysteria2_uri(data: dict[str, object]) -> str | None:
    """Render Hysteria2 proxy as Hysteria2 URI scheme."""
    host = _string(data.get("server"))
    port = _string(data.get("ports") or data.get("port"))
    password = _string(data.get("password") or data.get("auth") or data.get("auth-str"))
    if not host or not password:
        return None
    authority = _uri_host(host)
    if port:
        authority = f"{authority}:{port}"
    params: dict[str, str] = {}
    sni = _string(data.get("sni") or data.get("servername"))
    if sni:
        params["sni"] = sni
    if _boolish(data.get("skip-cert-verify")):
        params["insecure"] = "1"
    obfs = _string(data.get("obfs"))
    if obfs:
        params["obfs"] = obfs
    obfs_password = _string(data.get("obfs-password"))
    if obfs_password:
        params["obfs-password"] = obfs_password
    query = _query_string(params)
    query_part = f"?{query}" if query else ""
    return (
        f"hysteria2://{quote(password, safe='')}@{authority}/"
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
    return (
        f"vless://{quote(uuid, safe='')}@{hostport}{query_part}#{_encoded_name(data)}"
    )


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
            "hysteria2": _render_hysteria2_uri,
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
            if proxy_type == "ss":
                unsupported_ss_field = _has_unsupported_xray_ss_plugin(proxy)
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


class SurfboardRenderer:
    """Render route records as Surfboard full profile output."""

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        """Return Surfboard nodes companion path."""
        return (f"{route.path}-nodes",)

    def render(self, request: RenderRequest) -> RenderResponse:
        """Render Surfboard profile or nodes companion response."""
        proxies, prepare_warnings = _prepare_surfboard_records(
            request.route, request.records
        )
        warnings: list[str] = list(prepare_warnings)
        lines: list[str] = []
        names: list[str] = []
        renderers = {
            "ss": _render_sb_ss,
            "trojan": _render_sb_trojan,
            "vmess": _render_sb_vmess,
            "hysteria2": _render_sb_hysteria2,
            "snell": _render_sb_snell,
            "anytls": _render_sb_anytls,
            "http": _render_sb_http,
            "socks5": _render_sb_socks5,
        }

        for proxy in proxies:
            proxy_type = _string(proxy.get("type")).lower()
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
                    if network and network not in {"tcp", "ws"}
                    else "missing required Surfboard fields"
                )
                warnings.append(_skip_warning(proxy, reason))
                continue
            lines.append(line)
            names.append(_sb_label(proxy.get("name")))

        if not lines:
            if not warnings:
                warnings.append("no supported nodes for surfboard output")
            return RenderResponse(
                body=b"no supported nodes for surfboard output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )

        body = (
            "\n".join(lines) + "\n"
            if request.companion == "nodes"
            else self._render_profile(request, lines, names)
        )
        return RenderResponse(
            body=body.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )

    def _render_profile(
        self, request: RenderRequest, proxy_lines: list[str], names: list[str]
    ) -> str:
        """Render full Surfboard profile sections."""
        output = request.route.output
        nodes_url = _sb_value(request.companion_public_urls.get("nodes", ""))
        name_segments = ", ".join(names)
        proxy_prefix = f"{name_segments}, " if name_segments else ""
        auto_group = (
            f"Auto = url-test, {proxy_prefix}"
            f"policy-path={nodes_url}, policy-regex-filter=.*, "
            f"url={_sb_value(output.test_url)}, "
            f"interval={output.test_interval}, "
            f"tolerance={output.test_tolerance}, "
            f"timeout={output.test_timeout}"
        )
        proxy_group = (
            f"Proxy = select, {proxy_prefix}"
            f"policy-path={nodes_url}, policy-regex-filter=.*"
        )
        sections = [
            "[General]",
            "",
            "[Proxy]",
            *proxy_lines,
            "",
            "[Proxy Group]",
            "Main = select, Auto, Proxy, DIRECT",
            auto_group,
            proxy_group,
            "",
            "[Rule]",
            "FINAL,Main",
        ]
        return "\n".join(sections) + "\n"

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
            proxy_type = _string(proxy.get("type")).lower()
            security_issue = _qx_security_issue(proxy, proxy_type)
            if security_issue is not None:
                warnings.append(_skip_warning(proxy, security_issue))
                continue
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
            unrepresentable_field = _has_unrepresentable_qx_scalar(proxy)
            if unrepresentable_field is not None:
                warnings.append(
                    _skip_warning(
                        proxy,
                        f"unsupported comma/control or IPv6 field {unrepresentable_field}",
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

    async def render(
        self, route: RouteConfig, records: Sequence[SourceRecord]
    ) -> bytes:
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


def build_renderer_registry(
    *, yaml_sort_keys: bool = False
) -> dict[str, RouteRenderer]:
    """Build route renderer registry keyed by route output format."""
    return {
        "provider": ProviderRouteRenderer(
            ProviderRenderer(yaml_sort_keys=yaml_sort_keys)
        ),
        "xray-uri": XrayUriRenderer(),
        "quantumult-x": QuantumultXRenderer(),
        "surfboard": SurfboardRenderer(),
    }
