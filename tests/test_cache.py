import asyncio
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
async def test_unknown_schema_version_is_rejected(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 99, "source": "airport_a"}', encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    with pytest.raises(ValueError):
        await store.get("airport_a")


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
async def test_malformed_cache_json_raises_value_error(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text("not json", encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    with pytest.raises(ValueError, match="malformed cache file"):
        await store.get("airport_a")


@pytest.mark.asyncio
async def test_malformed_cache_missing_source_raises_value_error(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    with pytest.raises(ValueError, match="malformed cache file"):
        await store.get("airport_a")
