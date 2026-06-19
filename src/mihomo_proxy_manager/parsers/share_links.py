"""Share-link 解析器，支持 vmess://、ss://、vless://、trojan://、hysteria2:// 格式。

Share-link parser supporting vmess://, ss://, vless://, trojan://, and hysteria2:// formats.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, unquote, urlparse

from mihomo_proxy_manager.mihomo_schema import normalize_proxy
from mihomo_proxy_manager.models import ProxyRecord
from mihomo_proxy_manager.security import redact_secret


def _b64decode(value: str) -> bytes:
    """Base64 解码，同时支持 URL-safe 和标准 Base64 字母表。

    Base64 decode supporting both URL-safe and standard Base64 alphabets.

    Args:
        value: Base64 编码的字符串 / Base64-encoded string.

    Returns:
        解码后的字节数据 / Decoded bytes.
    """
    padding = "=" * (-len(value) % 4)
    padded = (value + padding).encode()
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception:
        # Some providers emit standard Base64 (+ and /) instead of the URL-safe
        # variant (- and _). Fall back to the standard alphabet.
        return base64.b64decode(padded)


def _name(fragment: str, fallback: str) -> str:
    """从 URL fragment 中提取代理名称，如无则使用备用名称。

    Extract proxy name from URL fragment, falling back to default.

    Args:
        fragment: URL fragment 部分 / URL fragment part.
        fallback: 备用名称 / Fallback name.

    Returns:
        代理名称 / Proxy name.
    """
    return unquote(fragment) if fragment else fallback


def _query(parsed) -> dict[str, str]:
    """解析 URL 查询参数为字典。

    Parse URL query parameters into a dictionary.

    Args:
        parsed: 解析后的 URL 对象 / Parsed URL object.

    Returns:
        查询参数字典 / Query parameters dict.
    """
    return {key: values[-1] for key, values in parse_qs(parsed.query).items()}


def _add_ss_plugin(proxy: dict[str, object], plugin_value: str | None) -> None:
    """向 Shadowsocks 代理字典添加插件配置。

    Add plugin configuration to a Shadowsocks proxy dict.

    Args:
        proxy: 代理字典 / Proxy dict.
        plugin_value: 插件配置字符串 / Plugin configuration string.
    """
    if not plugin_value:
        return
    parts = [item for item in plugin_value.split(";") if item]
    if not parts:
        return
    proxy["plugin"] = parts[0]
    opts: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            opts[key] = value
    if opts:
        proxy["plugin-opts"] = opts


def _apply_transport_options(proxy: dict[str, object], query: dict[str, str]) -> None:
    """应用传输层选项（network、tls、reality、ws、grpc 等）。

    Apply transport options (network, tls, reality, ws, grpc, etc.).

    Args:
        proxy: 代理字典 / Proxy dict.
        query: 查询参数字典 / Query parameters dict.
    """
    network = query.get("type") or query.get("network")
    if network:
        proxy["network"] = network
    if "sni" in query:
        if proxy.get("type") in {"vmess", "vless"}:
            proxy["servername"] = query["sni"]
        else:
            proxy["sni"] = query["sni"]
    if "alpn" in query:
        proxy["alpn"] = query["alpn"].split(",")
    if "security" in query:
        if query["security"] in {"tls", "reality"}:
            proxy["tls"] = True
    if "flow" in query:
        proxy["flow"] = query["flow"]
    if "allowInsecure" in query or "insecure" in query:
        proxy["skip-cert-verify"] = query.get(
            "allowInsecure", query.get("insecure")
        ) in {"1", "true", "True"}

    public_key = query.get("publicKey") or query.get("pbk")
    short_id = query.get("shortId") or query.get("sid")
    if public_key or short_id:
        opts: dict[str, str] = {}
        if public_key:
            opts["public-key"] = public_key
        if short_id:
            opts["short-id"] = short_id
        proxy["reality-opts"] = opts

    fingerprint = (
        query.get("client-fingerprint") or query.get("fingerprint") or query.get("fp")
    )
    if fingerprint:
        proxy["client-fingerprint"] = fingerprint

    if proxy.get("network") == "ws":
        ws_opts: dict[str, object] = {}
        if "path" in query:
            ws_opts["path"] = query["path"]
        if "host" in query:
            ws_opts["headers"] = {"Host": query["host"]}
        if ws_opts:
            proxy["ws-opts"] = ws_opts
    if proxy.get("network") == "grpc" and "serviceName" in query:
        proxy["grpc-opts"] = {"grpc-service-name": query["serviceName"]}


def _parse_vmess(link: str) -> dict[str, object]:
    """解析 vmess:// 链接。

    Parse a vmess:// link.

    Args:
        link: vmess:// 链接 / vmess:// link.

    Returns:
        代理字典 / Proxy dict.
    """
    raw = link.removeprefix("vmess://")
    data = json.loads(_b64decode(raw))
    proxy = {
        "name": data.get("ps") or data.get("add") or "vmess",
        "type": "vmess",
        "server": data.get("add"),
        "port": int(data.get("port", 0)),
        "uuid": data.get("id"),
        "alterId": int(data.get("aid", 0)),
        "cipher": data.get("scy") or data.get("cipher") or "auto",
    }
    if data.get("tls"):
        proxy["tls"] = data.get("tls") == "tls"
    if data.get("net"):
        proxy["network"] = data.get("net")
    if data.get("host") or data.get("path"):
        proxy["ws-opts"] = {
            "path": data.get("path", "/"),
            "headers": {"Host": data.get("host", "")},
        }
    return proxy


def _parse_ss(link: str) -> dict[str, object]:
    """解析 ss:// 链接。

    Parse an ss:// link.

    Args:
        link: ss:// 链接 / ss:// link.

    Returns:
        代理字典 / Proxy dict.
    """
    parsed = urlparse(link)
    query = _query(parsed)
    if parsed.hostname and parsed.username:
        userinfo = unquote(parsed.username)
        try:
            decoded = _b64decode(userinfo).decode()
        except Exception:
            decoded = userinfo
        cipher, password = decoded.split(":", 1)
        proxy: dict[str, object] = {
            "name": _name(parsed.fragment, parsed.hostname),
            "type": "ss",
            "server": parsed.hostname,
            "port": parsed.port,
            "cipher": cipher,
            "password": password,
        }
        _add_ss_plugin(proxy, query.get("plugin"))
        return proxy
    raw = link.removeprefix("ss://").split("?", 1)[0].split("#", 1)[0]
    decoded = _b64decode(raw).decode()
    method_password, endpoint = decoded.rsplit("@", 1)
    cipher, password = method_password.split(":", 1)
    if endpoint.startswith("["):
        bracket_end = endpoint.rfind("]")
        if bracket_end == -1:
            raise ValueError("malformed IPv6 endpoint: missing closing bracket")
        server = endpoint[1:bracket_end]
        port_part = endpoint[bracket_end + 1 :]
        if not port_part.startswith(":"):
            raise ValueError("malformed IPv6 endpoint: port required after ']'")
        port = int(port_part[1:])
    else:
        server, port_str = endpoint.rsplit(":", 1)
        port = int(port_str)
    proxy: dict[str, object] = {
        "name": _name(parsed.fragment, server),
        "type": "ss",
        "server": server,
        "port": port,
        "cipher": cipher,
        "password": password,
    }
    _add_ss_plugin(proxy, query.get("plugin"))
    return proxy


def _parse_url_link(link: str) -> dict[str, object]:
    """解析通用 URL 格式的代理链接（ss/vless/trojan/hysteria2）。

    Parse a generic URL-format proxy link (ss/vless/trojan/hysteria2).

    Args:
        link: 代理链接 / Proxy link.

    Returns:
        代理字典 / Proxy dict.
    """
    if link.startswith("ss://"):
        return _parse_ss(link)
    parsed = urlparse(link)
    query = _query(parsed)
    scheme = parsed.scheme.lower()
    proxy_type = "hysteria2" if scheme in {"hysteria2", "hy2"} else scheme
    proxy: dict[str, object] = {
        "name": _name(parsed.fragment, parsed.hostname or proxy_type),
        "type": proxy_type,
        "server": parsed.hostname,
        "port": parsed.port,
    }
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")

    if scheme == "vless":
        proxy.update({"uuid": username, "encryption": query.get("encryption", "none")})
    elif scheme == "trojan":
        proxy.update({"password": username})
    elif scheme in {"hysteria2", "hy2"}:
        proxy.update({"password": username or password})

    _apply_transport_options(proxy, query)
    return proxy


def parse_share_links_text(
    text: str, *, source: str
) -> tuple[list[ProxyRecord], list[str]]:
    """解析 share-link 文本，返回代理记录和警告列表。

    Parse share-link text, returning proxy records and warnings.

    Args:
        text: share-link 文本内容 / Share-link text content.
        source: 订阅源名称 / Source name.

    Returns:
        (代理记录列表, 警告列表) 元组 / Tuple of (proxy records list, warnings list).
    """
    records: list[ProxyRecord] = []
    warnings: list[str] = []
    for line in (item.strip() for item in text.splitlines()):
        if not line:
            continue
        try:
            if line.startswith("vmess://"):
                proxy = _parse_vmess(line)
            elif line.startswith(
                ("ss://", "vless://", "trojan://", "hysteria2://", "hy2://")
            ):
                proxy = _parse_url_link(line)
            else:
                raise ValueError("unsupported share link")
            normalized, item_warnings = normalize_proxy(proxy)
            if item_warnings:
                warnings.extend(item_warnings)
                continue
            if normalized is not None:
                records.append(ProxyRecord(source=source, data=normalized))
        except Exception as exc:
            # Do not embed the raw link or exception text that may contain tokens/secrets.
            detail = redact_secret(str(exc))[:200]
            warnings.append(f"failed to parse share link: {detail}")
    return records, warnings
