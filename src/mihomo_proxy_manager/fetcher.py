"""HTTP 订阅抓取器，支持安全重定向、Cookie 隔离和大小限制。

HTTP subscription fetcher with safe redirect handling, cookie isolation, and size limits.
"""

from __future__ import annotations

import http.cookiejar
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from .models import FetchConfig, HttpConfig
from .security import assert_safe_url, redact_secret, redact_url


# Headers considered safe to forward across origins on a redirect. Per-source
# custom headers are stripped unless they are in this allowlist.
_SAFE_CROSS_ORIGIN_HEADERS = frozenset(
    {"user-agent", "accept", "accept-language", "accept-encoding"}
)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    """解析 URL 并返回 (scheme, hostname, port) 三元组。

    Parse a URL and return the (scheme, hostname, port) triple.

    Args:
        url: 待解析的 URL 字符串 / URL string to parse.

    Returns:
        (scheme, hostname, port) 三元组。缺失的端口会根据 scheme 推断为 80 或 443。
        A tuple of (scheme, hostname, port). Missing ports are inferred as 80 or 443 based on scheme.
    """
    parsed = urlparse(url)
    port = parsed.port
    if port is None and parsed.scheme == "http":
        port = 80
    elif port is None and parsed.scheme == "https":
        port = 443
    return (parsed.scheme, parsed.hostname, port)


@dataclass(frozen=True)
class FetchResult:
    """不可变的抓取结果，包含响应体、ETag、Last-Modified 和未修改标志。

    Immutable fetch result containing response body, ETag, Last-Modified, and not-modified flag.

    Attributes:
        body: 响应体字节数据，未修改时为 None / Response body bytes, or None if not modified.
        etag: 响应 ETag 头 / Response ETag header value.
        last_modified: 响应 Last-Modified 头 / Response Last-Modified header value.
        not_modified: 服务端返回 304 未修改时为 True / True when server returned 304 Not Modified.
    """
    body: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class _NoOpCookies(http.cookiejar.CookieJar):
    """不存储也不发送任何 Cookie 的 Cookie jar。

    Cookie jar that never stores or sends cookies.

    httpx.AsyncClient 默认维护一个共享的 Cookie jar。将 ``cookies=None`` 传入仅会
    从一个空 jar 开始，但 Set-Cookie 响应仍会填充它，后续对匹配主机的请求仍会发送
    已存储的 Cookie。在创建客户端时传入此 no-op jar 可阻止客户端跨请求存储或发送
    任何 Cookie。

    httpx.AsyncClient keeps a shared cookie jar by default. Setting
    ``cookies=None`` only starts with an empty jar; Set-Cookie responses still
    populate it and the stored cookies are sent on later matching-host
    requests. Passing this no-op jar at client creation time prevents the
    client from ever storing or sending cookies across requests.
    """

    def extract_cookies(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        """忽略所有 Set-Cookie 响应头，不存储任何 Cookie。

        Ignore all Set-Cookie response headers; do not store any cookies.
        """
        return

    def add_cookie_header(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        """不为任何请求添加 Cookie 请求头。

        Do not add Cookie headers to any request.
        """
        return


class SafeHttpClient:
    """安全的 HTTP 客户端，支持重定向追踪、跨域头过滤和响应大小限制。

    Safe HTTP client with redirect following, cross-origin header filtering, and response size limits.
    """

    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
        """初始化 SafeHttpClient。

        Initialize SafeHttpClient.

        Args:
            client: httpx 异步客户端实例 / httpx async client instance.
            http_config: HTTP 配置，包含重定向和大小限制等 / HTTP configuration with redirect and size limits.
        """
        self.client = client
        self.http_config = http_config

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        allow_private_network: bool,
        body: bytes | str | None = None,
    ) -> httpx.Response:
        """执行一次安全的 HTTP 请求，自动处理重定向和跨域安全策略。

        Execute a safe HTTP request with automatic redirect handling and cross-origin security policy.

        Args:
            method: HTTP 方法（GET、POST 等）/ HTTP method (GET, POST, etc.).
            url: 请求目标 URL / Target URL for the request.
            headers: 自定义请求头 / Custom request headers.
            timeout: 请求超时时间（秒）/ Request timeout in seconds.
            allow_private_network: 是否允许访问私有网络地址 / Whether to allow private network addresses.
            body: 请求体（可选）/ Request body (optional).

        Returns:
            最终的 httpx.Response 对象 / The final httpx.Response object.

        Raises:
            ValueError: 重定向缺少 Location 头、响应超过大小限制或重定向次数过多时抛出。
                        Raised when a redirect is missing the Location header, the response exceeds
                        the size limit, or the maximum number of redirects is exceeded.
        """
        current = url
        current_method = method
        current_body = body
        current_headers = dict(headers)
        for _ in range(self.http_config.max_redirects + 1):
            assert_safe_url(current, allow_private_network=allow_private_network, resolve_dns=True)
            async with self.client.stream(
                current_method,
                current,
                headers=current_headers,
                content=current_body,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("redirect response missing Location")
                    next_url = urljoin(current, location)
                    if _origin(next_url) != _origin(current):
                        # Cross-origin redirect: strip sensitive and per-source custom headers.
                        current_headers = {
                            key: value
                            for key, value in current_headers.items()
                            if key.lower() in _SAFE_CROSS_ORIGIN_HEADERS
                        }
                    if response.status_code in {301, 302, 303}:
                        current_method = "GET"
                        current_body = None
                        current_headers = {
                            key: value
                            for key, value in current_headers.items()
                            if key.lower() not in {"content-length", "content-type", "transfer-encoding"}
                        }
                    # Do not buffer the redirect body; closing the stream releases the
                    # connection without consuming potentially large response content.
                    await response.aclose()
                    current = next_url
                    continue
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.http_config.max_response_size:
                        raise ValueError("upstream response exceeds max_response_size")
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(content),
                    request=response.request,
                )
        raise ValueError("too many redirects")


class SubscriptionFetcher:
    """订阅 URL 抓取器，封装了安全 HTTP 请求和条件请求（ETag / Last-Modified）。

    Subscription URL fetcher wrapping safe HTTP requests with conditional request support (ETag / Last-Modified).
    """

    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
        """初始化 SubscriptionFetcher。

        Initialize SubscriptionFetcher.

        Args:
            client: httpx 异步客户端实例 / httpx async client instance.
            http_config: HTTP 配置 / HTTP configuration.
        """
        self.safe_http = SafeHttpClient(client, http_config)

    async def fetch(
        self,
        url: str,
        fetch_config: FetchConfig,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        """抓取订阅 URL 并返回结果，支持条件请求以减少带宽消耗。

        Fetch a subscription URL and return the result, with conditional request support to reduce bandwidth.

        Args:
            url: 订阅 URL / Subscription URL.
            fetch_config: 抓取配置，包含超时、请求头等 / Fetch configuration with timeout, headers, etc.
            etag: 上次响应的 ETag，用于 If-None-Match 条件请求 / Previous ETag for If-None-Match conditional request.
            last_modified: 上次响应的 Last-Modified，用于 If-Modified-Since 条件请求 /
                Previous Last-Modified for If-Modified-Since conditional request.

        Returns:
            包含响应数据和未修改标志的 FetchResult / FetchResult with response data and not-modified flag.

        Raises:
            ValueError: 抓取失败时抛出，URL 和敏感信息会被脱敏处理。
                        Raised when the fetch fails; URLs and sensitive info are redacted.
        """
        headers = dict(fetch_config.headers)
        headers.setdefault("User-Agent", fetch_config.user_agent)
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        response = await self.safe_http.request(
            "GET",
            url,
            headers=headers,
            timeout=fetch_config.timeout.total_seconds(),
            allow_private_network=fetch_config.allow_private_network,
        )
        if response.status_code == 304:
            return FetchResult(None, etag, last_modified, True)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ValueError(
                f"fetch failed for {redact_url(url)}: {redact_secret(str(exc))}"
            ) from exc
        return FetchResult(response.content, response.headers.get("ETag"), response.headers.get("Last-Modified"))
