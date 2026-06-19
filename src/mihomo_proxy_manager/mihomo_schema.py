"""Mihomo proxy schema validation and normalization.

Schema source: MetaCubeX/mihomo ``Meta`` branch, tag ``v1.19.27``
(``5184081ac327394d9e15fa5d5f9f4a61e723fd94``), especially
``adapter/parser.go`` and ``adapter/outbound/*Option`` ``proxy`` tags.
"""

from __future__ import annotations

import re
from typing import Any, Literal

FieldKind = Literal[
    "string",
    "number",
    "bool",
    "string-list",
    "number-list",
    "string-map",
    "string-list-map",
    "any-map",
]
SchemaValue = FieldKind | dict[str, "SchemaValue"]
Schema = dict[str, SchemaValue]

COMMON_PROXY_FIELDS: Schema = {
    "tfo": "bool",
    "mptcp": "bool",
    "interface-name": "string",
    "routing-mark": "number",
    "ip-version": "string",
    "dialer-proxy": "string",
    "smux": {
        "enabled": "bool",
        "protocol": "string",
        "max-connections": "number",
        "min-streams": "number",
        "max-streams": "number",
        "padding": "bool",
        "statistic": "bool",
        "only-tcp": "bool",
        "brutal-opts": {"enabled": "bool", "up": "string", "down": "string"},
    },
}

ECH_OPTS: Schema = {
    "enable": "bool",
    "config": "string",
    "query-server-name": "string",
}

REALITY_OPTS: Schema = {
    "public-key": "string",
    "short-id": "string",
    "support-x25519mlkem768": "bool",
}

HTTP_OPTS: Schema = {
    "method": "string",
    "path": "string-list",
    "headers": "string-list-map",
}

H2_OPTS: Schema = {"host": "string-list", "path": "string"}

GRPC_OPTS: Schema = {
    "grpc-service-name": "string",
    "grpc-user-agent": "string",
    "ping-interval": "number",
    "max-connections": "number",
    "min-streams": "number",
    "max-streams": "number",
}

WS_OPTS: Schema = {
    "path": "string",
    "headers": "string-map",
    "max-early-data": "number",
    "early-data-header-name": "string",
    "v2ray-http-upgrade": "bool",
    "v2ray-http-upgrade-fast-open": "bool",
}

XHTTP_REUSE_SETTINGS: Schema = {
    "max-concurrency": "string",
    "max-connections": "string",
    "c-max-reuse-times": "string",
    "h-max-request-times": "string",
    "h-max-reusable-secs": "string",
    "h-keep-alive-period": "number",
}

XHTTP_DOWNLOAD_SETTINGS: Schema = {
    "path": "string",
    "host": "string",
    "headers": "string-map",
    "reuse-settings": XHTTP_REUSE_SETTINGS,
    "server": "string",
    "port": "number",
    "tls": "bool",
    "alpn": "string-list",
    "ech-opts": ECH_OPTS,
    "reality-opts": REALITY_OPTS,
    "skip-cert-verify": "bool",
    "fingerprint": "string",
    "certificate": "string",
    "private-key": "string",
    "servername": "string",
    "client-fingerprint": "string",
}

XHTTP_OPTS: Schema = {
    "path": "string",
    "host": "string",
    "mode": "string",
    "headers": "string-map",
    "no-grpc-header": "bool",
    "x-padding-bytes": "string",
    "x-padding-obfs-mode": "bool",
    "x-padding-key": "string",
    "x-padding-header": "string",
    "x-padding-placement": "string",
    "x-padding-method": "string",
    "uplink-http-method": "string",
    "session-placement": "string",
    "session-key": "string",
    "seq-placement": "string",
    "seq-key": "string",
    "uplink-data-placement": "string",
    "uplink-data-key": "string",
    "uplink-chunk-size": "string",
    "sc-max-each-post-bytes": "string",
    "sc-min-posts-interval-ms": "string",
    "reuse-settings": XHTTP_REUSE_SETTINGS,
    "download-settings": XHTTP_DOWNLOAD_SETTINGS,
}

AMNEZIA_WG_OPTION: Schema = {
    "jc": "number",
    "jmin": "number",
    "jmax": "number",
    "s1": "number",
    "s2": "number",
    "s3": "number",
    "s4": "number",
    "h1": "string",
    "h2": "string",
    "h3": "string",
    "h4": "string",
    "i1": "string",
    "i2": "string",
    "i3": "string",
    "i4": "string",
    "i5": "string",
    "j1": "string",
    "j2": "string",
    "j3": "string",
    "itime": "number",
}

WIREGUARD_PEER: Schema = {
    "server": "string",
    "port": "number",
    "public-key": "string",
    "pre-shared-key": "string",
    "reserved": "number-list",
    "allowed-ips": "string-list",
}

HYSTERIA2_REALM_OPTS: Schema = {
    "enable": "bool",
    "server-url": "string",
    "token": "string",
    "realm-id": "string",
    "stun-servers": "string-list",
    "sni": "string",
    "skip-cert-verify": "bool",
    "fingerprint": "string",
    "certificate": "string",
    "private-key": "string",
    "alpn": "string-list",
}

TROJAN_SS_OPTS: Schema = {
    "enabled": "bool",
    "method": "string",
    "password": "string",
}

SUDOKU_HTTPMASK: Schema = {
    "disable": "bool",
    "mode": "string",
    "tls": "bool",
    "host": "string",
    "path-root": "string",
    "multiplex": "string",
}

PROXY_SCHEMAS: dict[str, Schema] = {
    "ss": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "password": "string",
        "cipher": "string",
        "udp": "bool",
        "plugin": "string",
        "plugin-opts": "any-map",
        "udp-over-tcp": "bool",
        "udp-over-tcp-version": "number",
        "client-fingerprint": "string",
    },
    "ssr": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "password": "string",
        "cipher": "string",
        "obfs": "string",
        "obfs-param": "string",
        "protocol": "string",
        "protocol-param": "string",
        "udp": "bool",
    },
    "socks5": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "username": "string",
        "password": "string",
        "tls": "bool",
        "udp": "bool",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
    },
    "http": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "username": "string",
        "password": "string",
        "tls": "bool",
        "sni": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "headers": "string-map",
    },
    "vmess": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "uuid": "string",
        "alterId": "number",
        "cipher": "string",
        "udp": "bool",
        "network": "string",
        "tls": "bool",
        "alpn": "string-list",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "servername": "string",
        "ech-opts": ECH_OPTS,
        "reality-opts": REALITY_OPTS,
        "http-opts": HTTP_OPTS,
        "h2-opts": H2_OPTS,
        "grpc-opts": GRPC_OPTS,
        "ws-opts": WS_OPTS,
        "packet-addr": "bool",
        "xudp": "bool",
        "packet-encoding": "string",
        "global-padding": "bool",
        "authenticated-length": "bool",
        "client-fingerprint": "string",
    },
    "vless": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "uuid": "string",
        "flow": "string",
        "tls": "bool",
        "alpn": "string-list",
        "udp": "bool",
        "packet-addr": "bool",
        "xudp": "bool",
        "packet-encoding": "string",
        "encryption": "string",
        "network": "string",
        "ech-opts": ECH_OPTS,
        "reality-opts": REALITY_OPTS,
        "http-opts": HTTP_OPTS,
        "h2-opts": H2_OPTS,
        "grpc-opts": GRPC_OPTS,
        "ws-opts": WS_OPTS,
        "xhttp-opts": XHTTP_OPTS,
        "ws-headers": "string-map",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "servername": "string",
        "client-fingerprint": "string",
    },
    "snell": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "psk": "string",
        "udp": "bool",
        "version": "number",
        "reuse": "bool",
        "obfs-opts": "any-map",
    },
    "trojan": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "password": "string",
        "alpn": "string-list",
        "sni": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "udp": "bool",
        "network": "string",
        "ech-opts": ECH_OPTS,
        "reality-opts": REALITY_OPTS,
        "grpc-opts": GRPC_OPTS,
        "ws-opts": WS_OPTS,
        "ss-opts": TROJAN_SS_OPTS,
        "client-fingerprint": "string",
    },
    "hysteria": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "ports": "string",
        "protocol": "string",
        "obfs-protocol": "string",
        "up": "string",
        "up-speed": "number",
        "down": "string",
        "down-speed": "number",
        "auth": "string",
        "auth-str": "string",
        "obfs": "string",
        "sni": "string",
        "ech-opts": ECH_OPTS,
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "alpn": "string-list",
        "recv-window-conn": "number",
        "recv-window": "number",
        "disable-mtu-discovery": "bool",
        "fast-open": "bool",
        "hop-interval": "number",
    },
    "hysteria2": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "ports": "string",
        "hop-interval": "string",
        "up": "string",
        "down": "string",
        "password": "string",
        "obfs": "string",
        "obfs-password": "string",
        "obfs-min-packet-size": "number",
        "obfs-max-packet-size": "number",
        "sni": "string",
        "ech-opts": ECH_OPTS,
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "alpn": "string-list",
        "cwnd": "number",
        "bbr-profile": "string",
        "udp-mtu": "number",
        "realm-opts": HYSTERIA2_REALM_OPTS,
        "initial-stream-receive-window": "number",
        "max-stream-receive-window": "number",
        "initial-connection-receive-window": "number",
        "max-connection-receive-window": "number",
    },
    "wireguard": {
        "name": "string",
        "type": "string",
        "ip": "string",
        "ipv6": "string",
        "private-key": "string",
        "workers": "number",
        "mtu": "number",
        "udp": "bool",
        "persistent-keepalive": "number",
        "amnezia-wg-option": AMNEZIA_WG_OPTION,
        "peers": {"*": WIREGUARD_PEER},
        "remote-dns-resolve": "bool",
        "dns": "string-list",
        "refresh-server-ip-interval": "number",
    },
    "tuic": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "token": "string",
        "uuid": "string",
        "password": "string",
        "ip": "string",
        "heartbeat-interval": "number",
        "alpn": "string-list",
        "reduce-rtt": "bool",
        "request-timeout": "number",
        "udp-relay-mode": "string",
        "congestion-controller": "string",
        "disable-sni": "bool",
        "max-udp-relay-packet-size": "number",
        "fast-open": "bool",
        "max-open-streams": "number",
        "cwnd": "number",
        "bbr-profile": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "recv-window-conn": "number",
        "recv-window": "number",
        "disable-mtu-discovery": "bool",
        "max-datagram-frame-size": "number",
        "sni": "string",
        "ech-opts": ECH_OPTS,
        "udp-over-stream": "bool",
        "udp-over-stream-version": "number",
    },
    "gost-relay": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "forward": "bool",
        "udp": "bool",
        "tls": "bool",
        "mux": "bool",
        "sni": "string",
        "username": "string",
        "password": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "client-fingerprint": "string",
    },
    "direct": {"name": "string", "type": "string"},
    "dns": {"name": "string", "type": "string"},
    "reject": {"name": "string", "type": "string"},
    "ssh": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "username": "string",
        "password": "string",
        "private-key": "string",
        "private-key-passphrase": "string",
        "host-key": "string-list",
        "host-key-algorithms": "string-list",
    },
    "mieru": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "port-range": "string",
        "transport": "string",
        "udp": "bool",
        "username": "string",
        "password": "string",
        "multiplexing": "string",
        "handshake-mode": "string",
        "traffic-pattern": "string",
    },
    "anytls": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "password": "string",
        "alpn": "string-list",
        "sni": "string",
        "ech-opts": ECH_OPTS,
        "client-fingerprint": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "udp": "bool",
        "idle-session-check-interval": "number",
        "idle-session-timeout": "number",
        "min-idle-session": "number",
    },
    "sudoku": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "key": "string",
        "aead-method": "string",
        "padding-min": "number",
        "padding-max": "number",
        "table-type": "string",
        "enable-pure-downlink": "bool",
        "http-mask": "bool",
        "http-mask-mode": "string",
        "http-mask-tls": "bool",
        "http-mask-host": "string",
        "path-root": "string",
        "http-mask-multiplex": "string",
        "httpmask": SUDOKU_HTTPMASK,
        "custom-table": "string",
        "custom-tables": "string-list",
    },
    "masque": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "private-key": "string",
        "public-key": "string",
        "ip": "string",
        "ipv6": "string",
        "uri": "string",
        "sni": "string",
        "mtu": "number",
        "udp": "bool",
        "skip-cert-verify": "bool",
        "network": "string",
        "congestion-controller": "string",
        "cwnd": "number",
        "bbr-profile": "string",
        "remote-dns-resolve": "bool",
        "dns": "string-list",
    },
    "trusttunnel": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "username": "string",
        "password": "string",
        "alpn": "string-list",
        "sni": "string",
        "ech-opts": ECH_OPTS,
        "client-fingerprint": "string",
        "skip-cert-verify": "bool",
        "fingerprint": "string",
        "certificate": "string",
        "private-key": "string",
        "udp": "bool",
        "health-check": "bool",
        "quic": "bool",
        "congestion-controller": "string",
        "cwnd": "number",
        "bbr-profile": "string",
        "max-connections": "number",
        "min-streams": "number",
        "max-streams": "number",
    },
    "openvpn": {
        "name": "string",
        "type": "string",
        "server": "string",
        "port": "number",
        "proto": "string",
        "dev": "string",
        "cipher": "string",
        "auth": "string",
        "comp-lzo": "string",
        "ca": "string",
        "cert": "string",
        "key": "string",
        "tls-crypt": "string",
        "username": "string",
        "password": "string",
        "ping": "number",
        "ping-restart": "number",
        "mtu": "number",
        "udp": "bool",
        "remote-dns-resolve": "bool",
        "dns": "string-list",
    },
    "tailscale": {
        "name": "string",
        "type": "string",
        "hostname": "string",
        "auth-key": "string",
        "control-url": "string",
        "state-dir": "string",
        "ephemeral": "bool",
        "udp": "bool",
        "accept-routes": "bool",
        "exit-node": "string",
        "exit-node-allow-lan-access": "bool",
    },
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ss": ("name", "type", "server", "port", "cipher", "password"),
    "ssr": ("name", "type", "server", "port", "cipher", "password", "obfs", "protocol"),
    "socks5": ("name", "type", "server", "port"),
    "http": ("name", "type", "server", "port"),
    "vmess": ("name", "type", "server", "port", "uuid", "cipher"),
    "vless": ("name", "type", "server", "port", "uuid"),
    "snell": ("name", "type", "server", "port", "psk"),
    "trojan": ("name", "type", "server", "port", "password"),
    "hysteria": ("name", "type", "server", "up", "down"),
    "hysteria2": ("name", "type", "server", "port", "password"),
    "wireguard": ("name", "type", "ip", "private-key"),
    "tuic": ("name", "type", "server", "port"),
    "gost-relay": ("name", "type", "server", "port"),
    "direct": ("name", "type"),
    "dns": ("name", "type"),
    "reject": ("name", "type"),
    "ssh": ("name", "type", "server", "port", "username"),
    "mieru": ("name", "type", "server", "transport", "username", "password"),
    "anytls": ("name", "type", "server", "port", "password"),
    "sudoku": ("name", "type", "server", "port", "key"),
    "masque": ("name", "type", "server", "port", "private-key", "public-key"),
    "trusttunnel": ("name", "type", "server", "port", "username", "password"),
    "openvpn": ("name", "type", "server", "port", "ca"),
    "tailscale": ("name", "type"),
}

_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")


def normalize_proxy(proxy: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize one proxy against Mihomo schema, returning warnings on failure."""
    warnings: list[str] = []
    raw_type = proxy.get("type")
    if raw_type is None or raw_type == "":
        return None, ["proxy missing required field 'type'"]
    proxy_type = str(raw_type).lower()
    schema = PROXY_SCHEMAS.get(proxy_type)
    if schema is None:
        return None, [f"proxy {proxy.get('name', '<unnamed>')!r} unsupported proxy type {raw_type!r}"]

    merged_schema = {**COMMON_PROXY_FIELDS, **schema}
    normalized: dict[str, Any] = {"type": proxy_type}
    for field, value in proxy.items():
        kind = merged_schema.get(str(field))
        if kind is None:
            normalized[str(field)] = value
            continue
        normalized_value, error = _normalize_value(str(field), value, kind)
        if error:
            warnings.append(f"proxy {proxy.get('name', '<unnamed>')!r} {error}")
            continue
        normalized[str(field)] = normalized_value

    for field in REQUIRED_FIELDS.get(proxy_type, ("name", "type")):
        if field not in normalized or normalized[field] in (None, ""):
            warnings.append(
                f"proxy {proxy.get('name', '<unnamed>')!r} missing required field {field!r}"
            )

    _validate_reality_options(normalized, warnings)
    return (None, warnings) if warnings else (normalized, [])


def _normalize_value(
    field: str, value: Any, kind: SchemaValue
) -> tuple[Any | None, str | None]:
    if isinstance(kind, dict):
        if "*" in kind:
            if not isinstance(value, list):
                return None, f"field {field!r} must be list"
            item_schema = kind["*"]
            normalized_items = []
            for index, item in enumerate(value):
                normalized_item, error = _normalize_value(
                    f"{field}[{index}]", item, item_schema
                )
                if error:
                    return None, error
                normalized_items.append(normalized_item)
            return normalized_items, None
        if not isinstance(value, dict):
            return None, f"field {field!r} must be map"
        normalized = {}
        for key, item in value.items():
            child_kind = kind.get(str(key))
            if child_kind is None:
                normalized[str(key)] = item
                continue
            normalized_item, error = _normalize_value(f"{field}.{key!s}", item, child_kind)
            if error:
                return None, error
            normalized[str(key)] = normalized_item
        return normalized, None

    if kind == "string":
        return _to_string(value), None
    if kind == "number":
        number = _to_int(value)
        if number is None:
            return None, f"field {field!r} must be number"
        return number, None
    if kind == "bool":
        boolean = _to_bool(value)
        if boolean is None:
            return None, f"field {field!r} must be bool"
        return boolean, None
    if kind == "string-list":
        values = value.split(",") if isinstance(value, str) else value
        if not isinstance(values, list):
            return None, f"field {field!r} must be list"
        return [_to_string(item) for item in values], None
    if kind == "number-list":
        if not isinstance(value, list):
            return None, f"field {field!r} must be list"
        numbers = [_to_int(item) for item in value]
        if any(item is None for item in numbers):
            return None, f"field {field!r} must be number list"
        return numbers, None
    if kind == "string-map":
        if not isinstance(value, dict):
            return None, f"field {field!r} must be map"
        return {str(key): _to_string(item) for key, item in value.items()}, None
    if kind == "string-list-map":
        if not isinstance(value, dict):
            return None, f"field {field!r} must be map"
        normalized_map = {}
        for key, item in value.items():
            normalized_item, error = _normalize_value(
                f"{field}.{key!s}", item, "string-list"
            )
            if error:
                return None, error
            normalized_map[str(key)] = normalized_item
        return normalized_map, None
    if kind == "any-map":
        if not isinstance(value, dict):
            return None, f"field {field!r} must be map"
        return {str(key): item for key, item in value.items()}, None
    raise AssertionError(f"unknown schema kind: {kind!r}")


def _to_string(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "t", "true", "y", "yes", "on"}:
            return True
        if text in {"0", "f", "false", "n", "no", "off"}:
            return False
    return None


def _validate_reality_options(proxy: dict[str, Any], warnings: list[str]) -> None:
    opts = proxy.get("reality-opts")
    if not isinstance(opts, dict) or not opts.get("public-key"):
        return
    short_id = opts.get("short-id", "")
    if not isinstance(short_id, str):
        warnings.append(f"proxy {proxy.get('name', '<unnamed>')!r} reality-opts.short-id must be string")
        return
    if len(short_id) % 2 != 0 or len(short_id) > 16 or not _HEX_RE.fullmatch(short_id):
        warnings.append(
            f"proxy {proxy.get('name', '<unnamed>')!r} reality-opts.short-id must be even-length hex up to 16 chars"
        )
