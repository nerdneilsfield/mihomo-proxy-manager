from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from mihomo_proxy_manager.app import create_app
from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.fetcher import FetchResult, SafeHttpClient
from mihomo_proxy_manager.models import (
    AppConfig,
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    PluginConfig,
    PluginRefConfig,
    ProxyRecord,
    RefreshConfig,
    RenameConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceCache,
    SourceConfig,
    SourcePluginConfig,
)
from mihomo_proxy_manager.plugins.http_action import HttpActionPlugin
from mihomo_proxy_manager.refresher import SourceRefresher


def _proxy_yaml(name: str = "HK") -> bytes:
    return f"""\
proxies:
  - name: {name}
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
""".encode()


class StaticFetcher:
    def __init__(
        self,
        body: bytes,
        *,
        etag: str | None = '"etag"',
        last_modified: str | None = "Wed, 17 Jun 2026 04:00:00 GMT",
        not_modified: bool = False,
    ) -> None:
        self.body = body
        self.etag = etag
        self.last_modified = last_modified
        self.not_modified = not_modified
        self.calls = 0

    async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
        self.calls += 1
        return FetchResult(self.body, self.etag, self.last_modified, self.not_modified)


class FailingAfterFirstFetcher:
    def __init__(self, ok_body: bytes, fail_mode: str) -> None:
        self.ok_body = ok_body
        self.fail_mode = fail_mode
        self.calls = 0

    async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
        self.calls += 1
        if self.calls == 1:
            return FetchResult(self.ok_body, '"etag"', "Wed, 17 Jun 2026 04:00:00 GMT")
        if self.fail_mode == "raise":
            raise RuntimeError("network down")
        return FetchResult(b"", None, None)


class SlowFetcher:
    def __init__(self, sleep: float, body: bytes | None = None) -> None:
        self.sleep = sleep
        self.body = body if body is not None else _proxy_yaml()
        self.calls = 0

    async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
        self.calls += 1
        await asyncio.sleep(self.sleep)
        return FetchResult(self.body, None, None)


class CountingFetcher:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.calls = 0

    async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
        async with self.lock:
            self.calls += 1
            call = self.calls
            await asyncio.sleep(0.02)
        return FetchResult(_proxy_yaml(f"Node-{call}"), None, None)


class RaisingFetcher:
    async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
        raise RuntimeError("boom")


def _http_config() -> HttpConfig:
    return HttpConfig(
        timeout=timedelta(seconds=30),
        user_agent="ua",
        max_response_size=10 * 1024 * 1024,
        max_redirects=3,
    )


def _source(
    name: str,
    *,
    plugins: SourcePluginConfig | None = None,
    interval: timedelta | None = None,
) -> SourceConfig:
    return SourceConfig(
        name=name,
        url="https://example.com/sub",
        format="yaml",
        parse_error="fail",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(interval=interval),
        rename=RenameConfig(),
        filter=FilterConfig(),
        plugins=plugins or SourcePluginConfig(),
    )


def _plugin_config(status: tuple[int, ...] = (200, 204)) -> PluginConfig:
    return PluginConfig(
        name="auth",
        type="http_action",
        method="GET",
        url="https://93.184.216.34/ping",
        headers={},
        success_status=status,
        timeout=timedelta(seconds=5),
        allow_private_network=False,
        body=None,
    )


def _http_plugin_status(status: int) -> HttpActionPlugin:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpActionPlugin(SafeHttpClient(client, _http_config()))


def _route(
    name: str,
    path: str,
    sources: tuple[str, ...],
    require_all_sources: bool = False,
) -> RouteConfig:
    return RouteConfig(
        name=name,
        path=path,
        sources=sources,
        require_all_sources=require_all_sources,
        output=RouteOutputConfig(),
        rename=RenameConfig(),
        filter=FilterConfig(),
    )


def _app_config(
    tmp_path: Path,
    *,
    sources: dict[str, SourceConfig],
    routes: dict[str, RouteConfig],
    plugins: dict[str, PluginConfig] | None = None,
    status_path: str | None = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM",
    route_refresh_wait: timedelta = timedelta(seconds=1),
) -> AppConfig:
    return AppConfig(
        server=ServerConfig(
            host="127.0.0.1",
            port=0,
            timezone="UTC",
            health_path="/healthz",
            status_path=status_path,
            route_refresh_wait=route_refresh_wait,
        ),
        cache=CacheConfig(
            dir=tmp_path / "cache",
            write_indent=2,
            file_mode=0o600,
            max_stale=timedelta(days=7),
        ),
        logging_console=LoggingSinkConfig(enabled=False, level="INFO", colorize=False),
        logging_file=LoggingSinkConfig(
            enabled=False,
            level="DEBUG",
            path=tmp_path / "logs" / "mpm.log",
        ),
        http=_http_config(),
        scheduler=SchedulerConfig(
            startup_refresh=False,
            startup_refresh_mode="background",
            jitter=timedelta(seconds=0),
            refresh_lock_timeout=timedelta(seconds=1),
        ),
        security=SecurityConfig(
            hidden_path_min_entropy_bits=128,
            allow_private_network_urls=False,
        ),
        parser=ParserConfig(default_format="yaml", default_parse_error="skip"),
        output=OutputConfig(yaml_sort_keys=False, default_include_meta_comments=False),
        sources=sources,
        routes=routes,
        plugins=plugins or {},
    )


@pytest.mark.asyncio
async def test_before_fetch_plugin_success_then_refresh_succeeds(tmp_path: Path) -> None:
    source = _source("src", plugins=SourcePluginConfig(before_fetch={"auth": PluginRefConfig("abort")}))
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    refresher = SourceRefresher(
        sources={"src": source},
        plugins={"auth": _plugin_config()},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=_http_plugin_status(204),
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("src")
    cache = await store.get("src")

    assert result.ok
    assert cache is not None
    assert cache.node_count == 1
    assert cache.proxies[0].data["name"] == "HK"


@pytest.mark.asyncio
async def test_before_fetch_plugin_abort_preserves_cache_and_records_error(tmp_path: Path) -> None:
    old = SourceCache(
        source="src",
        schema_version=1,
        last_attempt_at=datetime.now(UTC),
        last_success_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
        node_count=1,
        warnings=(),
        last_error=None,
        proxies=(ProxyRecord("src", {"name": "OLD", "type": "vmess"}),),
    )
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    await store.set("src", old)

    source = _source("src", plugins=SourcePluginConfig(before_fetch={"auth": PluginRefConfig("abort")}))
    refresher = SourceRefresher(
        sources={"src": source},
        plugins={"auth": _plugin_config()},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=_http_plugin_status(500),
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("src")
    cache = await store.get("src")

    assert not result.ok
    assert cache is not None
    assert cache.proxies == old.proxies
    assert cache.last_error is not None
    assert "unexpected status 500" in cache.last_error


@pytest.mark.asyncio
async def test_before_fetch_plugin_continue_ignores_failure_and_refreshes(tmp_path: Path) -> None:
    source = _source("src", plugins=SourcePluginConfig(before_fetch={"auth": PluginRefConfig("continue")}))
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    refresher = SourceRefresher(
        sources={"src": source},
        plugins={"auth": _plugin_config()},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=_http_plugin_status(500),
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("src")
    cache = await store.get("src")

    assert result.ok
    assert cache is not None
    assert cache.node_count == 1


@pytest.mark.asyncio
async def test_require_all_sources_503_when_any_source_missing(tmp_path: Path) -> None:
    config = _app_config(
        tmp_path,
        sources={"a": _source("a"), "b": _source("b")},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("a", "b"), require_all_sources=True)},
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "a",
        SourceCache(
            source="a",
            schema_version=1,
            last_attempt_at=datetime.now(UTC),
            last_success_at=datetime.now(UTC),
            etag=None,
            last_modified=None,
            node_count=1,
            warnings=(),
            last_error=None,
            proxies=(ProxyRecord("a", {"name": "A", "type": "vmess"}),),
        ),
    )
    refresher = SourceRefresher(
        sources={"a": _source("a"), "b": _source("b")},
        plugins={},
        cache_store=store,
        fetcher=RaisingFetcher(),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        assert client.get("/r/aaabbbccc.yaml").status_code == 503

        await store.set(
            "b",
            SourceCache(
                source="b",
                schema_version=1,
                last_attempt_at=datetime.now(UTC),
                last_success_at=datetime.now(UTC),
                etag=None,
                last_modified=None,
                node_count=1,
                warnings=(),
                last_error=None,
                proxies=(ProxyRecord("b", {"name": "B", "type": "vmess"}),),
            ),
        )
        response = client.get("/r/aaabbbccc.yaml")

    assert response.status_code == 200
    assert "proxies:" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_mode", ["raise", "empty"])
async def test_refresher_preserves_old_cache_on_download_or_parse_failure(tmp_path: Path, fail_mode: str) -> None:
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    refresher = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=FailingAfterFirstFetcher(_proxy_yaml(), fail_mode),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    first = await refresher.refresh("src")
    assert first.ok
    old = await store.get("src")
    assert old is not None

    second = await refresher.refresh("src")
    assert not second.ok
    cache = await store.get("src")

    assert cache is not None
    assert cache.proxies == old.proxies
    assert cache.last_error is not None


@pytest.mark.asyncio
async def test_refresher_rewrites_cache_timestamps_on_304_not_modified(tmp_path: Path) -> None:
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    refresher = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    await refresher.refresh("src")
    old = await store.get("src")
    assert old is not None

    refresher_304 = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(b"", etag=old.etag, last_modified=old.last_modified, not_modified=True),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    before = datetime.now(UTC)
    result = await refresher_304.refresh("src")
    after = datetime.now(UTC)
    cache = await store.get("src")

    assert result.ok
    assert cache is not None
    assert cache.etag == old.etag
    assert cache.last_modified == old.last_modified
    assert cache.proxies == old.proxies
    assert cache.last_error is None
    assert cache.last_attempt_at is not None
    assert cache.last_attempt_at >= before
    assert cache.last_attempt_at <= after
    assert cache.last_success_at is not None
    assert cache.last_success_at >= before


@pytest.mark.asyncio
@pytest.mark.parametrize("require_all_sources", [True, False])
async def test_route_refresh_wait_timeout_is_respected(tmp_path: Path, require_all_sources: bool) -> None:
    config = _app_config(
        tmp_path,
        sources={"src": _source("src")},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",), require_all_sources=require_all_sources)},
        route_refresh_wait=timedelta(seconds=0.1),
    )
    store = JsonSourceCacheStore(config.cache)
    refresher = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=SlowFetcher(5.0),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        start = time.perf_counter()
        response = client.get("/r/aaabbbccc.yaml")
        elapsed = time.perf_counter() - start

    assert response.status_code == 503
    assert elapsed >= 0.1
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_concurrent_refresher_instances_do_not_corrupt_cache(tmp_path: Path) -> None:
    cache_config = CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    store = JsonSourceCacheStore(cache_config)
    fetcher = CountingFetcher()
    refresher1 = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    refresher2 = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    await asyncio.gather(refresher1.refresh("src"), refresher2.refresh("src"))

    cache = await store.get("src")
    assert cache is not None
    assert cache.node_count == 1

    cache_path = store.cache_path("src")
    assert cache_path is not None
    path = Path(cache_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["proxies"]) == 1
    assert data["proxies"][0]["data"]["type"] == "vmess"


@pytest.mark.asyncio
async def test_inflight_dedup_shares_single_fetch(tmp_path: Path) -> None:
    store = JsonSourceCacheStore(
        CacheConfig(tmp_path, 2, 0o600, max_stale=timedelta(days=7))
    )
    fetcher = StaticFetcher(_proxy_yaml())
    refresher = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    first, second = await asyncio.gather(
        refresher.refresh("src"), refresher.refresh("src")
    )

    assert first.ok
    assert second.ok
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_disabled_status_path_returns_404(tmp_path: Path) -> None:
    config = _app_config(
        tmp_path,
        sources={"src": _source("src")},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",))},
        status_path=None,
    )
    app = create_app(
        config, cache_store=JsonSourceCacheStore(config.cache), refresher=None, scheduler=None
    )

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM").status_code == 404


@pytest.mark.asyncio
async def test_app_with_real_refresher_serves_fresh_yaml(tmp_path: Path) -> None:
    config = _app_config(
        tmp_path,
        sources={"src": _source("src")},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",))},
    )
    store = JsonSourceCacheStore(config.cache)
    refresher = SourceRefresher(
        sources={"src": _source("src")},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/r/aaabbbccc.yaml")

    assert response.status_code == 200
    assert "proxies:" in response.text


@pytest.mark.asyncio
async def test_app_real_refresher_plugin_abort_returns_503(tmp_path: Path) -> None:
    source = _source("src", plugins=SourcePluginConfig(before_fetch={"auth": PluginRefConfig("abort")}))
    config = _app_config(
        tmp_path,
        sources={"src": source},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",))},
        plugins={"auth": _plugin_config()},
    )
    store = JsonSourceCacheStore(config.cache)
    refresher = SourceRefresher(
        sources={"src": source},
        plugins={"auth": _plugin_config()},
        cache_store=store,
        fetcher=StaticFetcher(_proxy_yaml()),
        http_plugin=_http_plugin_status(500),
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/r/aaabbbccc.yaml")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_app_real_refresher_serves_stale_cache_when_background_refresh_fails(tmp_path: Path) -> None:
    source = _source("src", interval=timedelta(minutes=1))
    config = _app_config(
        tmp_path,
        sources={"src": source},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",))},
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "src",
        SourceCache(
            source="src",
            schema_version=1,
            last_attempt_at=datetime.now(UTC) - timedelta(minutes=5),
            last_success_at=datetime.now(UTC),
            etag=None,
            last_modified=None,
            node_count=1,
            warnings=(),
            last_error=None,
            proxies=(ProxyRecord("src", {"name": "STALE", "type": "vmess"}),),
        ),
    )

    class RaisingFetcher:
        async def fetch(self, *args: object, **kwargs: object) -> FetchResult:
            raise RuntimeError("boom")

    refresher = SourceRefresher(
        sources={"src": source},
        plugins={},
        cache_store=store,
        fetcher=RaisingFetcher(),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/r/aaabbbccc.yaml")

    assert response.status_code == 200
    assert "STALE" in response.text


@pytest.mark.asyncio
async def test_app_real_refresher_rewrites_cache_on_304_and_serves_stale(tmp_path: Path) -> None:
    old_time = datetime.now(UTC) - timedelta(minutes=5)
    source = _source("src", interval=timedelta(minutes=1))
    config = _app_config(
        tmp_path,
        sources={"src": source},
        routes={"r": _route("r", "/r/aaabbbccc.yaml", ("src",))},
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "src",
        SourceCache(
            source="src",
            schema_version=1,
            last_attempt_at=old_time,
            last_success_at=old_time,
            etag='"etag"',
            last_modified="Wed, 17 Jun 2026 04:00:00 GMT",
            node_count=1,
            warnings=(),
            last_error=None,
            proxies=(ProxyRecord("src", {"name": "CACHED", "type": "vmess"}),),
        ),
    )

    refresher = SourceRefresher(
        sources={"src": source},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(b"", etag='"etag"', last_modified="Wed, 17 Jun 2026 04:00:00 GMT", not_modified=True),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/r/aaabbbccc.yaml")

    assert response.status_code == 200
    assert "CACHED" in response.text
    cache = await store.get("src")
    assert cache is not None
    assert cache.last_error is None
    assert cache.last_success_at is not None
    assert cache.last_success_at >= old_time
    assert cache.last_attempt_at is not None
    assert cache.last_attempt_at >= old_time
