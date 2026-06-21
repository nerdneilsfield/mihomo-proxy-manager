"""命令行接口，提供 ``serve``、``check``、``refresh`` 子命令。

Command-line interface providing ``serve``, ``check``, and ``refresh`` subcommands.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx
import uvicorn

from .access_audit import AccessAuditStore, SQLiteAccessAuditStore
from .app import create_app
from .cache import JsonSourceCacheStore
from .config import load_config
from .dns import DnsClient, DnsResolver
from .fetcher import SafeHttpClient, SubscriptionFetcher, _NoOpCookies
from .logging import configure_logging
from .models import AppConfig, HttpConfig
from .plugins.http_action import HttpActionPlugin
from .refresher import SourceRefresher
from .scheduler import RefreshScheduler


@dataclass(frozen=True)
class Runtime:
    """CLI runtime dependencies."""

    config: AppConfig
    cache_store: JsonSourceCacheStore
    client: httpx.AsyncClient
    refresher: SourceRefresher
    access_audit_store: AccessAuditStore | None = None


def build_parser() -> argparse.ArgumentParser:
    """构建并返回命令行参数解析器。

    Build and return the command-line argument parser.

    Returns:
        argparse.ArgumentParser: 配置了 ``serve``、``check``、``refresh`` 子命令的解析器。
            Parser configured with ``serve``, ``check``, and ``refresh`` subcommands.
    """
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)
    serve.add_argument(
        "--debug", action="store_true", help="force console log level to DEBUG"
    )

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")
    refresh.add_argument(
        "--debug", action="store_true", help="force console log level to DEBUG"
    )

    return parser


def _cmd_check(config_path: str) -> int:
    """验证配置文件并打印检查报告。

    Validate the configuration file and print the check report.

    Args:
        config_path: 配置文件路径。
            Path to the configuration file.

    Returns:
        int: 如果配置有效返回 0，否则返回 1。
            0 if the configuration is valid, 1 otherwise.
    """
    config_file = Path(config_path)
    config = load_config(config_file, validate=False)
    report = config.validate(config_path=config_file)
    filesystem_errors = config.check_filesystem()
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    for error in filesystem_errors:
        print(f"ERROR: {error}")
    if report.ok and not filesystem_errors:
        print("OK: configuration is valid")
        return 0
    return 1


async def _build_runtime(
    config_path: str, *, debug: bool = False, access_audit: bool = False
) -> Runtime:
    """构建运行时组件：配置、缓存、HTTP 客户端和刷新器。

    Build runtime components: config, cache store, HTTP client, and refresher.

    Args:
        config_path: 配置文件路径。
            Path to the configuration file.
        debug: 是否强制控制台日志为 DEBUG 级别 / Force console log level to DEBUG.
        access_audit: 是否初始化访问审计存储 / Whether to initialize access audit store.

    Returns:
        Runtime: 包含运行时依赖的 dataclass。
            Dataclass containing runtime dependencies.
    """
    config_file = Path(config_path)
    config = load_config(config_file)
    configure_logging(config, debug=debug)
    access_audit_store = None
    if access_audit and config.access_log.enabled:
        access_audit_store = SQLiteAccessAuditStore(config.access_log)
    cache_store = JsonSourceCacheStore(config.cache)
    client = httpx.AsyncClient(cookies=_NoOpCookies())
    plugin_safe_http = SafeHttpClient(client, config.http)
    dns_http_config = HttpConfig(
        timeout=config.http.timeout,
        user_agent=config.http.user_agent,
        max_response_size=4096,
        max_redirects=config.http.max_redirects,
    )
    dns_safe_http = SafeHttpClient(client, dns_http_config)
    fetcher = SubscriptionFetcher(client, config.http)
    plugin = HttpActionPlugin(plugin_safe_http)
    dns_client = DnsClient(safe_http=dns_safe_http)
    dns_resolver = DnsResolver(
        client=dns_client,
        allow_private_network=config.security.allow_private_network_urls,
    )
    refresher = SourceRefresher(
        sources=config.sources,
        plugins=config.plugins,
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=plugin,
        refresh_lock_timeout=config.scheduler.refresh_lock_timeout,
        dns_resolver=dns_resolver,
    )
    return Runtime(
        config=config,
        cache_store=cache_store,
        client=client,
        refresher=refresher,
        access_audit_store=access_audit_store,
    )


def _cmd_serve(config_path: str, *, debug: bool = False) -> int:
    """启动 HTTP 服务。

    Start the HTTP service.

    Args:
        config_path: 配置文件路径。
            Path to the configuration file.
        debug: 是否强制控制台日志为 DEBUG 级别 / Force console log level to DEBUG.

    Returns:
        int: 服务正常退出时返回 0。
            0 when the server exits normally.
    """

    async def run() -> int:
        runtime = await _build_runtime(config_path, debug=debug, access_audit=True)
        try:
            scheduler = RefreshScheduler(runtime.config, runtime.refresher)
            app = create_app(
                runtime.config,
                cache_store=runtime.cache_store,
                refresher=runtime.refresher,
                scheduler=scheduler,
                access_audit_store=runtime.access_audit_store,
            )
            server_config = uvicorn.Config(
                app,
                host=runtime.config.server.host,
                port=runtime.config.server.port,
                access_log=False,
            )
            server = uvicorn.Server(server_config)
            await server.serve()
            return 0
        except Exception:
            if runtime.access_audit_store is not None:
                with suppress(Exception):
                    runtime.access_audit_store.dispose()
            raise
        finally:
            await runtime.client.aclose()

    return asyncio.run(run())


def _cmd_refresh(config_path: str, source_name: str, *, debug: bool = False) -> int:
    """刷新指定数据源并打印结果。

    Refresh the specified source and print the result.

    Args:
        config_path: 配置文件路径。
            Path to the configuration file.
        source_name: 要刷新的数据源名称。
            Name of the source to refresh.
        debug: 是否强制控制台日志为 DEBUG 级别 / Force console log level to DEBUG.

    Returns:
        int: 刷新成功返回 0，失败或找不到数据源返回 1。
            0 on success, 1 if the source is unknown or refresh fails.
    """

    async def run() -> int:
        runtime = await _build_runtime(config_path, debug=debug)
        try:
            if source_name not in runtime.config.sources:
                print(f"ERROR: unknown source {source_name!r}")
                return 1
            result = await runtime.refresher.refresh(source_name)
            if result.ok:
                print(
                    f"OK: refreshed {result.source}: nodes={result.node_count} warnings={result.warning_count} cache={result.cache_path}"
                )
                return 0
            print(
                f"ERROR: refresh failed for {result.source}: nodes={result.node_count} warnings={result.warning_count} cache={result.cache_path} error={result.error}"
            )
            return 1
        finally:
            await runtime.client.aclose()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    """CLI 入口函数，解析参数并派发到对应的子命令处理函数。

    CLI entry point that parses arguments and dispatches to the appropriate command handler.

    Args:
        argv: 命令行参数列表，默认为 ``None``（使用 ``sys.argv``）。
            Command-line argument list; defaults to ``None`` (uses ``sys.argv``).

    Returns:
        int: 进程退出码。0 表示成功，1 表示错误，2 表示不可达分支。
            Process exit code. 0 for success, 1 for error, 2 for unreachable branch.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.config)
    if args.command == "serve":
        return _cmd_serve(args.config, debug=args.debug)
    if args.command == "refresh":
        return _cmd_refresh(args.config, args.source, debug=args.debug)
    parser.error("unreachable command")
    return 2
