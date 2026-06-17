"""Clash/Mihomo YAML 订阅解析器，包含必填字段验证。

Clash/Mihomo YAML subscription parser with required-field validation.
"""

from __future__ import annotations

from typing import Any

import yaml

from mihomo_proxy_manager.models import ProxyRecord

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ss": ("server", "port", "cipher", "password"),
    "vmess": ("server", "port", "uuid", "cipher"),
    "vless": ("server", "port", "uuid"),
    "trojan": ("server", "port", "password"),
    "hysteria2": ("server", "port", "password"),
    "hy2": ("server", "port", "password"),
    "http": ("server", "port"),
    "socks5": ("server", "port"),
}


def validate_required_fields(proxy: dict[str, Any]) -> list[str]:
    """验证代理字典是否包含所有必填字段。

    Validate that a proxy dict contains all required fields.

    Args:
        proxy: 代理字典 / Proxy dict.

    Returns:
        缺失字段的警告列表 / List of warnings for missing fields.
    """
    proxy_type = str(proxy.get("type", "")).lower()
    warnings: list[str] = []
    for field in REQUIRED_FIELDS.get(proxy_type, ("name", "type")):
        if field not in proxy or proxy[field] in (None, ""):
            warnings.append(f"proxy {proxy.get('name', '<unnamed>')!r} missing required field {field!r}")
    if "name" not in proxy or "type" not in proxy:
        warnings.append("proxy missing required field 'name' or 'type'")
    return warnings


def parse_yaml_subscription(body: bytes, *, source: str) -> tuple[list[ProxyRecord], list[str]]:
    """解析 YAML 格式的订阅内容。

    Parse a YAML-format subscription.

    Args:
        body: YAML 内容的原始字节 / Raw bytes of YAML content.
        source: 订阅源名称 / Source name.

    Returns:
        (代理记录列表, 警告列表) 元组 / Tuple of (proxy records list, warnings list).

    Raises:
        ValueError: 如果 YAML 结构无效 / If YAML structure is invalid.
    """
    loaded = yaml.safe_load(body.decode("utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError("YAML subscription must be a mapping")
    proxies = loaded.get("proxies")
    if not isinstance(proxies, list):
        raise ValueError("YAML subscription has no proxies list")

    records: list[ProxyRecord] = []
    warnings: list[str] = []
    for item in proxies:
        if not isinstance(item, dict):
            warnings.append("proxy entry is not a mapping")
            continue
        proxy = dict(item)
        item_warnings = validate_required_fields(proxy)
        warnings.extend(item_warnings)
        if not item_warnings:
            records.append(ProxyRecord(source=source, data=proxy))
    return records, warnings
