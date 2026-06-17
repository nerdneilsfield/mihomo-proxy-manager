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


@pytest.mark.asyncio
async def test_redirect_cross_origin_strips_sensitive_and_custom_headers() -> None:
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.host == "93.184.216.34":
            return httpx.Response(
                302, headers={"Location": "https://8.8.8.8/done"}
            )
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "GET",
        "https://93.184.216.34/start",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Custom-Token": "secret",
            "User-Agent": "custom-ua",
        },
        timeout=30.0,
        allow_private_network=False,
    )

    assert response.status_code == 200
    assert requests[0]["Authorization"] == "Bearer secret"
    assert requests[0]["Cookie"] == "session=secret"
    assert requests[0]["X-Custom-Token"] == "secret"

    assert "Authorization" not in requests[1]
    assert "Cookie" not in requests[1]
    assert "X-Custom-Token" not in requests[1]
    assert requests[1]["User-Agent"] == "custom-ua"


@pytest.mark.asyncio
async def test_redirect_same_origin_preserves_custom_headers() -> None:
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "GET",
        "https://93.184.216.34/start",
        headers={"X-Custom-Token": "secret"},
        timeout=30.0,
        allow_private_network=False,
    )

    assert response.status_code == 200
    assert requests[1]["X-Custom-Token"] == "secret"


@pytest.mark.asyncio
async def test_fetch_redacts_url_on_http_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client,
        HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3),
    )

    url = "https://example.com/sub?token=secret"
    with pytest.raises(ValueError) as exc_info:
        await fetcher.fetch(
            url,
            FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        )

    message = str(exc_info.value)
    assert "token=secret" not in message
    assert "token=***" in message or "example.com" in message


@pytest.mark.asyncio
async def test_redirect_to_default_port_is_same_origin(tmp_path) -> None:
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "http://93.184.216.34:80/done"}
            )
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "GET",
        "http://93.184.216.34/start",
        headers={"X-Custom-Token": "secret"},
        timeout=30.0,
        allow_private_network=False,
    )

    assert response.status_code == 200
    assert requests[1]["X-Custom-Token"] == "secret"


@pytest.mark.asyncio
async def test_shared_client_does_not_leak_cookies() -> None:
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/a":
            return httpx.Response(200, headers={"Set-Cookie": "session=secret; Path=/"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), cookies=None)
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    await safe.request(
        "GET",
        "https://93.184.216.34/a",
        headers={},
        timeout=30.0,
        allow_private_network=False,
    )
    await safe.request(
        "GET",
        "https://8.8.8.8/b",
        headers={},
        timeout=30.0,
        allow_private_network=False,
    )

    assert "Cookie" not in requests[1]


@pytest.mark.asyncio
async def test_redirect_body_ignored_without_size_limit() -> None:
    """Redirect bodies must not be buffered or subject to max_response_size."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/done"}, content=b"x" * 2048)
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)
    response = await safe.request(
        "GET",
        "https://93.184.216.34/start",
        headers={},
        timeout=30.0,
        allow_private_network=False,
    )

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.asyncio
async def test_max_redirects_is_enforced_for_long_chains() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        step = int(request.url.path.strip("/"))
        if step < 3:
            return httpx.Response(302, headers={"Location": f"/{step + 1}"})
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 2)
    safe = SafeHttpClient(client, http_config)

    with pytest.raises(ValueError, match="too many redirects"):
        await safe.request(
            "GET",
            "https://93.184.216.34/0",
            headers={},
            timeout=30.0,
            allow_private_network=False,
        )

    assert len(requests) == 3  # initial + two allowed redirects


@pytest.mark.asyncio
async def test_endless_redirect_loop_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/loop"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    safe = SafeHttpClient(client, http_config)

    with pytest.raises(ValueError, match="too many redirects"):
        await safe.request(
            "GET",
            "https://93.184.216.34/loop",
            headers={},
            timeout=30.0,
            allow_private_network=False,
        )
