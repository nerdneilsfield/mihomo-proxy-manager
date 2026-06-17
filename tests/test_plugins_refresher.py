import asyncio
from datetime import timedelta
from typing import cast

import httpx
import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.fetcher import FetchResult, SafeHttpClient
from mihomo_proxy_manager.models import (
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    PluginConfig,
    RefreshConfig,
    RenameConfig,
    SourceConfig,
    SourcePluginConfig,
)
from mihomo_proxy_manager.plugins.http_action import HttpActionPlugin, PluginContext
from mihomo_proxy_manager.refresher import SourceRefresher


@pytest.mark.asyncio
async def test_http_action_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plugin = HttpActionPlugin(SafeHttpClient(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)))
    config = PluginConfig(
        name="turn_on",
        type="http_action",
        method="POST",
        url="https://93.184.216.34/switch",
        headers={},
        success_status=(204,),
        timeout=__import__("datetime").timedelta(seconds=10),
        allow_private_network=False,
    )

    result = await plugin.run(PluginContext(source_name="airport_a", plugin=config))

    assert result.ok


@pytest.mark.asyncio
async def test_http_action_redacts_secrets_in_error_message() -> None:
    class RaisingSafeHttp:
        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            raise ValueError("request to https://example.com/switch?token=secret failed")

    plugin = HttpActionPlugin(cast(SafeHttpClient, RaisingSafeHttp()))
    config = PluginConfig(
        name="turn_on",
        type="http_action",
        method="POST",
        url="https://example.com/switch?token=secret",
        headers={},
        success_status=(204,),
        timeout=__import__("datetime").timedelta(seconds=10),
        allow_private_network=False,
    )

    result = await plugin.run(PluginContext(source_name="airport_a", plugin=config))

    assert not result.ok
    assert result.message is not None
    assert "token=secret" not in result.message
    assert "token=***" in result.message


class StaticFetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    async def fetch(self, *args, **kwargs):
        self.calls += 1
        return FetchResult(self.body, '"etag"', "Wed, 17 Jun 2026 04:00:00 GMT")


def source_config() -> SourceConfig:
    return SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="yaml",
        parse_error="fail",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(),
        rename=RenameConfig(prefix="[{source}] "),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )


@pytest.mark.asyncio
async def test_refresher_writes_cache(tmp_path) -> None:
    body = b'''
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
'''
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(body),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("airport_a")
    cache = await store.get("airport_a")

    assert result.ok
    assert cache is not None
    assert cache.proxies[0].data["name"] == "[airport_a] HK"


@pytest.mark.asyncio
async def test_refresher_shares_inflight_refresh(tmp_path) -> None:
    body = b'''
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
'''
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    fetcher = StaticFetcher(body)
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    first, second = await asyncio.gather(refresher.refresh("airport_a"), refresher.refresh("airport_a"))

    assert first.ok
    assert second.ok
    assert fetcher.calls == 1


class SlowFetcher:
    async def fetch(self, *args, **kwargs):
        await asyncio.sleep(10)
        return FetchResult(b"", None, None)


@pytest.mark.asyncio
async def test_refresher_inflight_timeout_allows_stale_cache_fallback(tmp_path) -> None:
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=SlowFetcher(),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=0.01),
    )

    first = asyncio.create_task(refresher.refresh("airport_a"))
    await asyncio.sleep(0)
    second = await refresher.refresh("airport_a")

    assert not second.ok
    assert "stale cache" in (second.error or "").lower()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
