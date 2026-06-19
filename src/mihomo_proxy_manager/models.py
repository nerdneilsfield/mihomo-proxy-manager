"""数据模型定义，包括配置、代理记录、缓存和状态等 dataclass。

Data model definitions including dataclasses for config, proxy records, cache, and status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

ProxyDict = dict[str, Any]


@dataclass(frozen=True)
class ValidationReport:
    """验证报告，包含错误和警告列表。

    Validation report containing lists of errors and warnings.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """验证是否通过（无错误）。

        Whether validation passed (no errors)."""
        return not self.errors


@dataclass(frozen=True)
class FilterConfig:
    """代理过滤配置，支持按名称和类型包含/排除。

    Proxy filter configuration supporting include/exclude by name and type.
    """

    include: str | None = None
    exclude: str | None = None
    include_types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenameConfig:
    """代理重命名配置，支持前缀和后缀。

    Proxy rename configuration supporting prefix and suffix.
    """

    prefix: str = ""
    suffix: str = ""


@dataclass(frozen=True)
class FetchConfig:
    """抓取配置，包含超时、User-Agent 和自定义请求头。

    Fetch configuration with timeout, User-Agent, and custom headers.
    """

    timeout: timedelta
    user_agent: str
    headers: dict[str, str] = field(default_factory=dict)
    allow_private_network: bool = False


@dataclass(frozen=True)
class RefreshConfig:
    """刷新配置，支持固定间隔和 cron 表达式。

    Refresh configuration supporting fixed intervals and cron expressions.
    """

    interval: timedelta | None = None
    cron: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginRefConfig:
    """插件引用配置，定义插件失败时的行为。

    Plugin reference configuration defining failure behavior.
    """

    on_failure: Literal["abort", "continue"] = "abort"


@dataclass(frozen=True)
class SourcePluginConfig:
    """源插件配置，包含抓取前执行的插件列表。

    Source plugin configuration containing plugins to run before fetch.
    """

    before_fetch: dict[str, PluginRefConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class DnsConfig:
    """Global DNS resolution defaults."""

    servers: tuple[str, ...] = ("udp://1.1.1.1:53",)
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    failure: Literal["keep", "drop", "fail"] = "keep"
    enable_ipv6: bool = False


@dataclass(frozen=True)
class SourceDnsConfig:
    """Per-source DNS resolution behavior."""

    enabled: bool = False
    servers: tuple[str, ...] = ("udp://1.1.1.1:53",)
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    failure: Literal["keep", "drop", "fail"] = "keep"
    enable_ipv6: bool = False


@dataclass(frozen=True)
class SourceConfig:
    """订阅源配置，包含 URL、格式、抓取、刷新、重命名和过滤设置。

    Source subscription configuration with URL, format, fetch, refresh, rename, and filter settings.
    """

    name: str
    url: str
    format: Literal["auto", "yaml", "share-links"]
    parse_error: Literal["skip", "fail"]
    fetch: FetchConfig
    refresh: RefreshConfig
    rename: RenameConfig
    filter: FilterConfig
    plugins: SourcePluginConfig
    dns: SourceDnsConfig = field(default_factory=SourceDnsConfig)


@dataclass(frozen=True)
class RouteOutputConfig:
    """路由输出配置，控制输出格式和元数据注释。

    Route output configuration controlling output format and meta comments.
    """

    format: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = "provider"
    include_meta_comments: bool = False
    mode: Literal["default", "full-profile", "server-remote"] = "default"
    encoding: Literal["base64", "plain"] = "base64"
    import_link: bool = True
    import_response: Literal["redirect", "plain"] = "redirect"
    import_target: Literal["app-scheme", "universal-link"] = "app-scheme"
    resource_tag: str | None = None
    test_url: str = "http://www.gstatic.com/generate_204"
    test_interval: int = 600
    test_timeout: int = 5
    test_tolerance: int = 100


@dataclass(frozen=True)
class RouteAccessConfig:
    """Route access control configuration."""

    user_agent: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteConfig:
    """路由配置，定义输出路径、来源和转换规则。

    Route configuration defining output path, sources, and transform rules.
    """

    name: str
    path: str
    sources: tuple[str, ...]
    require_all_sources: bool
    output: RouteOutputConfig
    rename: RenameConfig
    filter: FilterConfig
    access: RouteAccessConfig = field(default_factory=RouteAccessConfig)


@dataclass(frozen=True)
class PluginConfig:
    """插件配置，定义 HTTP Action 插件的请求参数。

    Plugin configuration defining HTTP Action plugin request parameters.
    """

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
    """服务器配置，包含监听地址、端口、时区和健康检查路径。

    Server configuration with listen address, port, timezone, and health check path.
    """

    host: str
    port: int
    timezone: str
    health_path: str
    status_path: str | None
    route_refresh_wait: timedelta
    public_base_url: str | None = None


@dataclass(frozen=True)
class CacheConfig:
    """缓存配置，包含目录、缩进、文件权限和最大过期时间。

    Cache configuration with directory, indentation, file mode, and max stale time.
    """

    dir: Path
    write_indent: int
    file_mode: int
    max_stale: timedelta


@dataclass(frozen=True)
class LoggingSinkConfig:
    """日志输出配置，支持控制台和文件两种 sink。

    Logging sink configuration supporting console and file sinks.
    """

    enabled: bool
    level: str
    colorize: bool = False
    path: Path | None = None
    rotation: str | None = None
    retention: str | None = None
    compression: str | None = None


@dataclass(frozen=True)
class HttpConfig:
    """HTTP 客户端配置，包含超时、User-Agent、响应大小限制和重定向次数。

    HTTP client configuration with timeout, User-Agent, response size limit, and max redirects.
    """

    timeout: timedelta
    user_agent: str
    max_response_size: int
    max_redirects: int


@dataclass(frozen=True)
class SchedulerConfig:
    """调度器配置，包含启动刷新、抖动和锁超时。

    Scheduler configuration with startup refresh, jitter, and lock timeout.
    """

    startup_refresh: bool
    startup_refresh_mode: Literal["background", "blocking"]
    jitter: timedelta
    refresh_lock_timeout: timedelta


@dataclass(frozen=True)
class SecurityConfig:
    """安全配置，包含路径最小熵和私有网络 URL 允许设置。

    Security configuration with minimum path entropy and private network URL allowance.
    """

    hidden_path_min_entropy_bits: int
    allow_private_network_urls: bool


@dataclass(frozen=True)
class ParserConfig:
    """解析器配置，包含默认格式和解析错误处理方式。

    Parser configuration with default format and parse error handling.
    """

    default_format: Literal["auto", "yaml", "share-links"]
    default_parse_error: Literal["skip", "fail"]


@dataclass(frozen=True)
class OutputConfig:
    """输出配置，包含 YAML 键排序和默认元注释设置。

    Output configuration with YAML key sorting and default meta comment settings.
    """

    yaml_sort_keys: bool
    default_include_meta_comments: bool


@dataclass(frozen=True)
class AppConfig:
    """应用顶层配置，聚合所有子配置。

    Top-level application configuration aggregating all sub-configurations.
    """

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
    dns: DnsConfig = field(default_factory=DnsConfig)


@dataclass(frozen=True)
class ProxyRecord:
    """代理记录，包含来源名称和代理数据字典。

    Proxy record containing source name and proxy data dictionary.
    """

    source: str
    data: ProxyDict


@dataclass(frozen=True)
class SourceCache:
    """源缓存，包含元数据、节点计数和代理列表。

    Source cache containing metadata, node count, and proxy list.
    """

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
    """源状态，包含刷新状态和节点信息。

    Source status containing refresh state and node information.
    """

    source: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    node_count: int
    last_error: str | None
    refreshing: bool = False
