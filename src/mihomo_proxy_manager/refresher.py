"""订阅刷新核心逻辑：插件执行、抓取、解析、转换和缓存。

Core refresh logic: plugin execution, fetch, parse, transform, and cache.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .cache import CURRENT_SCHEMA_VERSION, SourceCacheStore
from .models import PluginConfig, SourceCache, SourceConfig
from .parsers import ParseError, parse_subscription
from .plugins.http_action import HttpActionPlugin, PluginContext
from .security import redact_secret
from .transform import apply_transform


@dataclass(frozen=True)
class RefreshResult:
    """刷新结果，包含成功状态、节点数和错误信息。

    Refresh result containing success status, node count, and error information.
    """

    ok: bool
    source: str
    node_count: int = 0
    warning_count: int = 0
    cache_path: str | None = None
    error: str | None = None


class SourceRefresher:
    """订阅源刷新器，负责执行完整的刷新流程。

    Source refresher responsible for executing the full refresh pipeline.
    """

    def __init__(
        self,
        *,
        sources: dict[str, SourceConfig],
        plugins: dict[str, PluginConfig],
        cache_store: SourceCacheStore,
        fetcher: Any,
        http_plugin: HttpActionPlugin | None,
        refresh_lock_timeout: timedelta,
    ) -> None:
        """初始化 SourceRefresher。

        Initialize SourceRefresher.

        Args:
            sources: 订阅源配置字典 / Source configuration dict.
            plugins: 插件配置字典 / Plugin configuration dict.
            cache_store: 缓存存储实例 / Cache store instance.
            fetcher: HTTP 抓取器实例 / HTTP fetcher instance.
            http_plugin: HTTP Action 插件实例，可为 None / HTTP Action plugin instance, may be None.
            refresh_lock_timeout: 刷新锁超时时间 / Refresh lock timeout.
        """
        self.sources = sources
        self.plugins = plugins
        self.cache_store = cache_store
        self.fetcher = fetcher
        self.http_plugin = http_plugin
        self.refresh_lock_timeout = refresh_lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Task[RefreshResult]] = {}

    def _lock(self, source_name: str) -> asyncio.Lock:
        """获取或创建指定源的异步锁。

        Get or create an async lock for the given source.

        Args:
            source_name: 订阅源名称 / Source name.

        Returns:
            该源的异步锁 / Async lock for the source.
        """
        self._locks.setdefault(source_name, asyncio.Lock())
        return self._locks[source_name]

    async def refresh(self, source_name: str) -> RefreshResult:
        """刷新指定订阅源，支持去重和超时保护。

        Refresh the specified source with deduplication and timeout protection.

        Args:
            source_name: 订阅源名称 / Source name.

        Returns:
            刷新结果 / Refresh result.
        """
        existing = self._inflight.get(source_name)
        if existing is not None:
            if existing.done():
                return existing.result()
            try:
                return await asyncio.wait_for(
                    asyncio.shield(existing),
                    timeout=self.refresh_lock_timeout.total_seconds(),
                )
            except TimeoutError:
                return RefreshResult(
                    False,
                    source_name,
                    error="in-flight refresh timed out; stale cache may be used if still within max_stale",
                )
        task = asyncio.create_task(self._refresh_with_lock(source_name))
        self._inflight[source_name] = task
        task.add_done_callback(lambda t, name=source_name: self._inflight.pop(name, None))
        return await task

    async def _refresh_with_lock(self, source_name: str) -> RefreshResult:
        """在获取锁后执行刷新。

        Execute refresh after acquiring the lock.

        Args:
            source_name: 订阅源名称 / Source name.

        Returns:
            刷新结果 / Refresh result.
        """
        lock = self._lock(source_name)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self.refresh_lock_timeout.total_seconds())
        except TimeoutError:
            return RefreshResult(
                False,
                source_name,
                error="refresh lock timeout; stale cache may be used if still within max_stale",
            )
        try:
            return await self._refresh_locked(source_name)
        finally:
            lock.release()

    async def _refresh_locked(self, source_name: str) -> RefreshResult:
        """持有锁时执行实际刷新逻辑。

        Execute the actual refresh logic while holding the lock.

        Args:
            source_name: 订阅源名称 / Source name.

        Returns:
            刷新结果 / Refresh result.
        """
        source = self.sources[source_name]
        now = datetime.now(UTC)
        old_cache: SourceCache | None = None
        try:
            self.cache_store.set_refreshing(source_name, True)
            old_cache = await self.cache_store.get(source_name)
            for plugin_name, ref in source.plugins.before_fetch.items():
                plugin_config = self.plugins[plugin_name]
                if self.http_plugin is None:
                    raise RuntimeError("http plugin runner is not configured")
                result = await self.http_plugin.run(PluginContext(source_name, plugin_config))
                if not result.ok and ref.on_failure == "abort":
                    raise RuntimeError(result.message or f"plugin {plugin_name} failed")

            fetched = await self.fetcher.fetch(
                source.url,
                source.fetch,
                etag=old_cache.etag if old_cache else None,
                last_modified=old_cache.last_modified if old_cache else None,
            )
            if fetched.not_modified and old_cache:
                cache = SourceCache(
                    source=source_name,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    last_attempt_at=now,
                    last_success_at=now,
                    etag=old_cache.etag,
                    last_modified=old_cache.last_modified,
                    node_count=old_cache.node_count,
                    warnings=old_cache.warnings,
                    last_error=None,
                    proxies=old_cache.proxies,
                )
                await self.cache_store.set(source_name, cache)
                return RefreshResult(True, source_name, old_cache.node_count, len(old_cache.warnings), self.cache_store.cache_path(source_name))

            parsed = parse_subscription(
                fetched.body or b"",
                source=source_name,
                fmt=source.format,
                parse_error=source.parse_error,
            )
            transformed = apply_transform(parsed.records, filter_config=source.filter, rename_config=source.rename)
            if not transformed:
                raise ParseError("no usable proxies after source transform")
            cache = SourceCache(
                source=source_name,
                schema_version=CURRENT_SCHEMA_VERSION,
                last_attempt_at=now,
                last_success_at=now,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                node_count=len(transformed),
                warnings=tuple(parsed.warnings),
                last_error=None,
                proxies=tuple(transformed),
            )
            await self.cache_store.set(source_name, cache)
            return RefreshResult(True, source_name, len(transformed), len(parsed.warnings), self.cache_store.cache_path(source_name))
        except Exception as exc:
            redacted_error = redact_secret(str(exc))
            if old_cache:
                failed = SourceCache(
                    source=source_name,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    last_attempt_at=now,
                    last_success_at=old_cache.last_success_at,
                    etag=old_cache.etag,
                    last_modified=old_cache.last_modified,
                    node_count=old_cache.node_count,
                    warnings=old_cache.warnings,
                    last_error=redacted_error,
                    proxies=old_cache.proxies,
                )
                await self.cache_store.set(source_name, failed)
            return RefreshResult(False, source_name, cache_path=self.cache_store.cache_path(source_name), error=redacted_error)
        finally:
            self.cache_store.set_refreshing(source_name, False)
