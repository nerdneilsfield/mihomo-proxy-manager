"""JSON 缓存存储的读写、权限和并发测试。

JSON cache store read/write, permission, and concurrency tests.
"""

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.models import CacheConfig, ProxyRecord, SourceCache


@pytest.mark.asyncio
async def test_cache_roundtrip_and_permissions(tmp_path) -> None:
    """测试缓存的完整读写流程和文件权限。

    Test cache roundtrip and file permissions.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        - 读取的缓存对象与写入的一致 / The loaded cache equals the written cache.
        - 文件权限为预期的 0o600 / File permission is 0o600.
    """
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )
    cache = SourceCache(
        source="airport_a",
        schema_version=1,
        last_attempt_at=datetime(2026, 6, 17, tzinfo=UTC),
        last_success_at=datetime(2026, 6, 17, tzinfo=UTC),
        etag='"abc"',
        last_modified=None,
        node_count=1,
        warnings=("warn",),
        last_error=None,
        proxies=(ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
    )

    await store.set("airport_a", cache)
    loaded = await store.get("airport_a")

    assert loaded == cache
    assert (tmp_path / "airport_a.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_cache_json_includes_type_schema_metadata(tmp_path) -> None:
    """测试缓存 JSON 写入类型和 schema 标识 / Test cache JSON writes type and schema metadata."""
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )
    cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 17, tzinfo=UTC),
        datetime(2026, 6, 17, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
    )

    await store.set("airport_a", cache)
    data = json.loads((tmp_path / "airport_a.json").read_text(encoding="utf-8"))

    assert data["cache_type"] == "source-cache"
    assert data["schema"] == "mihomo-proxy-manager.source-cache.v1"


@pytest.mark.asyncio
async def test_cache_json_reads_legacy_v1_without_type_schema_metadata(
    tmp_path,
) -> None:
    """测试旧版 v1 缓存缺少 metadata 仍可读取 / Test legacy v1 cache without metadata still loads."""
    path = tmp_path / "airport_a.json"
    path.write_text(
        json.dumps(
            {
                "source": "airport_a",
                "schema_version": 1,
                "last_attempt_at": "2026-06-17T00:00:00+00:00",
                "last_success_at": "2026-06-17T00:00:00+00:00",
                "etag": None,
                "last_modified": None,
                "node_count": 1,
                "warnings": [],
                "last_error": None,
                "proxies": [
                    {"source": "airport_a", "data": {"name": "HK", "type": "vmess"}}
                ],
            }
        ),
        encoding="utf-8",
    )
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )

    loaded = await store.get("airport_a")

    assert loaded is not None
    assert loaded.proxies[0].data["name"] == "HK"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_unknown_schema_version_is_treated_as_miss(tmp_path, caplog) -> None:
    """测试未知 schema 版本被视为缓存未命中。

    Test that an unknown schema version is treated as a cache miss.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.
        caplog: pytest 的日志捕获 fixture / pytest log capture fixture.

    Asserts:
        get() 返回 None / get() returns None.
    """
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 99, "source": "airport_a"}', encoding="utf-8")
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_legacy_schema_version_zero_is_treated_as_miss(tmp_path) -> None:
    """测试旧版 schema 版本 0 被视为缓存未命中。

    Test that legacy schema version 0 is treated as a cache miss.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        get() 返回 None / get() returns None.
    """
    path = tmp_path / "airport_a.json"
    path.write_text(
        '{"schema_version": 0, "source": "airport_a", "proxies": []}',
        encoding="utf-8",
    )
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_cache_reloads_when_file_changes_from_another_process(tmp_path) -> None:
    """测试当文件被另一个进程修改时缓存会重新加载。

    Test that the cache reloads when the file changes from another process.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        第二次读取返回新写入的数据 / The second read returns the newly written data.
    """
    config = CacheConfig(
        tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
    )
    server_store = JsonSourceCacheStore(config)
    cli_store = JsonSourceCacheStore(config)
    old_cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 17, tzinfo=UTC),
        datetime(2026, 6, 17, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "old", "type": "vmess"}),),
    )
    new_cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 18, tzinfo=UTC),
        datetime(2026, 6, 18, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "new", "type": "vmess"}),),
    )

    await server_store.set("airport_a", old_cache)
    old_loaded = await server_store.get("airport_a")
    assert old_loaded is not None
    assert old_loaded.proxies[0].data["name"] == "old"
    await asyncio.sleep(0.01)
    await cli_store.set("airport_a", new_cache)

    new_loaded = await server_store.get("airport_a")
    assert new_loaded is not None
    assert new_loaded.proxies[0].data["name"] == "new"


@pytest.mark.asyncio
async def test_malformed_cache_json_is_treated_as_miss(tmp_path) -> None:
    """测试格式错误的 JSON 缓存被视为缓存未命中。

    Test that malformed cache JSON is treated as a cache miss.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        get() 返回 None / get() returns None.
    """
    path = tmp_path / "airport_a.json"
    path.write_text("not json", encoding="utf-8")
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_malformed_cache_missing_source_is_treated_as_miss(tmp_path) -> None:
    """测试缺少 source 字段的缓存被视为缓存未命中。

    Test that a cache missing the source field is treated as a cache miss.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        get() 返回 None / get() returns None.
    """
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    store = JsonSourceCacheStore(
        CacheConfig(
            tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
        )
    )

    assert await store.get("airport_a") is None


async def test_cache_dir_is_created_lazily(tmp_path) -> None:
    """测试缓存目录在首次写入时懒创建。

    Test that the cache directory is created lazily on first write.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        - 初始时目录不存在 / The directory does not exist initially.
        - set() 后目录被创建 / The directory is created after set().
    """
    config = CacheConfig(
        tmp_path / "lazy",
        2,
        0o600,
        max_stale=__import__("datetime").timedelta(days=7),
    )
    store = JsonSourceCacheStore(config)
    assert not config.dir.exists()

    cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 17, tzinfo=UTC),
        datetime(2026, 6, 17, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
    )
    await store.set("airport_a", cache)
    assert config.dir.exists()


def test_read_or_miss_treats_race_condition_as_miss(tmp_path) -> None:
    """测试 read_or_miss 将竞态条件视为缓存未命中。

    Test that read_or_miss treats a race condition as a cache miss.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        当文件在 stat 和 read 之间被删除时返回 (None, None) / Returns (None, None) when the file is deleted between stat and read.
    """
    from unittest.mock import MagicMock

    config = CacheConfig(
        tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
    )
    store = JsonSourceCacheStore(config)
    path = MagicMock()
    path.exists.return_value = True
    path.stat.side_effect = FileNotFoundError()

    assert store._read_or_miss("airport_a", path) == (None, None)


@pytest.mark.asyncio
async def test_get_does_not_overwrite_newer_memory_with_older_disk(tmp_path) -> None:
    """测试 get 不会用旧的磁盘数据覆盖新的内存数据。

    Test that get does not overwrite newer in-memory data with older disk data.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        内存中较新的数据被保留 / The newer in-memory data is preserved.
    """
    import time

    config = CacheConfig(
        tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
    )
    store = JsonSourceCacheStore(config)
    new_cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 18, tzinfo=UTC),
        datetime(2026, 6, 18, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "new", "type": "vmess"}),),
    )
    old_cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 17, tzinfo=UTC),
        datetime(2026, 6, 17, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "old", "type": "vmess"}),),
    )

    await store.set("airport_a", old_cache)
    # Pretend a concurrent set already updated memory to the newer cache.
    store._memory["airport_a"] = new_cache
    store._memory_mtime["airport_a"] = time.time() + 10

    loaded = await store.get("airport_a")
    assert loaded is not None
    assert loaded.proxies[0].data["name"] == "new"


@pytest.mark.asyncio
async def test_write_removes_stale_tmp_file(tmp_path) -> None:
    """测试写入时清理过期的临时文件。

    Test that writing removes stale temporary files.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        - 过期的 .tmp 文件被删除 / The stale .tmp file is removed.
        - 目标 .json 文件被创建 / The target .json file is created.
    """
    config = CacheConfig(
        tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
    )
    store = JsonSourceCacheStore(config)
    stale_tmp = tmp_path / "airport_a.json.tmp"
    stale_tmp.write_text("stale", encoding="utf-8")
    cache = SourceCache(
        "airport_a",
        1,
        datetime(2026, 6, 17, tzinfo=UTC),
        datetime(2026, 6, 17, tzinfo=UTC),
        None,
        None,
        1,
        (),
        None,
        (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
    )

    await store.set("airport_a", cache)

    assert not stale_tmp.exists()
    assert (tmp_path / "airport_a.json").exists()


@pytest.mark.asyncio
async def test_startup_cleans_stale_tmp_files(tmp_path) -> None:
    """测试启动时清理过期的临时文件。

    Test that startup cleans stale temporary files.

    Args:
        tmp_path: pytest 提供的临时目录路径 / Temporary directory path provided by pytest.

    Asserts:
        过期的 .tmp 文件在 _ensure_dir 后被删除 / The stale .tmp file is removed after _ensure_dir.
    """
    config = CacheConfig(
        tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)
    )
    stale_tmp = tmp_path / "airport_a.json.tmp"
    stale_tmp.write_text("stale", encoding="utf-8")
    # Make the tmp file look stale so the 60s writer-race grace period skips it.
    old_mtime = time.time() - 120
    os.utime(stale_tmp, (old_mtime, old_mtime))
    store = JsonSourceCacheStore(config)

    await store._ensure_dir()

    assert not stale_tmp.exists()
