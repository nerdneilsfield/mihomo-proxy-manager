from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
import uvicorn

from .app import create_app
from .cache import JsonSourceCacheStore
from .config import load_config
from .fetcher import SafeHttpClient, SubscriptionFetcher, _NoOpCookies
from .logging import configure_logging
from .plugins.http_action import HttpActionPlugin
from .refresher import SourceRefresher
from .scheduler import RefreshScheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")

    return parser


def _cmd_check(config_path: str) -> int:
    config_file = Path(config_path)
    config = load_config(config_file, validate=False)
    report = config.validate(config_path=config_file)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.ok:
        print("OK: configuration is valid")
        return 0
    return 1


async def _build_runtime(config_path: str):
    config_file = Path(config_path)
    config = load_config(config_file)
    configure_logging(config)
    cache_store = JsonSourceCacheStore(config.cache)
    client = httpx.AsyncClient(cookies=_NoOpCookies())
    fetcher = SubscriptionFetcher(client, config.http)
    plugin = HttpActionPlugin(SafeHttpClient(client, config.http))
    refresher = SourceRefresher(
        sources=config.sources,
        plugins=config.plugins,
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=plugin,
        refresh_lock_timeout=config.scheduler.refresh_lock_timeout,
    )
    return config, cache_store, client, refresher


def _cmd_serve(config_path: str) -> int:
    async def run() -> int:
        config, cache_store, client, refresher = await _build_runtime(config_path)
        scheduler = RefreshScheduler(config, refresher)
        app = create_app(config, cache_store=cache_store, refresher=refresher, scheduler=scheduler)
        try:
            server_config = uvicorn.Config(app, host=config.server.host, port=config.server.port, access_log=False)
            server = uvicorn.Server(server_config)
            await server.serve()
            return 0
        finally:
            await client.aclose()

    return asyncio.run(run())


def _cmd_refresh(config_path: str, source_name: str) -> int:
    async def run() -> int:
        config, _cache_store, client, refresher = await _build_runtime(config_path)
        try:
            if source_name not in config.sources:
                print(f"ERROR: unknown source {source_name!r}")
                return 1
            result = await refresher.refresh(source_name)
            if result.ok:
                print(f"OK: refreshed {result.source}: nodes={result.node_count} warnings={result.warning_count} cache={result.cache_path}")
                return 0
            print(f"ERROR: refresh failed for {result.source}: nodes={result.node_count} warnings={result.warning_count} cache={result.cache_path} error={result.error}")
            return 1
        finally:
            await client.aclose()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.config)
    if args.command == "serve":
        return _cmd_serve(args.config)
    if args.command == "refresh":
        return _cmd_refresh(args.config, args.source)
    parser.error("unreachable command")
    return 2
