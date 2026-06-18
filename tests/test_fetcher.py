"""HTTP 抓取器安全重定向、Cookie 隔离和大小限制测试。

HTTP fetcher safe redirect, cookie isolation, and size limit tests.
"""

import httpx2 as httpx
import pytest

from mihomo_proxy_manager.fetcher import (
    SafeHttpClient,
    SubscriptionFetcher,
    _NoOpCookies,
)
from mihomo_proxy_manager.models import FetchConfig, HttpConfig


@pytest.mark.asyncio
async def test_fetch_sends_conditional_headers() -> None:
    """测试抓取时发送条件请求头 / Test that fetch sends conditional headers.

    Args:
        None.

    Returns:
        None. 断言 not_modified 为 True / Asserts not_modified is True.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"abc"'
        assert request.headers["If-Modified-Since"] == "Wed, 17 Jun 2026 04:00:00 GMT"
        return httpx.Response(304)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    )
    result = await fetcher.fetch(
        "https://93.184.216.34/sub",
        FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        etag='"abc"',
        last_modified="Wed, 17 Jun 2026 04:00:00 GMT",
    )

    assert result.not_modified is True


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_response() -> None:
    """测试抓取拒绝超过大小限制的响应 / Test that fetch rejects oversized responses.

    Args:
        None.

    Returns:
        None. 断言抛出 ValueError / Asserts ValueError is raised.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1025)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    )

    with pytest.raises(ValueError):
        await fetcher.fetch(
            "https://93.184.216.34/sub",
            FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        )


@pytest.mark.asyncio
async def test_fetch_requests_identity_encoding_by_default() -> None:
    """测试抓取默认请求 identity 编码，避免上游返回错误压缩格式。

    Test that fetch requests identity encoding by default to avoid broken
    upstream compression metadata.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, content=b"proxies: []")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    )

    result = await fetcher.fetch(
        "https://93.184.216.34/sub",
        FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
    )

    assert result.body == b"proxies: []"


@pytest.mark.asyncio
async def test_fetch_tolerates_broken_content_encoding_header() -> None:
    """测试错误 Content-Encoding 不会导致订阅抓取失败。

    Test that an incorrect Content-Encoding header does not fail subscription
    fetches before parsing can inspect the raw body.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(b"proxies:\n  - name: HK\n    type: ss\n"),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    )

    result = await fetcher.fetch(
        "https://93.184.216.34/sub",
        FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
    )

    assert result.body == b"proxies:\n  - name: HK\n    type: ss\n"


@pytest.mark.asyncio
async def test_fetch_revalidates_redirect_target() -> None:
    """测试抓取拒绝重定向到私有网络地址 / Test that fetch rejects redirects to private network addresses.

    Args:
        None.

    Returns:
        None. 断言抛出 ValueError / Asserts ValueError is raised.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/sub"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(
        client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)
    )

    with pytest.raises(ValueError):
        await fetcher.fetch(
            "https://93.184.216.34/sub",
            FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        )


@pytest.mark.asyncio
async def test_redirect_302_rewrites_method_to_get_and_drops_body() -> None:
    """测试 302 重定向将方法改为 GET 并丢弃请求体 / Test that 302 redirect rewrites method to GET and drops the body.

    Args:
        None.

    Returns:
        None. 断言最终请求为 GET 且 body 为空 / Asserts final request is GET with empty body.
    """
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.content))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试 307 重定向保留方法和请求体 / Test that 307 redirect preserves method and body.

    Args:
        None.

    Returns:
        None. 断言两次请求均为 POST 且 body 相同 / Asserts both requests are POST with same body.
    """
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.content))
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试跨域重定向时移除敏感和自定义请求头 / Test that cross-origin redirect strips sensitive and custom headers.

    Args:
        None.

    Returns:
        None. 断言跨域请求中 Authorization、Cookie 和 X-Custom-Token 被移除，User-Agent 保留 / Asserts Authorization, Cookie, X-Custom-Token are stripped on cross-origin redirect; User-Agent preserved.
    """
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": "https://8.8.8.8/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试同源重定向保留自定义请求头 / Test that same-origin redirect preserves custom headers.

    Args:
        None.

    Returns:
        None. 断言同源重定向后 X-Custom-Token 仍存在 / Asserts X-Custom-Token is preserved after same-origin redirect.
    """
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/done"})
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试抓取在 HTTP 错误时对 URL 进行脱敏 / Test that fetch redacts the URL on HTTP error.

    Args:
        None.

    Returns:
        None. 断言异常消息中不包含原始 token / Asserts exception message does not contain the raw token.
    """

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
    """测试重定向到默认端口被视为同源 / Test that redirect to default port is treated as same origin.

    Args:
        tmp_path: pytest 提供的临时目录 / Temporary directory provided by pytest.

    Returns:
        None. 断言重定向后自定义请求头仍保留 / Asserts custom headers are preserved after redirect.
    """
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "http://93.184.216.34:80/done"}
            )
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试共享客户端不会泄露 Cookie / Test that the shared client does not leak cookies.

    Args:
        None.

    Returns:
        None. 断言第二个请求不携带 Cookie / Asserts the second request does not carry cookies.
    """
    requests: list[httpx.Headers] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers)
        if request.url.path == "/a":
            return httpx.Response(
                200, headers={"Set-Cookie": "session=TOKEN-FROM-A; Path=/"}
            )
        return httpx.Response(200)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), cookies=_NoOpCookies()
    )
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
    safe = SafeHttpClient(client, http_config)
    await safe.request(
        "GET",
        "https://93.184.216.34/a",
        headers={},
        timeout=30.0,
        allow_private_network=False,
    )
    # Same host, different path: the shared client cookie jar would send the
    # cookie set by source A on the request to source B.
    await safe.request(
        "GET",
        "https://93.184.216.34/b",
        headers={},
        timeout=30.0,
        allow_private_network=False,
    )

    assert "Cookie" not in requests[1]


@pytest.mark.asyncio
async def test_redirect_body_ignored_without_size_limit() -> None:
    """测试重定向响应体不受大小限制影响 / Test that redirect bodies are not subject to size limits.

    Args:
        None.

    Returns:
        None. 断言最终响应为 200 且内容为 b"ok" / Asserts final response is 200 with content b"ok".
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"Location": "/done"}, content=b"x" * 2048
            )
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
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
    """测试最大重定向次数被强制执行 / Test that max redirects is enforced for long chains.

    Args:
        None.

    Returns:
        None. 断言抛出 ValueError 且实际请求次数为 3 / Asserts ValueError is raised and only 3 requests were made.
    """
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        step = int(request.url.path.strip("/"))
        if step < 3:
            return httpx.Response(302, headers={"Location": f"/{step + 1}"})
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 2
    )
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
    """测试无限重定向循环被拒绝 / Test that an endless redirect loop is rejected.

    Args:
        None.

    Returns:
        None. 断言抛出 ValueError / Asserts ValueError is raised.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/loop"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_config = HttpConfig(
        __import__("datetime").timedelta(seconds=30), "ua", 1024, 3
    )
    safe = SafeHttpClient(client, http_config)

    with pytest.raises(ValueError, match="too many redirects"):
        await safe.request(
            "GET",
            "https://93.184.216.34/loop",
            headers={},
            timeout=30.0,
            allow_private_network=False,
        )
