from __future__ import annotations

import os
import re
import stat
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .security import SecurityError, assert_safe_url, has_path_entropy

from .models import (
    AppConfig,
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    PluginConfig,
    PluginRefConfig,
    RefreshConfig,
    RenameConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourcePluginConfig,
    ValidationReport,
)


def parse_duration(value: str) -> timedelta:
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
    match = re.fullmatch(r"(\d+)\s*(B|KB|MB)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid size {value!r}")
    amount = int(match.group(1))
    unit = match.group(2).upper()
    return amount * {"B": 1, "KB": 1024, "MB": 1024 * 1024}[unit]


def parse_file_mode(value: str | int) -> int:
    """Parse a file mode value.

    Integers are accepted as-is, so use an octal literal such as ``0o600`` in
    TOML. Strings are parsed as octal when they start with ``0`` or ``0o``;
    otherwise they are parsed as decimal integers.
    """
    if isinstance(value, int):
        return value
    s = str(value).strip()
    try:
        if s.startswith(("0o", "0O")) or (len(s) > 1 and s.startswith("0") and s.isdigit()):
            return int(s, 8)
        return int(s)
    except ValueError as exc:
        raise ValueError(f"invalid file mode {value!r}") from exc


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _filter(data: dict[str, Any]) -> FilterConfig:
    return FilterConfig(
        include=data.get("include"),
        exclude=data.get("exclude"),
        include_types=tuple(data.get("include_types", ())),
        exclude_types=tuple(data.get("exclude_types", ())),
    )


def _rename(data: dict[str, Any]) -> RenameConfig:
    return RenameConfig(prefix=data.get("prefix", ""), suffix=data.get("suffix", ""))


def _fetch(data: dict[str, Any], http: HttpConfig, security: SecurityConfig) -> FetchConfig:
    headers = _table(data, "headers")
    return FetchConfig(
        timeout=parse_duration(data.get("timeout", f"{int(http.timeout.total_seconds())}s")),
        user_agent=data.get("user_agent", http.user_agent),
        headers={str(k): str(v) for k, v in headers.items()},
        allow_private_network=bool(data.get("allow_private_network", security.allow_private_network_urls)),
    )


def _refresh(data: dict[str, Any]) -> RefreshConfig:
    interval = data.get("interval")
    cron = data.get("cron", ())
    if isinstance(cron, str):
        cron = (cron,)
    return RefreshConfig(
        interval=parse_duration(interval) if interval else None,
        cron=tuple(cron),
    )


def _source_plugins(data: dict[str, Any]) -> SourcePluginConfig:
    before_fetch_table = _table(data, "before_fetch")
    before_fetch = {
        name: PluginRefConfig(on_failure=values.get("on_failure", "abort"))
        for name, values in before_fetch_table.items()
    }
    return SourcePluginConfig(before_fetch=before_fetch)


class LoadedConfig(AppConfig):
    def validate(self, config_path: Path | None = None) -> ValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        paths: dict[str, str] = {self.server.health_path: "health_path"}
        if self.server.status_path:
            if self.server.status_path in paths:
                errors.append("health_path and status_path collide")
            paths[self.server.status_path] = "status_path"
        if self.server.status_path and not has_path_entropy(
            self.server.status_path,
            min_bits=self.security.hidden_path_min_entropy_bits,
        ):
            errors.append("status_path does not satisfy hidden path entropy requirement")
        for route in self.routes.values():
            if not route.path.startswith("/"):
                errors.append(f"route {route.name!r} path must start with '/'")
            if not has_path_entropy(route.path, min_bits=self.security.hidden_path_min_entropy_bits):
                errors.append(f"route {route.name!r} path does not satisfy hidden path entropy requirement")
            key = f"route {route.name!r}"
            if route.path in paths:
                errors.append(f"path collision for {key} with {paths[route.path]}")
            paths[route.path] = key
            for source in route.sources:
                if source not in self.sources:
                    errors.append(f"route {route.name!r} references missing source {source!r}")
            for pattern_name, pattern in (("include", route.filter.include), ("exclude", route.filter.exclude)):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"route {route.name!r} {pattern_name} regex is invalid: {exc}")
            if route.output.format != "provider":
                errors.append(f"route {route.name!r} output format is unsupported: {route.output.format!r}")

        for source in self.sources.values():
            if source.format not in {"auto", "yaml", "share-links"}:
                errors.append(f"source {source.name!r} format is unsupported: {source.format!r}")
            if source.parse_error not in {"skip", "fail"}:
                errors.append(f"source {source.name!r} parse_error is unsupported: {source.parse_error!r}")
            for pattern_name, pattern in (("include", source.filter.include), ("exclude", source.filter.exclude)):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"source {source.name!r} {pattern_name} regex is invalid: {exc}")
            for plugin_name in source.plugins.before_fetch:
                if plugin_name not in self.plugins:
                    errors.append(f"source {source.name!r} references missing plugin {plugin_name!r}")
            for expr in source.refresh.cron:
                if not croniter.is_valid(expr):
                    errors.append(f"source {source.name!r} cron expression is invalid: {expr!r}")
            try:
                assert_safe_url(source.url, allow_private_network=source.fetch.allow_private_network, resolve_dns=False)
            except SecurityError as exc:
                errors.append(f"source {source.name!r} URL is unsafe: {exc}")

        for plugin in self.plugins.values():
            if plugin.type != "http_action":
                errors.append(f"plugin {plugin.name!r} type is unsupported: {plugin.type!r}")
            try:
                assert_safe_url(plugin.url, allow_private_network=plugin.allow_private_network, resolve_dns=False)
            except SecurityError as exc:
                errors.append(f"plugin {plugin.name!r} URL is unsafe: {exc}")

        try:
            ZoneInfo(self.server.timezone)
        except ZoneInfoNotFoundError:
            errors.append(f"server timezone is invalid: {self.server.timezone!r}")

        if self.scheduler.startup_refresh_mode not in {"background", "blocking"}:
            errors.append(f"startup_refresh_mode is unsupported: {self.scheduler.startup_refresh_mode!r}")

        self.cache.dir.mkdir(parents=True, exist_ok=True)
        if not os.access(self.cache.dir, os.W_OK):
            errors.append(f"cache directory is not writable: {self.cache.dir}")
        if self.logging_file.enabled and self.logging_file.path:
            self.logging_file.path.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(self.logging_file.path.parent, os.W_OK):
                errors.append(f"log directory is not writable: {self.logging_file.path.parent}")

        if config_path and config_path.exists():
            mode = stat.S_IMODE(config_path.stat().st_mode)
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                warnings.append("config file is group/world-readable; use chmod 600")

        return ValidationReport(errors=errors, warnings=warnings)


def load_config(path: Path, *, validate: bool = True) -> LoadedConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed_top_level = {
        "server", "cache", "logging", "http", "scheduler", "security",
        "parser", "output", "sources", "routes", "plugins",
    }
    unknown_top_level = sorted(set(raw) - allowed_top_level)
    if unknown_top_level:
        raise ValueError("\n".join(f"unsupported top-level table {name!r}" for name in unknown_top_level))

    server_raw = _table(raw, "server")
    cache_raw = _table(raw, "cache")
    http_raw = _table(raw, "http")
    security_raw = _table(raw, "security")
    scheduler_raw = _table(raw, "scheduler")
    parser_raw = _table(raw, "parser")
    output_raw = _table(raw, "output")
    logging_raw = _table(raw, "logging")

    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(server_raw.get("port", 8080)),
        timezone=server_raw.get("timezone", "Asia/Shanghai"),
        health_path=server_raw.get("health_path", "/healthz"),
        status_path=server_raw.get("status_path"),
        route_refresh_wait=parse_duration(server_raw.get("route_refresh_wait", "10s")),
    )
    cache = CacheConfig(
        dir=Path(cache_raw.get("dir", "data/cache")),
        write_indent=int(cache_raw.get("write_indent", 2)),
        file_mode=parse_file_mode(cache_raw.get("file_mode", "0600")),
        max_stale=parse_duration(cache_raw.get("max_stale", "7d")),
    )
    http = HttpConfig(
        timeout=parse_duration(http_raw.get("timeout", "30s")),
        user_agent=http_raw.get("user_agent", "mihomo-proxy-manager/0.1"),
        max_response_size=parse_size(http_raw.get("max_response_size", "10 MB")),
        max_redirects=int(http_raw.get("max_redirects", 3)),
    )
    security = SecurityConfig(
        hidden_path_min_entropy_bits=int(security_raw.get("hidden_path_min_entropy_bits", 128)),
        allow_private_network_urls=bool(security_raw.get("allow_private_network_urls", False)),
    )
    scheduler = SchedulerConfig(
        startup_refresh=bool(scheduler_raw.get("startup_refresh", True)),
        startup_refresh_mode=scheduler_raw.get("startup_refresh_mode", "background"),
        jitter=parse_duration(scheduler_raw.get("jitter", "30s")),
        refresh_lock_timeout=parse_duration(scheduler_raw.get("refresh_lock_timeout", "35s")),
    )
    parser = ParserConfig(
        default_format=parser_raw.get("default_format", "auto"),
        default_parse_error=parser_raw.get("default_parse_error", "skip"),
    )
    output = OutputConfig(
        yaml_sort_keys=bool(output_raw.get("yaml_sort_keys", False)),
        default_include_meta_comments=bool(output_raw.get("default_include_meta_comments", False)),
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
            timeout=parse_duration(values.get("timeout", f"{int(http.timeout.total_seconds())}s")),
            allow_private_network=bool(values.get("allow_private_network", security.allow_private_network_urls)),
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
        )

    routes = {}
    for name, values in _table(raw, "routes").items():
        output_values = _table(values, "output")
        routes[name] = RouteConfig(
            name=name,
            path=values.get("path", ""),
            sources=tuple(values.get("sources", ())),
            require_all_sources=bool(values.get("require_all_sources", False)),
            output=RouteOutputConfig(
                format=output_values.get("format", "provider"),
                include_meta_comments=bool(output_values.get("include_meta_comments", output.default_include_meta_comments)),
            ),
            rename=_rename(_table(values, "rename")),
            filter=_filter(_table(values, "filter")),
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
    )
    if validate:
        report = config.validate(config_path=path)
        if not report.ok:
            raise ValueError("\n".join(report.errors))
    return config
