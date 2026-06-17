from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from filelock import FileLock

from .models import CacheConfig, ProxyRecord, SourceCache, SourceStatus

CURRENT_SCHEMA_VERSION = 1


class SourceCacheStore(Protocol):
    async def get(self, source_name: str) -> SourceCache | None: ...
    async def set(self, source_name: str, cache: SourceCache) -> None: ...
    async def status(self, source_name: str) -> SourceStatus: ...
    def set_refreshing(self, source_name: str, refreshing: bool) -> None: ...
    def cache_path(self, source_name: str) -> str | None: ...


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dt_s(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class JsonSourceCacheStore:
    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self.config.dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, SourceCache] = {}
        self._memory_mtime: dict[str, float] = {}
        self._refreshing: set[str] = set()

    def _path(self, source_name: str) -> Path:
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.json"

    def _lock_path(self, source_name: str) -> Path:
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.lock"

    def cache_path(self, source_name: str) -> str | None:
        return str(self._path(source_name))

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        if refreshing:
            self._refreshing.add(source_name)
        else:
            self._refreshing.discard(source_name)

    async def get(self, source_name: str) -> SourceCache | None:
        path = self._path(source_name)
        cache, mtime = await asyncio.to_thread(self._read_or_miss, path)
        if cache is None:
            self._memory.pop(source_name, None)
            self._memory_mtime.pop(source_name, None)
            return None
        assert mtime is not None
        if source_name in self._memory and self._memory_mtime.get(source_name) == mtime:
            return self._memory[source_name]
        self._memory[source_name] = cache
        self._memory_mtime[source_name] = mtime
        return cache

    async def set(self, source_name: str, cache: SourceCache) -> None:
        mtime = await asyncio.to_thread(self._write_file, source_name, cache)
        self._memory[source_name] = cache
        self._memory_mtime[source_name] = mtime

    async def status(self, source_name: str) -> SourceStatus:
        cache = await self.get(source_name)
        if cache is None:
            return SourceStatus(source_name, None, None, 0, "no cache", source_name in self._refreshing)
        return SourceStatus(
            source=source_name,
            last_attempt_at=cache.last_attempt_at,
            last_success_at=cache.last_success_at,
            node_count=cache.node_count,
            last_error=cache.last_error,
            refreshing=source_name in self._refreshing,
        )

    def _read_or_miss(self, path: Path) -> tuple[SourceCache | None, float | None]:
        if not path.exists():
            return None, None
        mtime = path.stat().st_mtime
        return self._read_file(path), mtime

    def _read_file(self, path: Path) -> SourceCache:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed cache file {path}: {exc}") from exc
        if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported cache schema version: {data.get('schema_version')}")
        try:
            proxies = tuple(ProxyRecord(item["source"], item["data"]) for item in data.get("proxies", ()))
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
        return {
            "source": cache.source,
            "schema_version": cache.schema_version,
            "last_attempt_at": _dt_s(cache.last_attempt_at),
            "last_success_at": _dt_s(cache.last_success_at),
            "etag": cache.etag,
            "last_modified": cache.last_modified,
            "node_count": cache.node_count,
            "warnings": list(cache.warnings),
            "last_error": cache.last_error,
            "proxies": [{"source": record.source, "data": record.data} for record in cache.proxies],
        }

    def _write_file(self, source_name: str, cache: SourceCache) -> float:
        path = self._path(source_name)
        tmp = path.with_suffix(".json.tmp")
        lock = FileLock(str(self._lock_path(source_name)))
        with lock:
            tmp.write_text(json.dumps(self._to_json(cache), ensure_ascii=False, indent=self.config.write_indent), encoding="utf-8")
            os.chmod(tmp, self.config.file_mode)
            os.replace(tmp, path)
            os.chmod(path, self.config.file_mode)
        return path.stat().st_mtime
