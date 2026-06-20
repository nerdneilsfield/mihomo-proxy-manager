"""Mihomo proxy schema validation and normalization.

Schema source: MetaCubeX/mihomo ``Meta`` branch, tag ``v1.19.27``
(``5184081ac327394d9e15fa5d5f9f4a61e723fd94``), especially
``adapter/parser.go`` and ``adapter/outbound/*Option`` ``proxy`` tags.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import uuid
from typing import Any, Literal, cast

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
_RATE_RE = re.compile(r"^(\d+)\s*([KMGT]?)([Bb])ps$")
_PATH_ROOT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PEM_RE = re.compile(
    r"-----BEGIN [^-]+-----\s+([A-Za-z0-9+/=\s]+)-----END [^-]+-----",
    re.DOTALL,
)

SS_CIPHERS = {
    "none",
    "aes-128-gcm",
    "aes-192-gcm",
    "aes-256-gcm",
    "chacha20-ietf-poly1305",
    "xchacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
}
SSR_STREAM_CIPHERS = {
    "none",
    "dummy",
    "rc4-md5",
    "aes-128-cfb",
    "aes-192-cfb",
    "aes-256-cfb",
    "aes-128-ctr",
    "aes-192-ctr",
    "aes-256-ctr",
    "chacha20",
    "chacha20-ietf",
    "xchacha20",
}
SSR_OBFS = {"plain", "tls1.2_ticket_auth", "tls1.2_ticket_fastauth"}
SSR_PROTOCOLS = {
    "origin",
    "auth_sha1_v4",
    "auth_aes128_md5",
    "auth_aes128_sha1",
    "auth_chain_a",
    "auth_chain_b",
}
VMESS_CIPHERS = {"auto", "aes-128-gcm", "chacha20-poly1305", "none"}
TROJAN_SS_CIPHERS = {"aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305"}
SUDOKU_TABLE_TYPES = {
    "",
    "prefer_ascii",
    "prefer_entropy",
    "up_ascii_down_entropy",
    "up_entropy_down_ascii",
}
OPENVPN_CIPHERS = {
    "",
    "AES-128-GCM",
    "AES-192-GCM",
    "AES-256-GCM",
    "AES-CBC",
    "AES-128-CBC",
    "AES-192-CBC",
    "AES-256-CBC",
    "CHACHA20-POLY1305",
}
OPENVPN_AUTHS = {"", "MD5", "SHA1", "SHA-1", "SHA256", "SHA384", "SHA512"}
MIERU_MULTIPLEXING = {
    "",
    "MULTIPLEXING_DEFAULT",
    "MULTIPLEXING_OFF",
    "MULTIPLEXING_LOW",
    "MULTIPLEXING_MIDDLE",
    "MULTIPLEXING_HIGH",
}
MIERU_HANDSHAKE_MODES = {
    "",
    "HANDSHAKE_STANDARD",
    "HANDSHAKE_NO_WAIT",
    "HANDSHAKE_RANDOM_PADDING",
}


def normalize_proxy(proxy: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize one proxy against Mihomo schema, returning warnings on failure."""
    warnings: list[str] = []
    raw_type = proxy.get("type")
    if raw_type is None or raw_type == "":
        return None, ["proxy missing required field 'type'"]
    proxy_type = str(raw_type).lower()
    schema = PROXY_SCHEMAS.get(proxy_type)
    if schema is None:
        return None, [
            f"proxy {proxy.get('name', '<unnamed>')!r} unsupported proxy type {raw_type!r}"
        ]

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

    _validate_known_content(normalized, warnings)
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
            normalized_item, error = _normalize_value(
                f"{field}.{key!s}", item, child_kind
            )
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


def _warn(proxy: dict[str, Any], warnings: list[str], message: str) -> None:
    warnings.append(f"proxy {proxy.get('name', '<unnamed>')!r} {message}")


def _validate_known_content(proxy: dict[str, Any], warnings: list[str]) -> None:
    proxy_type = str(proxy.get("type", "")).lower()
    _validate_nested_crypto_options(proxy, warnings)
    _validate_common_proxy_values(proxy, warnings)
    if proxy_type == "ss":
        _validate_ss(proxy, warnings)
    elif proxy_type == "ssr":
        _validate_ssr(proxy, warnings)
    elif proxy_type == "vmess":
        _validate_vmess(proxy, warnings)
    elif proxy_type == "vless":
        _validate_vless(proxy, warnings)
    elif proxy_type == "snell":
        _validate_snell(proxy, warnings)
    elif proxy_type == "trojan":
        _validate_trojan(proxy, warnings)
    elif proxy_type == "hysteria":
        _validate_hysteria(proxy, warnings)
    elif proxy_type == "hysteria2":
        _validate_hysteria2(proxy, warnings)
    elif proxy_type == "wireguard":
        _validate_wireguard(proxy, warnings)
    elif proxy_type == "tuic":
        _validate_uot_version(proxy, warnings, "udp-over-stream-version")
    elif proxy_type == "gost-relay":
        if not proxy.get("server") or not _valid_port(proxy.get("port")):
            _warn(proxy, warnings, "requires a valid server and port")
    elif proxy_type == "ssh":
        _validate_ssh(proxy, warnings)
    elif proxy_type == "mieru":
        _validate_mieru(proxy, warnings)
    elif proxy_type == "sudoku":
        _validate_sudoku(proxy, warnings)
    elif proxy_type == "masque":
        _validate_masque(proxy, warnings)
    elif proxy_type == "openvpn":
        _validate_openvpn(proxy, warnings)


def _validate_common_proxy_values(proxy: dict[str, Any], warnings: list[str]) -> None:
    for field in ("server", "name", "password", "key", "username"):
        if (
            field in REQUIRED_FIELDS.get(str(proxy.get("type", "")).lower(), ())
            and proxy.get(field) == ""
        ):
            _warn(proxy, warnings, f"{field} is empty")


def _validate_nested_crypto_options(
    value: Any, warnings: list[str], proxy: dict[str, Any] | None = None, path: str = ""
) -> None:
    if proxy is None and isinstance(value, dict):
        proxy = value
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "reality-opts" and isinstance(item, dict) and proxy is not None:
                _validate_reality_options(proxy, item, warnings, child_path)
            elif key == "ech-opts" and isinstance(item, dict) and proxy is not None:
                _validate_ech_options(proxy, item, warnings, child_path)
            else:
                _validate_nested_crypto_options(item, warnings, proxy, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_nested_crypto_options(item, warnings, proxy, f"{path}[{index}]")


def _validate_reality_options(
    proxy: dict[str, Any], opts: dict[str, Any], warnings: list[str], path: str
) -> None:
    public_key = opts.get("public-key", "")
    if not public_key:
        return
    decoded = _decode_raw_url_base64(str(public_key))
    if decoded is None or len(decoded) != 32:
        _warn(proxy, warnings, f"{path}.public-key invalid REALITY public key")
    short_id = opts.get("short-id", "")
    if not isinstance(short_id, str):
        _warn(proxy, warnings, f"{path}.short-id must be string")
        return
    if len(short_id) % 2 != 0 or len(short_id) > 16 or not _HEX_RE.fullmatch(short_id):
        _warn(
            proxy, warnings, f"{path}.short-id must be even-length hex up to 16 chars"
        )


def _validate_ech_options(
    proxy: dict[str, Any], opts: dict[str, Any], warnings: list[str], path: str
) -> None:
    if not opts.get("enable") or not opts.get("config"):
        return
    if _decode_std_base64(str(opts["config"])) is None:
        _warn(proxy, warnings, f"{path}.config must be standard base64")


def _validate_ss(proxy: dict[str, Any], warnings: list[str]) -> None:
    cipher = str(proxy.get("cipher", "")).lower()
    if cipher not in SS_CIPHERS:
        _warn(proxy, warnings, f"cipher {proxy.get('cipher')!r} not supported")
    _validate_uot_version(proxy, warnings, "udp-over-tcp-version")
    plugin = proxy.get("plugin")
    opts = proxy.get("plugin-opts")
    if plugin == "obfs":
        mode = opts.get("mode") if isinstance(opts, dict) else ""
        if mode not in {"tls", "http"}:
            _warn(proxy, warnings, f"plugin-opts.mode obfs mode error: {mode}")
    elif plugin in {"v2ray-plugin", "gost-plugin"}:
        mode = opts.get("mode") if isinstance(opts, dict) else ""
        if mode != "websocket":
            _warn(proxy, warnings, f"plugin-opts.mode obfs mode error: {mode}")
    elif plugin == "shadow-tls":
        if not isinstance(opts, dict) or not opts.get("host"):
            _warn(proxy, warnings, "shadow-tls plugin-opts.host is required")
    elif plugin == "restls":
        if (
            not isinstance(opts, dict)
            or not opts.get("host")
            or not opts.get("password")
            or not opts.get("version-hint")
        ):
            _warn(
                proxy,
                warnings,
                "restls plugin-opts host/password/version-hint are required",
            )


def _validate_ssr(proxy: dict[str, Any], warnings: list[str]) -> None:
    cipher = str(proxy.get("cipher", "")).lower()
    if cipher not in SSR_STREAM_CIPHERS:
        _warn(
            proxy, warnings, f"{cipher} is not none or a supported stream cipher in ssr"
        )
    if str(proxy.get("obfs", "")).lower() not in SSR_OBFS:
        _warn(proxy, warnings, f"initialize obfs error: {proxy.get('obfs')}")
    if str(proxy.get("protocol", "")).lower() not in SSR_PROTOCOLS:
        _warn(proxy, warnings, f"initialize protocol error: {proxy.get('protocol')}")


def _validate_vmess(proxy: dict[str, Any], warnings: list[str]) -> None:
    if str(proxy.get("cipher", "")).lower() not in VMESS_CIPHERS:
        _warn(proxy, warnings, f"cipher {proxy.get('cipher')!r} not supported")
    _validate_uuid(proxy, warnings)


def _validate_vless(proxy: dict[str, Any], warnings: list[str]) -> None:
    _validate_uuid(proxy, warnings)
    flow = str(proxy.get("flow", ""))
    if len(flow) >= 16 and flow[:16] != "xtls-rprx-vision":
        _warn(proxy, warnings, f"unsupported xtls flow type: {flow[:16]}")
    if not _valid_vless_encryption(str(proxy.get("encryption", ""))):
        _warn(
            proxy,
            warnings,
            f"invalid vless encryption value: {proxy.get('encryption')}",
        )
    xhttp = proxy.get("xhttp-opts")
    if (
        isinstance(xhttp, dict)
        and xhttp.get("mode") == "stream-one"
        and xhttp.get("download-settings") is not None
    ):
        _warn(
            proxy,
            warnings,
            'xhttp mode "stream-one" cannot be used with download-settings',
        )


def _validate_snell(proxy: dict[str, Any], warnings: list[str]) -> None:
    version = int(proxy.get("version") or 0)
    if version == 0:
        version = 4
    if version == 5:
        version = 4
    if version in {1, 2} and proxy.get("udp"):
        _warn(proxy, warnings, f"snell version {version} not support UDP")
    elif version not in {1, 2, 3, 4}:
        _warn(proxy, warnings, f"snell version error: {version}")
    opts = proxy.get("obfs-opts")
    if isinstance(opts, dict) and str(opts.get("mode", "")) not in {"", "tls", "http"}:
        _warn(proxy, warnings, f"snell obfs mode error: {opts.get('mode')}")


def _validate_trojan(proxy: dict[str, Any], warnings: list[str]) -> None:
    opts = proxy.get("ss-opts")
    if not isinstance(opts, dict) or not opts.get("enabled"):
        return
    if opts.get("password", "") == "":
        _warn(proxy, warnings, "empty password")
    method = str(opts.get("method") or "AES-128-GCM").lower()
    if method not in TROJAN_SS_CIPHERS:
        _warn(proxy, warnings, f"ss-opts.method {opts.get('method')!r} not supported")


def _validate_hysteria(proxy: dict[str, Any], warnings: list[str]) -> None:
    if not _valid_speed(str(proxy.get("up", ""))):
        _warn(proxy, warnings, f"invalid upload speed: {proxy.get('up')}")
    if not _valid_speed(str(proxy.get("down", ""))):
        _warn(proxy, warnings, f"invalid download speed: {proxy.get('down')}")
    if proxy.get("auth") and _decode_std_base64(str(proxy["auth"])) is None:
        _warn(proxy, warnings, "auth must be standard base64")


def _validate_hysteria2(proxy: dict[str, Any], warnings: list[str]) -> None:
    obfs = str(proxy.get("obfs", ""))
    if obfs:
        if not proxy.get("obfs-password"):
            _warn(proxy, warnings, "missing obfs password")
        if obfs not in {"salamander", "gecko"}:
            _warn(proxy, warnings, f"unknown obfs type: {obfs}")
    if proxy.get("ports"):
        if not _valid_unsigned_ranges(str(proxy["ports"]), max_value=65535):
            _warn(proxy, warnings, "ports must be unsigned port ranges")
        if proxy.get("hop-interval") and not _valid_unsigned_range(
            str(proxy["hop-interval"])
        ):
            _warn(proxy, warnings, "hop-interval must be unsigned range")
    elif not _valid_port(proxy.get("port")):
        _warn(proxy, warnings, "invalid port")


def _validate_wireguard(proxy: dict[str, Any], warnings: list[str]) -> None:
    _validate_prefixes(proxy, warnings)
    _validate_std_base64_field(proxy, warnings, "private-key")
    if proxy.get("reserved") and len(proxy["reserved"]) != 3:
        _warn(proxy, warnings, "invalid reserved value, required 3 bytes")
    peers = proxy.get("peers")
    if isinstance(peers, list) and peers:
        for index, peer in enumerate(peers):
            if not isinstance(peer, dict):
                continue
            peer_map = cast(dict[str, Any], peer)
            if _decode_std_base64(str(peer_map.get("public-key", ""))) is None:
                _warn(proxy, warnings, f"decode public key for peer {index}")
            if (
                peer_map.get("pre-shared-key")
                and _decode_std_base64(str(peer_map["pre-shared-key"])) is None
            ):
                _warn(proxy, warnings, f"decode pre shared key for peer {index}")
            if not peer_map.get("allowed-ips"):
                _warn(proxy, warnings, f"missing allowed-ips for peer {index}")
            reserved = peer_map.get("reserved")
            if reserved and hasattr(reserved, "__len__") and len(reserved) != 3:
                _warn(
                    proxy,
                    warnings,
                    f"invalid reserved value for peer {index}, required 3 bytes",
                )
    else:
        _validate_std_base64_field(proxy, warnings, "public-key")
        if proxy.get("pre-shared-key"):
            _validate_std_base64_field(proxy, warnings, "pre-shared-key")


def _validate_ssh(proxy: dict[str, Any], warnings: list[str]) -> None:
    private_key = str(proxy.get("private-key", ""))
    if "PRIVATE KEY" in private_key and not _valid_pem(private_key):
        _warn(proxy, warnings, "private-key must be valid PEM")
    for host_key in proxy.get("host-key", []) or []:
        if not _valid_authorized_key(str(host_key)):
            _warn(proxy, warnings, "host-key must be authorized-key text")


def _validate_mieru(proxy: dict[str, Any], warnings: list[str]) -> None:
    port = int(proxy.get("port") or 0)
    port_range = str(proxy.get("port-range") or "")
    if port == 0 and port_range == "":
        _warn(proxy, warnings, "either port or port-range must be set")
    if port != 0 and port_range != "":
        _warn(proxy, warnings, "port and port-range cannot be set at the same time")
    if port != 0 and not _valid_port(port):
        _warn(proxy, warnings, "port must be between 1 and 65535")
    if port_range:
        parsed = _parse_mieru_port_range(port_range)
        if parsed is None:
            _warn(proxy, warnings, "invalid port-range format")
        else:
            begin, end = parsed
            if begin < 1 or begin > 65535:
                _warn(proxy, warnings, "begin port must be between 1 and 65535")
            if end < 1 or end > 65535:
                _warn(proxy, warnings, "end port must be between 1 and 65535")
            if begin > end:
                _warn(
                    proxy, warnings, "begin port must be less than or equal to end port"
                )
    if proxy.get("transport") not in {"TCP", "UDP"}:
        _warn(proxy, warnings, "transport must be TCP or UDP")
    if str(proxy.get("multiplexing", "")) not in MIERU_MULTIPLEXING:
        _warn(
            proxy, warnings, f"invalid multiplexing level: {proxy.get('multiplexing')}"
        )
    if str(proxy.get("handshake-mode", "")) not in MIERU_HANDSHAKE_MODES:
        _warn(proxy, warnings, f"invalid handshake mode: {proxy.get('handshake-mode')}")


def _validate_sudoku(proxy: dict[str, Any], warnings: list[str]) -> None:
    if not proxy.get("server"):
        _warn(proxy, warnings, "server is required")
    if not _valid_port(proxy.get("port")):
        _warn(proxy, warnings, f"invalid port: {proxy.get('port')}")
    if not proxy.get("key"):
        _warn(proxy, warnings, "key is required")
    aead = str(proxy.get("aead-method") or "chacha20-poly1305")
    if aead not in {"aes-128-gcm", "chacha20-poly1305", "none"}:
        _warn(proxy, warnings, f"invalid aead-method: {aead}")
    padding_min = int(proxy.get("padding-min", 10))
    padding_max = int(proxy.get("padding-max", 30))
    if (
        "padding-min" not in proxy
        and "padding-max" in proxy
        and padding_max < padding_min
    ):
        padding_min = padding_max
    if (
        "padding-max" not in proxy
        and "padding-min" in proxy
        and padding_max < padding_min
    ):
        padding_max = padding_min
    if padding_min < 0 or padding_min > 100:
        _warn(
            proxy, warnings, f"padding-min must be between 0 and 100, got {padding_min}"
        )
    if padding_max < 0 or padding_max > 100:
        _warn(
            proxy, warnings, f"padding-max must be between 0 and 100, got {padding_max}"
        )
    if padding_max < padding_min:
        _warn(
            proxy,
            warnings,
            f"padding-max ({padding_max}) must be >= padding-min ({padding_min})",
        )
    if str(proxy.get("table-type", "")) not in SUDOKU_TABLE_TYPES:
        _warn(
            proxy,
            warnings,
            "table-type must be prefer_ascii, prefer_entropy, up_ascii_down_entropy, or up_entropy_down_ascii",
        )
    mode = str(proxy.get("http-mask-mode", ""))
    multiplex = str(proxy.get("http-mask-multiplex", ""))
    path_root = str(proxy.get("path-root", ""))
    hm = proxy.get("httpmask")
    if isinstance(hm, dict):
        mode = str(hm.get("mode") or mode)
        multiplex = str(hm.get("multiplex") or multiplex)
        path_root = str(hm.get("path-root") or path_root)
    if mode.strip().lower() not in {"", "legacy", "stream", "poll", "auto", "ws"}:
        _warn(proxy, warnings, f"invalid http-mask-mode: {mode}")
    path_root = path_root.strip().strip("/")
    if path_root and ("/" in path_root or not _PATH_ROOT_RE.fullmatch(path_root)):
        _warn(proxy, warnings, "invalid http-mask-path-root")
    if multiplex.strip().lower() not in {"", "off", "auto", "on"}:
        _warn(proxy, warnings, f"invalid http-mask-multiplex: {multiplex}")
    for field in ("custom-table",):
        if proxy.get(field) and not _valid_sudoku_table(str(proxy[field])):
            _warn(proxy, warnings, "custom table must contain exactly 2 x, 2 p, 4 v")
    for table in proxy.get("custom-tables", []) or []:
        if not _valid_sudoku_table(str(table)):
            _warn(proxy, warnings, "custom table must contain exactly 2 x, 2 p, 4 v")


def _validate_masque(proxy: dict[str, Any], warnings: list[str]) -> None:
    _validate_prefixes(proxy, warnings)
    if _decode_std_base64(str(proxy.get("private-key", ""))) is None:
        _warn(proxy, warnings, "failed to decode private key")
    if _decode_std_base64(str(proxy.get("public-key", ""))) is None:
        _warn(proxy, warnings, "failed to decode public key")


def _validate_openvpn(proxy: dict[str, Any], warnings: list[str]) -> None:
    proto = _normalize_openvpn_proto(str(proxy.get("proto", "")))
    if proto not in {"udp", "tcp"}:
        _warn(proxy, warnings, f"unsupported openvpn proto {proto!r}")
    dev = str(proxy.get("dev") or "tun").strip().lower()
    if dev != "tun":
        _warn(proxy, warnings, f"unsupported openvpn dev {dev!r}")
    cipher = str(proxy.get("cipher") or "").strip().upper()
    if cipher not in OPENVPN_CIPHERS:
        _warn(proxy, warnings, f"unsupported openvpn cipher {cipher!r}")
    auth = str(proxy.get("auth") or "").strip().upper()
    if auth not in OPENVPN_AUTHS:
        _warn(proxy, warnings, f"unsupported openvpn auth {auth!r}")
    ca = str(proxy.get("ca", ""))
    if not _valid_pem(ca):
        _warn(proxy, warnings, "inline <ca> block is not PEM")
    cert = str(proxy.get("cert") or "")
    key = str(proxy.get("key") or "")
    if cert.strip() or key.strip():
        if not cert.strip() or not key.strip():
            _warn(
                proxy,
                warnings,
                "openvpn cert and key must both be set when using client certificate auth",
            )
        elif not _valid_pem(cert) or not _valid_pem(key):
            _warn(proxy, warnings, "inline <cert>/<key> block is not PEM")
    elif not str(proxy.get("username") or "").strip():
        _warn(proxy, warnings, "openvpn requires either cert+key or username")
    if int(proxy.get("ping") or 0) < 0:
        _warn(proxy, warnings, "openvpn ping interval must be positive")
    if int(proxy.get("ping-restart") or 0) < 0:
        _warn(proxy, warnings, "openvpn ping restart must be positive")


def _validate_uuid(proxy: dict[str, Any], warnings: list[str]) -> None:
    try:
        uuid.UUID(str(proxy.get("uuid", "")))
    except ValueError:
        _warn(proxy, warnings, "uuid must be valid UUID")


def _validate_uot_version(
    proxy: dict[str, Any], warnings: list[str], field: str
) -> None:
    version = int(proxy.get(field) or 0)
    if version not in {0, 1, 2}:
        _warn(proxy, warnings, f"unknown {field}: {version}")


def _valid_port(value: Any) -> bool:
    return isinstance(value, int) and 1 <= value <= 65535


def _valid_speed(value: str) -> bool:
    if value == "":
        return False
    if value.isdecimal():
        return int(value) > 0
    match = _RATE_RE.fullmatch(value)
    return bool(match and int(match.group(1)) > 0)


def _valid_vless_encryption(value: str) -> bool:
    if value in {"", "none"}:
        return True
    parts = value.split(".")
    if len(parts) < 4 or parts[0] != "mlkem768x25519plus":
        return False
    if parts[1] not in {"native", "xorpub", "random"}:
        return False
    if parts[2] not in {"1rtt", "0rtt"}:
        return False
    found_key = False
    for part in parts[3:]:
        if len(part) < 20:
            continue
        decoded = _decode_raw_url_base64(part)
        if decoded is None or len(decoded) not in {32, 1216}:
            return False
        found_key = True
    return found_key


def _decode_std_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _decode_raw_url_base64(value: str) -> bytes | None:
    if "=" in value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None


def _validate_std_base64_field(
    proxy: dict[str, Any], warnings: list[str], field: str
) -> None:
    if _decode_std_base64(str(proxy.get(field, ""))) is None:
        _warn(proxy, warnings, f"decode {field}")


def _validate_prefixes(proxy: dict[str, Any], warnings: list[str]) -> None:
    found = False
    for field, suffix in (("ip", "/32"), ("ipv6", "/128")):
        value = str(proxy.get(field) or "")
        if not value:
            continue
        found = True
        if "/" not in value:
            value += suffix
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            _warn(proxy, warnings, f"{field} address parse error")
    if not found:
        _warn(proxy, warnings, "missing local address")


def _valid_unsigned_range(value: str) -> bool:
    return _parse_unsigned_range(value) is not None


def _valid_unsigned_ranges(value: str, *, max_value: int) -> bool:
    text = value.strip()
    if text in {"", "*"}:
        return True
    parts = text.replace(",", "/").split("/")
    if len(parts) > 28:
        return False
    for part in parts:
        if part == "":
            continue
        parsed = _parse_unsigned_range(part)
        if parsed is None:
            return False
        start, end = parsed
        if start > max_value or end > max_value:
            return False
    return True


def _parse_unsigned_range(value: str) -> tuple[int, int] | None:
    parts = [part.strip().strip("[] ") for part in value.strip().split("-")]
    if len(parts) not in {1, 2} or any(not part.isdecimal() for part in parts):
        return None
    start = int(parts[0])
    end = int(parts[-1])
    return (min(start, end), max(start, end))


def _parse_mieru_port_range(value: str) -> tuple[int, int] | None:
    parts = value.split("-")
    if len(parts) != 2 or any(not part.strip().isdecimal() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _valid_pem(value: str) -> bool:
    match = _PEM_RE.search(value)
    if not match:
        return False
    body = "".join(match.group(1).split())
    return _decode_std_base64(body) is not None


def _valid_authorized_key(value: str) -> bool:
    parts = value.split()
    return (
        len(parts) >= 2
        and parts[0].startswith("ssh-")
        and _decode_std_base64(parts[1]) is not None
    )


def _valid_sudoku_table(value: str) -> bool:
    cleaned = "".join(ch for ch in value.lower() if not ch.isspace())
    return (
        len(cleaned) == 8
        and cleaned.count("x") == 2
        and cleaned.count("p") == 2
        and cleaned.count("v") == 4
        and set(cleaned) <= {"x", "p", "v"}
    )


def _normalize_openvpn_proto(value: str) -> str:
    text = value.strip().lower()
    if text in {"", "udp", "udp4"}:
        return "udp"
    if text in {"tcp", "tcp-client", "tcp4", "tcp4-client"}:
        return "tcp"
    return text
