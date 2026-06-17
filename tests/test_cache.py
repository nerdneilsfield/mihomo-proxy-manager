import asyncio
import os
import time
from datetime import UTC, datetime

import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.models import CacheConfig, ProxyRecord, SourceCache


@pytest.mark.asyncio
async def test_cache_roundtrip_and_permissions(tmp_path) -> None:
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))
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
@pytest.mark.asyncio
async def test_unknown_schema_version_is_treated_as_miss(tmp_path, caplog) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 99, "source": "airport_a"}', encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_legacy_schema_version_zero_is_treated_as_miss(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text(
        '{"schema_version": 0, "source": "airport_a", "proxies": []}',
        encoding="utf-8",
    )
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_cache_reloads_when_file_changes_from_another_process(tmp_path) -> None:
    config = CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7))
    server_store = JsonSourceCacheStore(config)
    cli_store = JsonSourceCacheStore(config)
    old_cache = SourceCache(
        "airport_a", 1, datetime(2026, 6, 17, tzinfo=UTC), datetime(2026, 6, 17, tzinfo=UTC),
        None, None, 1, (), None, (ProxyRecord("airport_a", {"name": "old", "type": "vmess"}),),
    )
    new_cache = SourceCache(
        "airport_a", 1, datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 18, tzinfo=UTC),
        None, None, 1, (), None, (ProxyRecord("airport_a", {"name": "new", "type": "vmess"}),),
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
    path = tmp_path / "airport_a.json"
    path.write_text("not json", encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    assert await store.get("airport_a") is None


@pytest.mark.asyncio
async def test_malformed_cache_missing_source_is_treated_as_miss(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    assert await store.get("airport_a") is None


async def test_cache_dir_is_created_lazily(tmp_path) -> None:
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
    from unittest.mock import MagicMock

    config = CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7))
    store = JsonSourceCacheStore(config)
    path = MagicMock()
    path.exists.return_value = True
    path.stat.side_effect = FileNotFoundError()

    assert store._read_or_miss("airport_a", path) == (None, None)


@pytest.mark.asyncio
async def test_get_does_not_overwrite_newer_memory_with_older_disk(tmp_path) -> None:
    import time

    config = CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7))
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
    config = CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7))
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
    config = CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7))
    stale_tmp = tmp_path / "airport_a.json.tmp"
    stale_tmp.write_text("stale", encoding="utf-8")
    # Make the tmp file look stale so the 60s writer-race grace period skips it.
    old_mtime = time.time() - 120
    os.utime(stale_tmp, (old_mtime, old_mtime))
    store = JsonSourceCacheStore(config)

    await store._ensure_dir()

    assert not stale_tmp.exists()
