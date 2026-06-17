"""HTTP Action 插件和 SourceRefresher 集成测试。

HTTP Action plugin and SourceRefresher integration tests.
"""

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
from mihomo_proxy_manager.refresher import RefreshResult, SourceRefresher


@pytest.mark.asyncio
async def test_http_action_success() -> None:
    """测试 HTTP Action 插件执行成功。

    Test that the HTTP Action plugin executes successfully.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        """请求处理器 / Request handler.

        Args:
            request: HTTP 请求对象 / HTTP request object.

        Returns:
            httpx.Response: 模拟的 HTTP 响应 / Mocked HTTP response.
        """
        assert request.method == "POST"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plugin = HttpActionPlugin(
        SafeHttpClient(
            client,
            HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3),
        )
    )
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
    """测试 HTTP Action 插件在错误消息中脱敏。

    Test that the HTTP Action plugin redacts secrets in error messages.
    """

    class RaisingSafeHttp:
        """模拟抛出异常的 SafeHttpClient。

        A mock SafeHttpClient that raises an exception.
        """

        async def request(
            self, method: str, url: str, **kwargs: object
        ) -> httpx.Response:
            """模拟请求并抛出异常。

            Mock a request and raise an exception.

            Args:
                method: HTTP 方法 / HTTP method.
                url: 请求 URL / Request URL.
                **kwargs: 额外参数 / Extra arguments.

            Raises:
                ValueError: 总是抛出 / Always raised.
            """
            raise ValueError(
                "request to https://example.com/switch?token=secret failed"
            )

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
    """返回固定内容的模拟抓取器。

    A mock fetcher that returns static content.
    """

    def __init__(self, body: bytes) -> None:
        """初始化 StaticFetcher。

        Initialize StaticFetcher.

        Args:
            body: 响应体 / Response body.
        """
        self.body = body
        self.calls = 0

    async def fetch(self, *args, **kwargs):
        """模拟抓取并返回预设内容。

        Mock fetch and return preset content.

        Args:
            *args: 位置参数 / Positional args.
            **kwargs: 关键字参数 / Keyword args.

        Returns:
            FetchResult: 预设的抓取结果 / Preset fetch result.
        """
        self.calls += 1
        return FetchResult(self.body, '"etag"', "Wed, 17 Jun 2026 04:00:00 GMT")


def source_config() -> SourceConfig:
    """创建默认的源配置。

    Create a default source config.

    Returns:
        SourceConfig: 源配置对象 / Source config object.
    """
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
    """测试刷新器写入缓存。

    Test that the refresher writes to cache.
    """
    body = b"""
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
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
    """测试刷新器共享进行中的刷新任务。

    Test that the refresher shares in-flight refresh tasks.
    """
    body = b"""
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
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

    first, second = await asyncio.gather(
        refresher.refresh("airport_a"), refresher.refresh("airport_a")
    )

    assert first.ok
    assert second.ok
    assert fetcher.calls == 1


class SlowFetcher:
    """模拟慢速抓取的抓取器。

    A mock fetcher that simulates slow fetches.
    """

    async def fetch(self, *args, **kwargs):
        """模拟慢速抓取。

        Mock a slow fetch.

        Args:
            *args: 位置参数 / Positional args.
            **kwargs: 关键字参数 / Keyword args.

        Returns:
            FetchResult: 空的抓取结果 / Empty fetch result.
        """
        await asyncio.sleep(10)
        return FetchResult(b"", None, None)


@pytest.mark.asyncio
async def test_refresher_inflight_timeout_allows_stale_cache_fallback(tmp_path) -> None:
    """测试进行中刷新超时允许回退到旧缓存。

    Test that in-flight refresh timeout allows stale cache fallback.
    """
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


class RaisingCacheStore(JsonSourceCacheStore):
    """模拟 get 时抛出异常的缓存存储。

    A cache store that raises on get.
    """

    async def get(self, source_name: str):
        """模拟获取缓存并抛出异常。

        Mock cache get and raise an exception.

        Args:
            source_name: 源名称 / Source name.

        Raises:
            RuntimeError: 总是抛出 / Always raised.
        """
        raise RuntimeError("cache corrupted")


@pytest.mark.asyncio
async def test_refresher_clears_refreshing_flag_when_cache_get_fails(tmp_path) -> None:
    """测试缓存获取失败时刷新器清除 refreshing 标志。

    Test that the refresher clears the refreshing flag when cache get fails.
    """
    store = RaisingCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(b""),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("airport_a")

    assert not result.ok
    assert source_config().name not in store._refreshing


@pytest.mark.asyncio
async def test_refresher_returns_done_inflight_result_without_extra_fetch(
    tmp_path,
) -> None:
    """测试刷新器返回已完成的任务结果而不额外抓取。

    Test that the refresher returns a done in-flight result without extra fetch.
    """
    body = b"""
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
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

    async def done_refresh() -> RefreshResult:
        """执行一次刷新并返回结果。

        Perform a refresh and return the result.

        Returns:
            RefreshResult: 刷新结果 / Refresh result.
        """
        return await refresher.refresh("airport_a")

    done_task = asyncio.create_task(done_refresh())
    await asyncio.sleep(0)
    refresher._inflight["airport_a"] = done_task
    second = await refresher.refresh("airport_a")

    assert second.ok
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_refresher_caller_cancellation_keeps_inflight_task(tmp_path) -> None:
    """测试调用者取消时保留进行中的刷新任务。

    Test that caller cancellation keeps the in-flight refresh task alive.
    """
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=SlowFetcher(),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    first = asyncio.create_task(refresher.refresh("airport_a"))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(refresher.refresh("airport_a"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert "airport_a" in refresher._inflight
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
