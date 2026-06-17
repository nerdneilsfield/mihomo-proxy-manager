import httpx
import pytest

from mihomo_proxy_manager.fetcher import FetchResult, SubscriptionFetcher
from mihomo_proxy_manager.models import FetchConfig, HttpConfig


@pytest.mark.asyncio
async def test_fetch_sends_conditional_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"abc"'
        assert request.headers["If-Modified-Since"] == "Wed, 17 Jun 2026 04:00:00 GMT"
        return httpx.Response(304)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))
    result = await fetcher.fetch(
        "https://93.184.216.34/sub",
        FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        etag='"abc"',
        last_modified="Wed, 17 Jun 2026 04:00:00 GMT",
    )

    assert result.not_modified is True


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1025)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))

    with pytest.raises(ValueError):
        await fetcher.fetch("https://93.184.216.34/sub", FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False))


@pytest.mark.asyncio
async def test_fetch_revalidates_redirect_target() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/sub"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))

    with pytest.raises(ValueError):
        await fetcher.fetch("https://93.184.216.34/sub", FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False))
