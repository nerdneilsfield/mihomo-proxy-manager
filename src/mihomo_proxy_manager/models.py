from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

ProxyDict = dict[str, Any]


@dataclass(frozen=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class FilterConfig:
    include: str | None = None
    exclude: str | None = None
    include_types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenameConfig:
    prefix: str = ""
    suffix: str = ""


@dataclass(frozen=True)
class FetchConfig:
    timeout: timedelta
    user_agent: str
    headers: dict[str, str] = field(default_factory=dict)
    allow_private_network: bool = False


@dataclass(frozen=True)
class RefreshConfig:
    interval: timedelta | None = None
    cron: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginRefConfig:
    on_failure: Literal["abort", "continue"] = "abort"


@dataclass(frozen=True)
class SourcePluginConfig:
    before_fetch: dict[str, PluginRefConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    format: Literal["auto", "yaml", "share-links"]
    parse_error: Literal["skip", "fail"]
    fetch: FetchConfig
    refresh: RefreshConfig
    rename: RenameConfig
    filter: FilterConfig
    plugins: SourcePluginConfig


@dataclass(frozen=True)
class RouteOutputConfig:
    format: Literal["provider"] = "provider"
    include_meta_comments: bool = False


@dataclass(frozen=True)
class RouteConfig:
    name: str
    path: str
    sources: tuple[str, ...]
    require_all_sources: bool
    output: RouteOutputConfig
    rename: RenameConfig
    filter: FilterConfig


@dataclass(frozen=True)
class PluginConfig:
    name: str
    type: Literal["http_action"]
    method: str
    url: str
    headers: dict[str, str]
    success_status: tuple[int, ...]
    timeout: timedelta
    allow_private_network: bool
    body: str | None = None


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    timezone: str
    health_path: str
    status_path: str | None
    route_refresh_wait: timedelta


@dataclass(frozen=True)
class CacheConfig:
    dir: Path
    write_indent: int
    file_mode: int
    max_stale: timedelta


@dataclass(frozen=True)
class LoggingSinkConfig:
    enabled: bool
    level: str
    colorize: bool = False
    path: Path | None = None
    rotation: str | None = None
    retention: str | None = None
    compression: str | None = None


@dataclass(frozen=True)
class HttpConfig:
    timeout: timedelta
    user_agent: str
    max_response_size: int
    max_redirects: int


@dataclass(frozen=True)
class SchedulerConfig:
    startup_refresh: bool
    startup_refresh_mode: Literal["background", "blocking"]
    jitter: timedelta
    refresh_lock_timeout: timedelta


@dataclass(frozen=True)
class SecurityConfig:
    hidden_path_min_entropy_bits: int
    allow_private_network_urls: bool


@dataclass(frozen=True)
class ParserConfig:
    default_format: Literal["auto", "yaml", "share-links"]
    default_parse_error: Literal["skip", "fail"]


@dataclass(frozen=True)
class OutputConfig:
    yaml_sort_keys: bool
    default_include_meta_comments: bool


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    cache: CacheConfig
    logging_console: LoggingSinkConfig
    logging_file: LoggingSinkConfig
    http: HttpConfig
    scheduler: SchedulerConfig
    security: SecurityConfig
    parser: ParserConfig
    output: OutputConfig
    sources: dict[str, SourceConfig]
    routes: dict[str, RouteConfig]
    plugins: dict[str, PluginConfig]


@dataclass(frozen=True)
class ProxyRecord:
    source: str
    data: ProxyDict


@dataclass(frozen=True)
class SourceCache:
    source: str
    schema_version: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    etag: str | None
    last_modified: str | None
    node_count: int
    warnings: tuple[str, ...]
    last_error: str | None
    proxies: tuple[ProxyRecord, ...]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    node_count: int
    last_error: str | None
    refreshing: bool = False
