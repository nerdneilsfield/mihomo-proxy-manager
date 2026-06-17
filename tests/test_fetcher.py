import httpx
import pytest

from mihomo_proxy_manager.fetcher import FetchResult, SafeHttpClient, SubscriptionFetcher
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


@pytest.mark.asyncio
async def test_redirect_302_rewrites_method_to_get_and_drops_body() -> None:
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.content))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "POST",
        "https://93.184.216.34/start",
        headers={"Content-Type": "application/json"},
        timeout=30.0,
        allow_private_network=False,
        body=b'{"x":1}',
    )

    assert response.status_code == 200
    assert requests == [("POST", b'{"x":1}'), ("GET", b"")]


@pytest.mark.asyncio
async def test_redirect_307_preserves_method_and_body() -> None:
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.content))
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "POST",
        "https://93.184.216.34/start",
        headers={"Content-Type": "application/json"},
        timeout=30.0,
        allow_private_network=False,
        body=b'{"x":1}',
    )

    assert response.status_code == 200
    assert requests == [("POST", b'{"x":1}'), ("POST", b'{"x":1}')]
