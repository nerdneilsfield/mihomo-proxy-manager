"""基于 JSON 文件的缓存存储，支持文件锁、原子写入和内存缓存。

JSON file-based cache store with file locking, atomic writes, and in-memory read-through.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from filelock import FileLock
from loguru import logger

from .models import CacheConfig, ProxyRecord, SourceCache, SourceStatus

CURRENT_CACHE_TYPE = "source-cache"
CURRENT_SCHEMA_ID = "mihomo-proxy-manager.source-cache.v1"
CURRENT_SCHEMA_VERSION = 1


class SourceCacheStore(Protocol):
    """源缓存存储的协议接口。

    Protocol interface for source cache storage.

    Args:
        source_name: 源名称 / Source name
        cache: 缓存对象 / Cache object
        refreshing: 是否正在刷新 / Whether refreshing is in progress
    """

    async def get(self, source_name: str) -> SourceCache | None:
        """获取指定源的缓存 / Get cache for the given source."""

    async def set(self, source_name: str, cache: SourceCache) -> None:
        """设置指定源的缓存 / Set cache for the given source."""

    async def status(self, source_name: str) -> SourceStatus:
        """获取指定源的缓存状态 / Get cache status for the given source."""

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        """标记或取消标记指定源正在刷新 / Mark or unmark the given source as refreshing."""

    def cache_path(self, source_name: str) -> str | None:
        """获取指定源缓存文件的路径 / Get the cache file path for the given source."""


def _dt(value: str | None) -> datetime | None:
    """将 ISO 格式字符串转换为 datetime 对象 / Convert ISO format string to datetime object.

    Args:
        value: ISO 格式的时间字符串 / ISO format time string.

    Returns:
        datetime 对象，如果输入为 None 则返回 None / datetime object, or None if input is None.
    """
    return datetime.fromisoformat(value) if value else None


def _dt_s(value: datetime | None) -> str | None:
    """将 datetime 对象转换为 ISO 格式字符串 / Convert datetime object to ISO format string.

    Args:
        value: datetime 对象 / datetime object.

    Returns:
        ISO 格式的时间字符串，如果输入为 None 则返回 None / ISO format time string, or None if input is None.
    """
    return value.isoformat() if value else None


class JsonSourceCacheStore:
    """基于 JSON 文件的缓存存储实现，支持文件锁、原子写入和内存缓存穿透。

    JSON file-based cache store implementation with file locking, atomic writes, and in-memory read-through.

    Args:
        config: 缓存配置对象 / Cache configuration object.
    """

    def __init__(self, config: CacheConfig) -> None:
        """初始化 JsonSourceCacheStore / Initialize JsonSourceCacheStore.

        Args:
            config: 缓存配置对象 / Cache configuration object.
        """
        self.config = config
        self._memory: dict[str, SourceCache] = {}
        self._memory_mtime: dict[str, float] = {}
        self._refreshing: set[str] = set()
        self._dir_created: bool = False
        # Per-source async locks so that reads/writes for different sources do
        # not serialise against each other. The file-level FileLock still
        # guards cross-process access.
        self._locks: dict[str, asyncio.Lock] = {}
        # Dedicated lock guarding the one-time directory initialisation.
        self._dir_lock = asyncio.Lock()

    def _lock_for(self, source_name: str) -> asyncio.Lock:
        """获取或创建指定源的异步锁。

        Get or create the async lock for the given source.
        """
        return self._locks.setdefault(source_name, asyncio.Lock())

    async def _ensure_dir(self) -> None:
        """确保缓存目录存在，并清理过期临时文件 / Ensure the cache directory exists and clean up stale temp files.

        Returns:
            None
        """
        if self._dir_created:
            return
        async with self._dir_lock:
            if self._dir_created:
                return
            await asyncio.to_thread(self.config.dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self._cleanup_tmp_files)
            self._dir_created = True
            logger.debug("cache dir initialized: dir={dir}", dir=str(self.config.dir))

    def _cleanup_tmp_files(self) -> None:
        """清理超过 60 秒未修改的 .json.tmp 临时文件 / Clean up .json.tmp temp files not modified for over 60 seconds.

        Returns:
            None
        """
        now = time.time()
        for tmp in self.config.dir.glob("*.json.tmp"):
            try:
                if now - tmp.stat().st_mtime < 60:
                    continue
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _path(self, source_name: str) -> Path:
        """获取指定源对应的 JSON 缓存文件路径 / Get the JSON cache file path for the given source.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            缓存文件的 Path 对象 / Path object for the cache file.
        """
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.json"

    def _lock_path(self, source_name: str) -> Path:
        """获取指定源对应的文件锁路径 / Get the file lock path for the given source.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            锁文件的 Path 对象 / Path object for the lock file.
        """
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.lock"

    def cache_path(self, source_name: str) -> str | None:
        """获取指定源缓存文件的字符串路径 / Get the cache file path as a string for the given source.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            缓存文件路径字符串 / Cache file path string.
        """
        return str(self._path(source_name))

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        """标记或取消标记指定源正在刷新 / Mark or unmark the given source as refreshing.

        Args:
            source_name: 源名称 / Source name.
            refreshing: True 表示标记为正在刷新，False 表示取消标记 / True to mark as refreshing, False to unmark.
        """
        if refreshing:
            self._refreshing.add(source_name)
        else:
            self._refreshing.discard(source_name)

    async def get(self, source_name: str) -> SourceCache | None:
        """获取指定源的缓存，优先从内存返回 / Get cache for the given source, serving from memory when possible.

        使用内存缓存穿透策略：如果磁盘文件未变更则直接返回内存缓存，否则从磁盘重新加载。
        Uses in-memory read-through: returns in-memory cache if the disk file hasn't changed,
        otherwise reloads from disk.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            缓存对象，如果缓存不存在或已损坏则返回 None / Cache object, or None if cache is missing or corrupted.
        """
        await self._ensure_dir()
        async with self._lock_for(source_name):
            path = self._path(source_name)
            try:
                cache, mtime = await asyncio.to_thread(
                    self._read_or_miss, source_name, path
                )
            except (ValueError, KeyError) as exc:
                logger.warning(
                    "corrupted cache file for source {source}: {error}; treating as miss",
                    source=source_name,
                    error=exc,
                )
                self._memory.pop(source_name, None)
                self._memory_mtime.pop(source_name, None)
                return None
            if cache is None:
                logger.debug("cache miss: source={source}", source=source_name)
                self._memory.pop(source_name, None)
                self._memory_mtime.pop(source_name, None)
                return None
            assert mtime is not None
            memory_mtime = self._memory_mtime.get(source_name)
            if memory_mtime is not None and mtime <= memory_mtime:
                logger.debug(
                    "cache hit (memory): source={source} nodes={nodes}",
                    source=source_name,
                    nodes=self._memory[source_name].node_count,
                )
                return self._memory[source_name]
            logger.debug(
                "cache hit (disk): source={source} nodes={nodes}",
                source=source_name,
                nodes=cache.node_count,
            )
            self._memory[source_name] = cache
            self._memory_mtime[source_name] = mtime
            return cache

    async def set(self, source_name: str, cache: SourceCache) -> None:
        """设置指定源的缓存，原子写入磁盘并更新内存 / Set cache for the given source, atomically write to disk and update memory.

        Args:
            source_name: 源名称 / Source name.
            cache: 要写入的缓存对象 / Cache object to write.
        """
        async with self._lock_for(source_name):
            await self._ensure_dir()
            mtime = await asyncio.to_thread(self._write_file, source_name, cache)
            self._memory[source_name] = cache
            self._memory_mtime[source_name] = mtime
            logger.debug(
                "cache written: source={source} nodes={nodes} path={path}",
                source=source_name,
                nodes=cache.node_count,
                path=str(self._path(source_name)),
            )

    async def status(self, source_name: str) -> SourceStatus:
        """获取指定源的缓存状态 / Get cache status for the given source.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            包含缓存状态信息的 SourceStatus 对象 / SourceStatus object containing cache status information.
        """
        cache = await self.get(source_name)
        if cache is None:
            return SourceStatus(
                source_name, None, None, 0, "no cache", source_name in self._refreshing
            )
        return SourceStatus(
            source=source_name,
            last_attempt_at=cache.last_attempt_at,
            last_success_at=cache.last_success_at,
            node_count=cache.node_count,
            last_error=cache.last_error,
            refreshing=source_name in self._refreshing,
        )

    def _cleanup_stale_tmp_for_source(self, source_name: str) -> None:
        """移除指定源超过 300 秒未修改的孤立 .json.tmp 临时文件 / Remove orphaned .json.tmp files for a source if they are very stale.

        Args:
            source_name: 源名称 / Source name.
        """
        tmp = self._path(source_name).with_suffix(".json.tmp")
        try:
            if tmp.exists() and time.time() - tmp.stat().st_mtime > 300:
                tmp.unlink()
        except FileNotFoundError:
            pass

    def _read_or_miss(
        self, source_name: str, path: Path
    ) -> tuple[SourceCache | None, float | None]:
        """尝试从磁盘读取缓存，如果文件不存在则返回缓存未命中 / Attempt to read cache from disk, return miss if file does not exist.

        使用文件锁保证并发安全，并在读取前清理过期临时文件。
        Uses file lock for concurrency safety and cleans up stale temp files before reading.

        Args:
            source_name: 源名称 / Source name.
            path: 缓存文件路径 / Cache file path.

        Returns:
            (缓存对象或 None, 文件修改时间或 None) / (Cache object or None, file mtime or None).
        """
        try:
            lock = FileLock(str(self._lock_path(source_name)))
            with lock:
                self._cleanup_stale_tmp_for_source(source_name)
                if not path.exists():
                    return None, None
                mtime = path.stat().st_mtime
                return self._read_file(path), mtime
        except FileNotFoundError:
            # Treat a file removed between exists()/stat()/read_text() as a cache miss.
            # 如果在 exists()/stat()/read_text() 之间文件被删除，视为缓存未命中。
            return None, None

    def _read_file(self, path: Path) -> SourceCache:
        """从 JSON 文件读取并解析缓存 / Read and parse cache from a JSON file.

        Args:
            path: 缓存文件路径 / Cache file path.

        Returns:
            解析后的 SourceCache 对象 / Parsed SourceCache object.

        Raises:
            ValueError: 文件格式错误、schema 版本不匹配或缺少必要字段 /
                        File is malformed, schema version mismatch, or required fields are missing.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed cache file {path}: {exc}") from exc
        if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported cache schema version: {data.get('schema_version')}"
            )
        if data.get("cache_type", CURRENT_CACHE_TYPE) != CURRENT_CACHE_TYPE:
            raise ValueError(f"unsupported cache type: {data.get('cache_type')}")
        if data.get("schema", CURRENT_SCHEMA_ID) != CURRENT_SCHEMA_ID:
            raise ValueError(f"unsupported cache schema: {data.get('schema')}")
        try:
            proxies = tuple(
                ProxyRecord(item["source"], item["data"])
                for item in data.get("proxies", ())
            )
            return SourceCache(
                source=data["source"],
                schema_version=data["schema_version"],
                last_attempt_at=_dt(data.get("last_attempt_at")),
                last_success_at=_dt(data.get("last_success_at")),
                etag=data.get("etag"),
                last_modified=data.get("last_modified"),
                node_count=int(data.get("node_count", len(proxies))),
                warnings=tuple(data.get("warnings", ())),
                last_error=data.get("last_error"),
                proxies=proxies,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed cache file {path}: {exc}") from exc

    def _to_json(self, cache: SourceCache) -> dict[str, Any]:
        """将 SourceCache 对象转换为可序列化的字典 / Convert SourceCache object to a serializable dictionary.

        Args:
            cache: 缓存对象 / Cache object.

        Returns:
            可用于 json.dumps 的字典 / Dictionary suitable for json.dumps.
        """
        return {
            "cache_type": CURRENT_CACHE_TYPE,
            "schema": CURRENT_SCHEMA_ID,
            "source": cache.source,
            "schema_version": cache.schema_version,
            "last_attempt_at": _dt_s(cache.last_attempt_at),
            "last_success_at": _dt_s(cache.last_success_at),
            "etag": cache.etag,
            "last_modified": cache.last_modified,
            "node_count": cache.node_count,
            "warnings": list(cache.warnings),
            "last_error": cache.last_error,
            "proxies": [
                {"source": record.source, "data": record.data}
                for record in cache.proxies
            ],
        }

    def _write_file(self, source_name: str, cache: SourceCache) -> float:
        """原子写入缓存到 JSON 文件 / Atomically write cache to a JSON file.

        先写入 .json.tmp 临时文件，然后通过 os.replace 原子替换目标文件，确保写入完整性。
        Writes to a .json.tmp temp file first, then atomically replaces the target file via os.replace.

        Args:
            source_name: 源名称 / Source name.
            cache: 要写入的缓存对象 / Cache object to write.

        Returns:
            写入后目标文件的修改时间戳 / Modification timestamp of the target file after write.
        """
        path = self._path(source_name)
        tmp = path.with_suffix(".json.tmp")
        lock = FileLock(str(self._lock_path(source_name)))
        with lock:
            tmp.unlink(missing_ok=True)
            tmp.write_text(
                json.dumps(
                    self._to_json(cache),
                    ensure_ascii=False,
                    indent=self.config.write_indent,
                ),
                encoding="utf-8",
            )
            os.chmod(tmp, self.config.file_mode)
            os.replace(tmp, path)
            os.chmod(path, self.config.file_mode)
        return path.stat().st_mtime
