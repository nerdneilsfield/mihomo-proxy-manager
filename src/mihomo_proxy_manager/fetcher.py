from __future__ import annotations

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
    parsed = urlparse(url)
    port = parsed.port
    if port is None and parsed.scheme == "http":
        port = 80
    elif port is None and parsed.scheme == "https":
        port = 443
    return (parsed.scheme, parsed.hostname, port)


@dataclass(frozen=True)
class FetchResult:
    body: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class SafeHttpClient:
    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
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
    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
        self.safe_http = SafeHttpClient(client, http_config)

    async def fetch(
        self,
        url: str,
        fetch_config: FetchConfig,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
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
