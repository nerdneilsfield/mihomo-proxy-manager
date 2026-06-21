"""TOML 配置加载、解析和验证。

TOML configuration loading, parsing, and validation.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from datetime import timedelta
from ipaddress import ip_network
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .security import SecurityError, assert_safe_url, has_path_entropy

from .models import (
    AccessLogConfig,
    AccessLogFileConfig,
    AccessLogHeadersConfig,
    AccessLogStatusConfig,
    AppConfig,
    CacheConfig,
    DEFAULT_TRUSTED_PROXY_NETWORKS,
    DnsConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    IPNetwork,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    PluginConfig,
    PluginRefConfig,
    RealIPHeader,
    RefreshConfig,
    RenameConfig,
    RouteAccessConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourceDnsConfig,
    SourcePluginConfig,
    ValidationReport,
)

DEFAULT_USER_AGENT = "mihomo/1.19.5"
USER_AGENT_PATTERN = re.compile(
    r"^(?:clash[.-]meta|mihomo)/\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9._-]+)?$"
)

DNS_FAILURES = {"keep", "drop", "fail"}
DNS_SCHEMES = {"udp", "tcp", "tls", "https"}
SUPPORTED_REAL_IP_HEADERS = {
    "cf-connecting-ip",
    "true-client-ip",
    "x-forwarded-for",
    "x-real-ip",
}


def parse_duration(value: str) -> timedelta:
    """解析持续时间字符串，返回 timedelta。

    Parse a duration string and return a timedelta.

    Args:
        value: 持续时间字符串，如 ``30s``、``5m``、``2h``、``7d``。
               Duration string, e.g. ``30s``, ``5m``, ``2h``, ``7d``.

    Returns:
        解析后的 timedelta 对象。
        Parsed timedelta object.

    Raises:
        ValueError: 如果格式无效。If the format is invalid.
    """
    match = re.fullmatch(r"(\d+)(s|m|h|d)", value.strip())
    if not match:
        raise ValueError(f"invalid duration {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def parse_size(value: str) -> int:
    """解析大小字符串，返回字节数。

    Parse a size string and return the number of bytes.

    Args:
        value: 大小字符串，如 ``10 MB``、``512KB``、``128B``。
               Size string, e.g. ``10 MB``, ``512KB``, ``128B``.

    Returns:
        对应的字节数。The corresponding number of bytes.

    Raises:
        ValueError: 如果格式无效。If the format is invalid.
    """
    match = re.fullmatch(r"(\d+)\s*(B|KB|MB)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid size {value!r}")
    amount = int(match.group(1))
    unit = match.group(2).upper()
    return amount * {"B": 1, "KB": 1024, "MB": 1024 * 1024}[unit]


def parse_file_mode(value: str | int) -> int:
    """解析文件权限模式值。

    Parse a file mode value.

    Integers are accepted as-is, so use an octal literal such as ``0o600`` in
    TOML. Strings are parsed as octal when they start with ``0`` or ``0o``;
    otherwise they are parsed as decimal integers.

    Args:
        value: 文件权限模式值（整数或字符串）。
               File mode value (integer or string).

    Returns:
        解析后的整数权限值。Parsed integer permission value.

    Raises:
        ValueError: 如果格式无效。If the format is invalid.
    """
    if isinstance(value, int):
        return value
    s = str(value).strip()
    try:
        if s.startswith(("0o", "0O")) or (
            len(s) > 1 and s.startswith("0") and s.isdigit()
        ):
            return int(s, 8)
        return int(s)
    except ValueError as exc:
        raise ValueError(f"invalid file mode {value!r}") from exc


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    """安全地从字典中获取子表。

    Safely retrieve a sub-table from a dictionary.

    Args:
        data: 源字典。Source dictionary.
        key: 键名。Key name.

    Returns:
        子表字典，如果不存在或不是字典则返回空字典。
        The sub-table dictionary, or an empty dict if missing or not a dict.
    """
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _positive_duration(value: str, *, key: str) -> timedelta:
    duration = parse_duration(value)
    if duration.total_seconds() <= 0:
        raise ValueError(f"{key} must be positive")
    return duration


def _positive_int(value: object, *, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be positive")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdecimal():
        parsed = int(value)
    else:
        raise ValueError(f"{key} must be positive")
    if parsed <= 0:
        raise ValueError(f"{key} must be positive")
    return parsed


def _string_tuple(
    value: object, default: tuple[str, ...], *, key: str
) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{key} must contain string values")
        result.append(item)
    return tuple(result)


def _trusted_proxies(values: object) -> tuple[IPNetwork, ...]:
    raw_values = _string_tuple(
        values,
        DEFAULT_TRUSTED_PROXY_NETWORKS,
        key="access_log.trusted_proxies",
    )
    networks: list[IPNetwork] = []
    try:
        for value in raw_values:
            networks.append(ip_network(value, strict=False))
    except ValueError as exc:
        raise ValueError("access_log trusted proxy is invalid") from exc
    return tuple(networks)


def _real_ip_headers(values: object) -> tuple[RealIPHeader, ...]:
    raw_values = _string_tuple(
        values,
        ("cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"),
        key="access_log.real_ip_headers",
    )
    headers = tuple(value.lower() for value in raw_values)
    unsupported = sorted(set(headers) - SUPPORTED_REAL_IP_HEADERS)
    if unsupported:
        raise ValueError(
            "access_log.real_ip_headers value is unsupported: "
            + ", ".join(repr(item) for item in unsupported)
        )
    return cast(tuple[RealIPHeader, ...], headers)


def _reject_unknown_keys(
    data: dict[str, object], allowed: set[str], prefix: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            "\n".join(f"{prefix} key is unsupported: {key!r}" for key in unknown)
        )


def _access_log(raw: dict[str, Any]) -> AccessLogConfig:
    allowed = {
        "enabled",
        "db_path",
        "retention",
        "trusted_proxies",
        "real_ip_headers",
        "file",
        "headers",
        "status",
    }
    file_allowed = {"enabled", "path", "rotation", "retention", "compression"}
    headers_allowed = {"max_value_length", "stats_allowlist", "stats_max_rows"}
    status_allowed = {
        "enabled",
        "mask_ips",
        "include_recent",
        "recent_limit",
        "top_limit",
    }

    _reject_unknown_keys(raw, allowed, "access_log")
    file_raw = _table(raw, "file")
    headers_raw = _table(raw, "headers")
    status_raw = _table(raw, "status")
    _reject_unknown_keys(file_raw, file_allowed, "access_log.file")
    _reject_unknown_keys(headers_raw, headers_allowed, "access_log.headers")
    _reject_unknown_keys(status_raw, status_allowed, "access_log.status")

    return AccessLogConfig(
        enabled=bool(raw.get("enabled", True)),
        db_path=Path(raw.get("db_path", "data/access/access.sqlite3")),
        retention=_positive_duration(
            str(raw.get("retention", "30d")), key="access_log.retention"
        ),
        trusted_proxies=_trusted_proxies(raw.get("trusted_proxies")),
        real_ip_headers=_real_ip_headers(raw.get("real_ip_headers")),
        file=AccessLogFileConfig(
            enabled=bool(file_raw.get("enabled", True)),
            path=Path(file_raw.get("path", "logs/access.log")),
            rotation=str(file_raw.get("rotation", "10 MB")),
            retention=str(file_raw.get("retention", "30 days")),
            compression=str(file_raw.get("compression", "gz")),
        ),
        headers=AccessLogHeadersConfig(
            max_value_length=_positive_int(
                headers_raw.get("max_value_length", 512),
                key="access_log.headers.max_value_length",
            ),
            stats_allowlist=tuple(
                item.lower()
                for item in _string_tuple(
                    headers_raw.get("stats_allowlist"),
                    ("user-agent", "host", "cf-ipcountry", "cf-ray"),
                    key="access_log.headers.stats_allowlist",
                )
            ),
            stats_max_rows=_positive_int(
                headers_raw.get("stats_max_rows", 5000),
                key="access_log.headers.stats_max_rows",
            ),
        ),
        status=AccessLogStatusConfig(
            enabled=bool(status_raw.get("enabled", True)),
            mask_ips=bool(status_raw.get("mask_ips", True)),
            include_recent=bool(status_raw.get("include_recent", False)),
            recent_limit=_positive_int(
                status_raw.get("recent_limit", 20),
                key="access_log.status.recent_limit",
            ),
            top_limit=_positive_int(
                status_raw.get("top_limit", 20),
                key="access_log.status.top_limit",
            ),
        ),
    )


def _filter(data: dict[str, Any]) -> FilterConfig:
    """从原始字典构建 FilterConfig。

    Build a FilterConfig from a raw dictionary.

    Args:
        data: 包含过滤选项的字典。
               Dictionary containing filter options.

    Returns:
        构建的 FilterConfig 对象。Constructed FilterConfig object.
    """
    return FilterConfig(
        include=data.get("include"),
        exclude=data.get("exclude"),
        include_types=tuple(data.get("include_types", ())),
        exclude_types=tuple(data.get("exclude_types", ())),
    )


def _rename(data: dict[str, Any]) -> RenameConfig:
    """从原始字典构建 RenameConfig。

    Build a RenameConfig from a raw dictionary.

    Args:
        data: 包含重命名选项的字典。
               Dictionary containing rename options.

    Returns:
        构建的 RenameConfig 对象。Constructed RenameConfig object.
    """
    return RenameConfig(prefix=data.get("prefix", ""), suffix=data.get("suffix", ""))


def _fetch(
    data: dict[str, Any], http: HttpConfig, security: SecurityConfig
) -> FetchConfig:
    """从原始字典构建 FetchConfig。

    Build a FetchConfig from a raw dictionary.

    Args:
        data: 包含抓取选项的字典。
               Dictionary containing fetch options.
        http: 全局 HTTP 配置，用于提供默认值。
               Global HTTP config, used to supply defaults.
        security: 全局安全配置，用于提供默认值。
                  Global security config, used to supply defaults.

    Returns:
        构建的 FetchConfig 对象。Constructed FetchConfig object.
    """
    headers = _table(data, "headers")
    return FetchConfig(
        timeout=parse_duration(
            data.get("timeout", f"{int(http.timeout.total_seconds())}s")
        ),
        user_agent=data.get("user_agent", http.user_agent),
        headers={str(k): str(v) for k, v in headers.items()},
        allow_private_network=bool(
            data.get("allow_private_network", security.allow_private_network_urls)
        ),
    )


def _validate_user_agent(value: str, *, label: str) -> str | None:
    """验证 User-Agent 是否是允许的 Mihomo/Clash Meta 客户端格式。

    Validate that a User-Agent uses the allowed Mihomo/Clash Meta client format.
    """
    if USER_AGENT_PATTERN.fullmatch(value.strip()):
        return None
    return (
        f"{label} user_agent must use 'clash-meta/<version>', "
        f"'clash.meta/<version>', or 'mihomo/<version>'; got {value!r}"
    )


def _header_user_agent(headers: dict[str, str]) -> str | None:
    """从 header 字典中查找 User-Agent，大小写不敏感。

    Find a User-Agent header case-insensitively.
    """
    for key, value in headers.items():
        if key.lower() == "user-agent":
            return value
    return None


def _refresh(data: dict[str, Any]) -> RefreshConfig:
    """从原始字典构建 RefreshConfig。

    Build a RefreshConfig from a raw dictionary.

    Args:
        data: 包含刷新选项的字典。
               Dictionary containing refresh options.

    Returns:
        构建的 RefreshConfig 对象。Constructed RefreshConfig object.
    """
    interval = data.get("interval")
    cron = data.get("cron", ())
    if isinstance(cron, str):
        cron = (cron,)
    return RefreshConfig(
        interval=parse_duration(interval) if interval else None,
        cron=tuple(cron),
    )


def _source_plugins(data: dict[str, Any]) -> SourcePluginConfig:
    """从原始字典构建 SourcePluginConfig。

    Build a SourcePluginConfig from a raw dictionary.

    Args:
        data: 包含源插件选项的字典。
               Dictionary containing source plugin options.

    Returns:
        构建的 SourcePluginConfig 对象。Constructed SourcePluginConfig object.
    """
    before_fetch_table = _table(data, "before_fetch")
    before_fetch = {}
    for name, values in before_fetch_table.items():
        # Validation of on_failure values is deferred to LoadedConfig.validate()
        # so that all enum errors can be collected in a single report.
        on_failure = values.get("on_failure", "abort")
        before_fetch[name] = PluginRefConfig(on_failure=on_failure)
    return SourcePluginConfig(before_fetch=before_fetch)


def _as_tuple(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Coerce a raw TOML value into a tuple of strings.

    Args:
        value: 原始值（None、字符串或可迭代对象）。
               Raw value (None, string, or iterable).
        default: 当 value 为 None 时使用的默认元组。
                 Default tuple used when value is None.

    Returns:
        字符串元组 / Tuple of strings.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _dns(data: dict[str, Any]) -> DnsConfig:
    """从原始字典构建全局 DnsConfig。

    Build a global DnsConfig from a raw dictionary.
    """
    return DnsConfig(
        servers=_as_tuple(data.get("servers"), default=("udp://1.1.1.1:53",)),
        timeout=parse_duration(data.get("timeout", "5s")),
        failure=data.get("failure", "keep"),
        enable_ipv6=bool(data.get("enable_ipv6", False)),
    )


def _source_dns(data: dict[str, Any], dns: DnsConfig) -> SourceDnsConfig:
    """从原始字典构建 SourceDnsConfig，缺省值继承全局 DNS 配置。

    Build a SourceDnsConfig from a raw dictionary, inheriting defaults from
    the global DNS config.
    """
    return SourceDnsConfig(
        enabled=bool(data.get("enabled", False)),
        servers=_as_tuple(data.get("servers"), default=dns.servers),
        timeout=parse_duration(
            data.get("timeout", f"{int(dns.timeout.total_seconds())}s")
        ),
        failure=data.get("failure", dns.failure),
        enable_ipv6=bool(data.get("enable_ipv6", dns.enable_ipv6)),
    )


def _route_access(data: dict[str, Any]) -> RouteAccessConfig:
    """从原始字典构建 RouteAccessConfig。

    Build a RouteAccessConfig from a raw dictionary.
    """
    return RouteAccessConfig(user_agent=_as_tuple(data.get("user_agent")))


def _validate_dns_servers(
    servers: tuple[str, ...],
    *,
    label: str,
    allow_private_network: bool,
) -> list[str]:
    """验证 DNS 服务器列表的 scheme、host 和地址安全性。

    Validate DNS server list: scheme, host presence, and address safety.
    """
    errors: list[str] = []
    if not servers:
        return [f"{label} dns servers must not be empty"]
    for server in servers:
        parsed = urlparse(server)
        if parsed.scheme not in DNS_SCHEMES:
            errors.append(f"{label} unsupported DNS server scheme: {parsed.scheme!r}")
            continue
        if not parsed.hostname:
            errors.append(f"{label} DNS server host is required: {server!r}")
            continue
        if parsed.scheme in {"udp", "tcp", "tls"} and parsed.port is None:
            errors.append(f"{label} DNS server port is required: {server!r}")
        if parsed.scheme == "https":
            try:
                assert_safe_url(
                    server,
                    allow_private_network=allow_private_network,
                    resolve_dns=False,
                )
            except SecurityError as exc:
                errors.append(f"{label} DNS server is unsafe: {exc}")
        else:
            # Reuse static URL host checks by temporarily validating as https.
            static_url = f"https://{parsed.hostname}/"
            try:
                assert_safe_url(
                    static_url,
                    allow_private_network=allow_private_network,
                    resolve_dns=False,
                )
            except SecurityError as exc:
                errors.append(
                    f"{label} dns server resolves to non-public address: {exc}"
                )
    return errors


def _validate_public_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return "server public_base_url must use http:// or https://"
    if not parsed.hostname:
        return "server public_base_url host is required"
    if parsed.query or "?" in value:
        return "server public_base_url must not include query"
    if parsed.fragment or "#" in value:
        return "server public_base_url must not include fragment"
    if value.endswith("/"):
        return "server public_base_url must not end with '/'"
    return None


def _companion_paths(route: RouteConfig) -> tuple[str, ...]:
    if route.output.format == "surfboard":
        return (f"{route.path}-nodes",)
    if route.output.format == "quantumult-x" and route.output.import_link:
        return (f"{route.path}-import",)
    if route.output.format == "auto":
        paths = [f"{route.path}-nodes"]
        if route.output.import_link:
            paths.append(f"{route.path}-import")
        return tuple(paths)
    return ()


class LoadedConfig(AppConfig):
    """已加载并解析的完整应用配置。

    A fully loaded and parsed application configuration.

    该对象封装了所有配置节（server、cache、http、security、scheduler、parser、
    output、sources、routes、plugins），并提供 validate() 方法进行一致性校验。

    This object holds all configuration sections (server, cache, http, security,
    scheduler, parser, output, sources, routes, plugins) and provides a
    validate() method for consistency checking.
    """

    def validate(self, config_path: Path | None = None) -> ValidationReport:
        """验证配置的一致性，返回错误和警告列表。

        Validate configuration consistency and return a list of errors and warnings.

        检查项包括：路径冲突、正则表达式有效性、URL 安全性、时区有效性、
        缓存/日志目录可写性、文件权限等。

        Checks include: path collisions, regex validity, URL safety, timezone
        validity, cache/log directory writability, file permissions, etc.

        Args:
            config_path: 可选的配置文件路径，用于检查文件权限。
                         Optional config file path for permission checking.

        Returns:
            包含 errors 和 warnings 列表的 ValidationReport。
            A ValidationReport containing lists of errors and warnings.
        """
        errors: list[str] = []
        warnings: list[str] = []

        http_user_agent_error = _validate_user_agent(self.http.user_agent, label="http")
        if http_user_agent_error:
            errors.append(http_user_agent_error)

        if self.dns.failure not in DNS_FAILURES:
            errors.append("dns failure must be 'keep', 'drop', or 'fail'")
        errors.extend(
            _validate_dns_servers(
                self.dns.servers,
                label="global",
                allow_private_network=self.security.allow_private_network_urls,
            )
        )

        public_base_url_error = _validate_public_base_url(self.server.public_base_url)
        if public_base_url_error:
            errors.append(public_base_url_error)

        paths: dict[str, str] = {self.server.health_path: "health_path"}
        if self.server.status_path:
            if self.server.status_path in paths:
                errors.append("health_path and status_path collide")
            paths[self.server.status_path] = "status_path"
            status_api_path = f"{self.server.status_path.rstrip('/')}/api"
            if status_api_path in paths:
                errors.append(
                    f"path collision for status_api_path with {paths[status_api_path]}"
                )
            paths[status_api_path] = "status_api_path"
        if self.server.status_path and not has_path_entropy(
            self.server.status_path,
            min_bits=self.security.hidden_path_min_entropy_bits,
        ):
            errors.append(
                "status_path does not satisfy hidden path entropy requirement"
            )
        for route in self.routes.values():
            if not route.path.startswith("/"):
                errors.append(f"route {route.name!r} path must start with '/'")
            if not has_path_entropy(
                route.path, min_bits=self.security.hidden_path_min_entropy_bits
            ):
                errors.append(
                    f"route {route.name!r} path does not satisfy hidden path entropy requirement"
                )
            key = f"route {route.name!r}"
            if route.path in paths:
                errors.append(f"path collision for {key} with {paths[route.path]}")
            paths[route.path] = key
            for companion_path in _companion_paths(route):
                companion_key = f"{key} companion path {companion_path!r}"
                if companion_path in paths:
                    errors.append(
                        f"path collision for {companion_key} with {paths[companion_path]}"
                    )
                paths[companion_path] = companion_key
            for source in route.sources:
                if source not in self.sources:
                    errors.append(
                        f"route {route.name!r} references missing source {source!r}"
                    )
            for pattern_name, pattern in (
                ("include", route.filter.include),
                ("exclude", route.filter.exclude),
            ):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(
                            f"route {route.name!r} {pattern_name} regex is invalid: {exc}"
                        )
            if route.output.format not in {
                "provider",
                "surfboard",
                "quantumult-x",
                "xray-uri",
                "auto",
            }:
                errors.append(
                    f"route {route.name!r} output format is unsupported: {route.output.format!r}"
                )
                continue
            if route.output.auto_default not in {
                "provider",
                "surfboard",
                "quantumult-x",
                "xray-uri",
            }:
                errors.append(
                    f"route {route.name!r} auto_default is unsupported: "
                    f"{route.output.auto_default!r}"
                )
            if route.output.format == "auto":
                if route.output.mode != "default":
                    errors.append(
                        f"route {route.name!r} auto output mode must be default"
                    )
                if route.output.import_response not in {"redirect", "plain"}:
                    errors.append(
                        f"route {route.name!r} quantumult-x import_response is unsupported: "
                        f"{route.output.import_response!r}"
                    )
                if route.output.import_target not in {
                    "app-scheme",
                    "universal-link",
                }:
                    errors.append(
                        f"route {route.name!r} quantumult-x import_target is unsupported: "
                        f"{route.output.import_target!r}"
                    )
                if not route.output.test_url.startswith("http://"):
                    errors.append(
                        f"route {route.name!r} surfboard test_url must use http://"
                    )
                if not 1 <= route.output.test_interval <= 2_678_400:
                    errors.append(
                        f"route {route.name!r} surfboard test_interval must be between 1 and 2678400"
                    )
                if not 1 <= route.output.test_timeout <= 300:
                    errors.append(
                        f"route {route.name!r} surfboard test_timeout must be between 1 and 300"
                    )
                if not 0 <= route.output.test_tolerance <= 60_000:
                    errors.append(
                        f"route {route.name!r} surfboard test_tolerance must be between 0 and 60000"
                    )
                if route.output.encoding not in {"base64", "plain"}:
                    errors.append(
                        f"route {route.name!r} xray-uri encoding is unsupported: "
                        f"{route.output.encoding!r}"
                    )
                if not self.server.public_base_url:
                    errors.append(
                        f"route {route.name!r} public_base_url is required for auto output"
                    )
                continue
            if route.output.format == "provider":
                if route.output.mode != "default":
                    errors.append(
                        f"route {route.name!r} provider output mode must be default"
                    )
            else:
                if route.output.include_meta_comments:
                    errors.append(
                        f"route {route.name!r} include_meta_comments is only supported for provider output"
                    )
                if route.output.format == "surfboard":
                    if route.output.mode not in {"default", "full-profile"}:
                        errors.append(
                            f"route {route.name!r} surfboard mode is unsupported: {route.output.mode!r}"
                        )
                    if not route.output.test_url.startswith("http://"):
                        errors.append(
                            f"route {route.name!r} surfboard test_url must use http://"
                        )
                    if not 1 <= route.output.test_interval <= 2_678_400:
                        errors.append(
                            f"route {route.name!r} surfboard test_interval must be between 1 and 2678400"
                        )
                    if not 1 <= route.output.test_timeout <= 300:
                        errors.append(
                            f"route {route.name!r} surfboard test_timeout must be between 1 and 300"
                        )
                    if not 0 <= route.output.test_tolerance <= 60_000:
                        errors.append(
                            f"route {route.name!r} surfboard test_tolerance must be between 0 and 60000"
                        )
                    if not self.server.public_base_url:
                        errors.append(
                            f"route {route.name!r} public_base_url is required for surfboard output"
                        )
                elif route.output.format == "quantumult-x":
                    if route.output.mode not in {"default", "server-remote"}:
                        errors.append(
                            f"route {route.name!r} quantumult-x mode is unsupported: {route.output.mode!r}"
                        )
                    if route.output.import_response not in {"redirect", "plain"}:
                        errors.append(
                            f"route {route.name!r} quantumult-x import_response is unsupported: {route.output.import_response!r}"
                        )
                    if route.output.import_target not in {
                        "app-scheme",
                        "universal-link",
                    }:
                        errors.append(
                            f"route {route.name!r} quantumult-x import_target is unsupported: {route.output.import_target!r}"
                        )
                    if route.output.import_link and not self.server.public_base_url:
                        errors.append(
                            f"route {route.name!r} public_base_url is required for quantumult-x import_link"
                        )
                elif route.output.format == "xray-uri":
                    if route.output.mode != "default":
                        errors.append(
                            f"route {route.name!r} xray-uri output mode must be default"
                        )
                    if route.output.encoding not in {"base64", "plain"}:
                        errors.append(
                            f"route {route.name!r} xray-uri encoding is unsupported: {route.output.encoding!r}"
                        )

        for source in self.sources.values():
            fetch_user_agent_error = _validate_user_agent(
                source.fetch.user_agent, label=f"source {source.name!r} fetch"
            )
            if fetch_user_agent_error:
                errors.append(fetch_user_agent_error)
            fetch_header_user_agent = _header_user_agent(source.fetch.headers)
            if fetch_header_user_agent:
                fetch_header_error = _validate_user_agent(
                    fetch_header_user_agent,
                    label=f"source {source.name!r} fetch header User-Agent",
                )
                if fetch_header_error:
                    errors.append(fetch_header_error)
            if source.format not in {"auto", "yaml", "share-links"}:
                errors.append(
                    f"source {source.name!r} format is unsupported: {source.format!r}"
                )
            if source.parse_error not in {"skip", "fail"}:
                errors.append(
                    f"source {source.name!r} parse_error is unsupported: {source.parse_error!r}"
                )
            for pattern_name, pattern in (
                ("include", source.filter.include),
                ("exclude", source.filter.exclude),
            ):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(
                            f"source {source.name!r} {pattern_name} regex is invalid: {exc}"
                        )
            for plugin_name, ref in source.plugins.before_fetch.items():
                if plugin_name not in self.plugins:
                    errors.append(
                        f"source {source.name!r} references missing plugin {plugin_name!r}"
                    )
                if ref.on_failure not in {"abort", "continue"}:
                    errors.append(
                        f"source {source.name!r} plugin ref {plugin_name!r} "
                        f"has invalid on_failure {ref.on_failure!r}; must be 'abort' or 'continue'"
                    )
            for expr in source.refresh.cron:
                if not croniter.is_valid(expr):
                    errors.append(
                        f"source {source.name!r} cron expression is invalid: {expr!r}"
                    )
            if not source.url:
                errors.append(f"source {source.name!r} URL is required")
            else:
                try:
                    assert_safe_url(
                        source.url,
                        allow_private_network=source.fetch.allow_private_network,
                        resolve_dns=False,
                    )
                except SecurityError as exc:
                    errors.append(f"source {source.name!r} URL is unsafe: {exc}")

            if source.dns.enabled:
                if source.dns.failure not in DNS_FAILURES:
                    errors.append(
                        f"source {source.name!r} dns failure must be 'keep', 'drop', or 'fail'"
                    )
                errors.extend(
                    _validate_dns_servers(
                        source.dns.servers,
                        label=f"source {source.name!r}",
                        allow_private_network=self.security.allow_private_network_urls,
                    )
                )

        for plugin in self.plugins.values():
            plugin_header_user_agent = _header_user_agent(plugin.headers)
            if plugin_header_user_agent:
                plugin_header_error = _validate_user_agent(
                    plugin_header_user_agent,
                    label=f"plugin {plugin.name!r} header User-Agent",
                )
                if plugin_header_error:
                    errors.append(plugin_header_error)
            if plugin.type != "http_action":
                errors.append(
                    f"plugin {plugin.name!r} type is unsupported: {plugin.type!r}"
                )
            if not plugin.url:
                errors.append(f"plugin {plugin.name!r} URL is required")
            else:
                try:
                    assert_safe_url(
                        plugin.url,
                        allow_private_network=plugin.allow_private_network,
                        resolve_dns=False,
                    )
                except SecurityError as exc:
                    errors.append(f"plugin {plugin.name!r} URL is unsafe: {exc}")

        try:
            ZoneInfo(self.server.timezone)
        except ZoneInfoNotFoundError:
            errors.append(f"server timezone is invalid: {self.server.timezone!r}")

        if self.scheduler.startup_refresh_mode not in {"background", "blocking"}:
            errors.append(
                f"startup_refresh_mode is unsupported: {self.scheduler.startup_refresh_mode!r}"
            )

        # Directory creation / writability checks live in check_filesystem()
        # so validate() remains side-effect free.

        if config_path and config_path.exists():
            mode = stat.S_IMODE(config_path.stat().st_mode)
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                warnings.append("config file is group/world-readable; use chmod 600")

        return ValidationReport(errors=errors, warnings=warnings)

    def check_filesystem(self) -> list[str]:
        """检查并创建运行时所需的目录，返回错误列表。

        Check (and create) runtime directories. Returns a list of error
        strings. This may create missing parent directories, so it is kept out
        of :meth:`validate`, which must remain side-effect free.

        Returns:
            目录不可写时的错误列表 / List of errors when directories are not writable.
        """
        errors: list[str] = []

        def ensure_writable_dir(path: Path, label: str) -> None:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                errors.append(f"{label} cannot be created: {path}: {exc}")
                return
            if not path.is_dir():
                errors.append(f"{label} is not a directory: {path}")
                return
            if not os.access(path, os.W_OK):
                errors.append(f"{label} is not writable: {path}")

        ensure_writable_dir(self.cache.dir, "cache directory")
        if self.logging_file.enabled and self.logging_file.path:
            ensure_writable_dir(self.logging_file.path.parent, "log directory")
        if self.access_log.enabled:
            ensure_writable_dir(
                self.access_log.db_path.parent, "access log database directory"
            )
            if self.access_log.file.enabled:
                ensure_writable_dir(
                    self.access_log.file.path.parent, "access log directory"
                )
        return errors


def load_config(path: Path, *, validate: bool = True) -> LoadedConfig:
    """从 TOML 文件加载并解析配置。

    Load and parse configuration from a TOML file.

    读取指定路径的 TOML 配置文件，解析所有配置节（server、cache、http、security、
    scheduler、parser、output、sources、routes、plugins），并可选择执行一致性验证。

    Reads the TOML config file at the given path, parses all configuration
    sections (server, cache, http, security, scheduler, parser, output, sources,
    routes, plugins), and optionally runs consistency validation.

    Args:
        path: TOML 配置文件的路径。
              Path to the TOML configuration file.
        validate: 是否在加载后执行验证（默认 True）。
                  Whether to run validation after loading (default True).

    Returns:
        完整的 LoadedConfig 对象。A fully populated LoadedConfig object.

    Raises:
        ValueError: 如果存在未知的顶级表，或验证失败（validate=True 时）。
                    If unknown top-level tables are present, or if validation
                    fails (when validate=True).
    """
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed_top_level = {
        "server",
        "cache",
        "logging",
        "http",
        "scheduler",
        "security",
        "parser",
        "output",
        "sources",
        "routes",
        "plugins",
        "dns",
        "access_log",
    }
    unknown_top_level = sorted(set(raw) - allowed_top_level)
    if unknown_top_level:
        raise ValueError(
            "\n".join(
                f"unsupported top-level table {name!r}" for name in unknown_top_level
            )
        )

    server_raw = _table(raw, "server")
    cache_raw = _table(raw, "cache")
    http_raw = _table(raw, "http")
    security_raw = _table(raw, "security")
    scheduler_raw = _table(raw, "scheduler")
    parser_raw = _table(raw, "parser")
    output_raw = _table(raw, "output")
    logging_raw = _table(raw, "logging")
    dns_raw = _table(raw, "dns")
    access_log_raw = _table(raw, "access_log")
    dns = _dns(dns_raw)

    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(server_raw.get("port", 8080)),
        timezone=server_raw.get("timezone", "Asia/Shanghai"),
        health_path=server_raw.get("health_path", "/healthz"),
        status_path=server_raw.get("status_path"),
        route_refresh_wait=parse_duration(server_raw.get("route_refresh_wait", "10s")),
        public_base_url=server_raw.get("public_base_url"),
    )
    cache = CacheConfig(
        dir=Path(cache_raw.get("dir", "data/cache")),
        write_indent=int(cache_raw.get("write_indent", 2)),
        file_mode=parse_file_mode(cache_raw.get("file_mode", "0600")),
        max_stale=parse_duration(cache_raw.get("max_stale", "7d")),
    )
    http = HttpConfig(
        timeout=parse_duration(http_raw.get("timeout", "30s")),
        user_agent=http_raw.get("user_agent", DEFAULT_USER_AGENT),
        max_response_size=parse_size(http_raw.get("max_response_size", "10 MB")),
        max_redirects=int(http_raw.get("max_redirects", 3)),
    )
    security = SecurityConfig(
        hidden_path_min_entropy_bits=int(
            security_raw.get("hidden_path_min_entropy_bits", 128)
        ),
        allow_private_network_urls=bool(
            security_raw.get("allow_private_network_urls", False)
        ),
    )
    scheduler = SchedulerConfig(
        startup_refresh=bool(scheduler_raw.get("startup_refresh", True)),
        startup_refresh_mode=scheduler_raw.get("startup_refresh_mode", "background"),
        jitter=parse_duration(scheduler_raw.get("jitter", "30s")),
        refresh_lock_timeout=parse_duration(
            scheduler_raw.get("refresh_lock_timeout", "35s")
        ),
    )
    parser = ParserConfig(
        default_format=parser_raw.get("default_format", "auto"),
        default_parse_error=parser_raw.get("default_parse_error", "skip"),
    )
    output = OutputConfig(
        yaml_sort_keys=bool(output_raw.get("yaml_sort_keys", False)),
        default_include_meta_comments=bool(
            output_raw.get("default_include_meta_comments", False)
        ),
    )
    console_raw = _table(logging_raw, "console")
    file_raw = _table(logging_raw, "file")
    logging_console = LoggingSinkConfig(
        enabled=bool(console_raw.get("enabled", True)),
        level=console_raw.get("level", "INFO"),
        colorize=bool(console_raw.get("colorize", True)),
    )
    logging_file = LoggingSinkConfig(
        enabled=bool(file_raw.get("enabled", False)),
        level=file_raw.get("level", "DEBUG"),
        path=Path(file_raw.get("path", "logs/mihomo-proxy-manager.log")),
        rotation=file_raw.get("rotation", "10 MB"),
        retention=file_raw.get("retention", "14 days"),
        compression=file_raw.get("compression", "gz"),
    )

    plugins = {}
    for name, values in _table(raw, "plugins").items():
        success_status = values.get("success_status", (200,))
        if isinstance(success_status, int):
            success_status = (success_status,)
        plugins[name] = PluginConfig(
            name=name,
            type=values.get("type", "http_action"),
            method=values.get("method", "GET"),
            url=values.get("url", ""),
            headers={str(k): str(v) for k, v in _table(values, "headers").items()},
            success_status=tuple(success_status),
            timeout=parse_duration(
                values.get("timeout", f"{int(http.timeout.total_seconds())}s")
            ),
            allow_private_network=bool(
                values.get("allow_private_network", security.allow_private_network_urls)
            ),
            body=values.get("body"),
        )

    sources = {}
    for name, values in _table(raw, "sources").items():
        source_fetch = _fetch(_table(values, "fetch"), http, security)
        sources[name] = SourceConfig(
            name=name,
            url=values.get("url", ""),
            format=values.get("format", parser.default_format),
            parse_error=values.get("parse_error", parser.default_parse_error),
            fetch=source_fetch,
            refresh=_refresh(_table(values, "refresh")),
            rename=_rename(_table(values, "rename")),
            filter=_filter(_table(values, "filter")),
            plugins=_source_plugins(_table(values, "plugins")),
            dns=_source_dns(_table(values, "dns"), dns),
        )

    routes = {}
    allowed_route_output_keys = {
        "format",
        "auto_default",
        "include_meta_comments",
        "mode",
        "encoding",
        "import_link",
        "import_response",
        "import_target",
        "resource_tag",
        "test_url",
        "test_interval",
        "test_timeout",
        "test_tolerance",
    }
    for name, values in _table(raw, "routes").items():
        output_values = _table(values, "output")
        unknown_output_keys = sorted(set(output_values) - allowed_route_output_keys)
        if unknown_output_keys:
            raise ValueError(
                "\n".join(
                    f"route {name!r} output key is unsupported: {key!r}"
                    for key in unknown_output_keys
                )
            )
        output_format = output_values.get("format", "provider")
        include_meta_comments = bool(
            output_values.get(
                "include_meta_comments",
                output.default_include_meta_comments
                if output_format == "provider"
                else False,
            )
        )
        routes[name] = RouteConfig(
            name=name,
            path=values.get("path", ""),
            sources=tuple(values.get("sources", ())),
            require_all_sources=bool(values.get("require_all_sources", False)),
            output=RouteOutputConfig(
                format=output_format,
                auto_default=output_values.get("auto_default", "provider"),
                include_meta_comments=include_meta_comments,
                mode=output_values.get("mode", "default"),
                encoding=output_values.get("encoding", "base64"),
                import_link=bool(output_values.get("import_link", True)),
                import_response=output_values.get("import_response", "redirect"),
                import_target=output_values.get("import_target", "app-scheme"),
                resource_tag=output_values.get("resource_tag"),
                test_url=output_values.get(
                    "test_url", "http://www.gstatic.com/generate_204"
                ),
                test_interval=int(output_values.get("test_interval", 600)),
                test_timeout=int(output_values.get("test_timeout", 5)),
                test_tolerance=int(output_values.get("test_tolerance", 100)),
            ),
            rename=_rename(_table(values, "rename")),
            filter=_filter(_table(values, "filter")),
            access=_route_access(_table(values, "access")),
        )

    config = LoadedConfig(
        server=server,
        cache=cache,
        logging_console=logging_console,
        logging_file=logging_file,
        http=http,
        scheduler=scheduler,
        security=security,
        parser=parser,
        output=output,
        sources=sources,
        routes=routes,
        plugins=plugins,
        dns=dns,
        access_log=_access_log(access_log_raw),
    )
    if validate:
        report = config.validate(config_path=path)
        fs_errors = config.check_filesystem()
        all_errors = list(report.errors) + fs_errors
        if all_errors:
            raise ValueError("\n".join(all_errors))
    return config
