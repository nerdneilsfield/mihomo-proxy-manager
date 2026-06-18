"""Refresher DNS integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.fetcher import FetchResult
from mihomo_proxy_manager.models import (
    CacheConfig,
    FetchConfig,
    FilterConfig,
    ProxyRecord,
    RefreshConfig,
    RenameConfig,
    SourceCache,
    SourceConfig,
    SourceDnsConfig,
    SourcePluginConfig,
)
from mihomo_proxy_manager.refresher import SourceRefresher


class FakeFetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str | None, str | None]] = []

    async def fetch(
        self,
        url: str,
        fetch_config: FetchConfig,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        self.calls.append((etag, last_modified))
        return FetchResult(self.body, '"new"', "Fri, 19 Jun 2026 00:00:00 GMT")


class FakeResolver:
    def __init__(
        self,
        records: list[ProxyRecord],
        warnings: list[str] | None = None,
    ) -> None:
        self.records = records
        self.warnings = warnings or []
        self.calls = 0

    async def resolve_records(
        self,
        records: list[ProxyRecord],
        config: SourceDnsConfig,
        *,
        source: str,
    ) -> tuple[list[ProxyRecord], list[str]]:
        self.calls += 1
        return self.records, self.warnings


def _source_config(*, dns_enabled: bool, failure: str = "keep") -> SourceConfig:
    return SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="yaml",
        parse_error="fail",
        fetch=FetchConfig(timedelta(seconds=30), "mihomo/1.19.5", {}, False),
        refresh=RefreshConfig(interval=None, cron=()),
        rename=RenameConfig(prefix="[A] "),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
        dns=SourceDnsConfig(
            dns_enabled,
            ("udp://1.1.1.1:53",),
            timedelta(seconds=5),
            failure,  # type: ignore[arg-type]
        ),
    )


_PROXY_BODY = (
    b"proxies:\n"
    b"  - name: HK\n"
    b"    type: vmess\n"
    b"    server: example.com\n"
    b"    port: 443\n"
    b"    uuid: 00000000-0000-0000-0000-000000000000\n"
    b"    cipher: auto\n"
)


@pytest.mark.asyncio
async def test_refresher_applies_dns_after_source_transform(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    resolver = FakeResolver(
        [
            ProxyRecord(
                "airport_a",
                {
                    "name": "[A] HK",
                    "type": "vmess",
                    "server": "93.184.216.34",
                },
            )
        ],
        ["dns warning"],
    )
    refresher = SourceRefresher(
        sources={"airport_a": _source_config(dns_enabled=True)},
        plugins={},
        cache_store=cache_store,
        fetcher=FakeFetcher(_PROXY_BODY),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=resolver,
    )

    result = await refresher.refresh("airport_a")
    cache = await cache_store.get("airport_a")

    assert result.ok is True
    assert result.warning_count == 1
    assert resolver.calls == 1
    assert cache is not None
    assert cache.proxies[0].data["server"] == "93.184.216.34"
    assert cache.proxies[0].data["name"] == "[A] HK"
    assert cache.warnings == ("dns warning",)


@pytest.mark.asyncio
async def test_dns_enabled_source_skips_conditional_fetch_headers(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    now = datetime.now(UTC)
    await cache_store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            now,
            now,
            '"old"',
            "Thu, 18 Jun 2026 00:00:00 GMT",
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "old", "server": "1.1.1.1"}),),
        ),
    )
    fetcher = FakeFetcher(_PROXY_BODY)
    resolver = FakeResolver(
        [
            ProxyRecord(
                "airport_a",
                {
                    "name": "[A] HK",
                    "type": "vmess",
                    "server": "93.184.216.34",
                },
            )
        ]
    )
    refresher = SourceRefresher(
        sources={"airport_a": _source_config(dns_enabled=True)},
        plugins={},
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=resolver,
    )

    await refresher.refresh("airport_a")

    assert fetcher.calls == [(None, None)]


@pytest.mark.asyncio
async def test_dns_disabled_source_keeps_conditional_fetch_headers(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    now = datetime.now(UTC)
    await cache_store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            now,
            now,
            '"old"',
            "Thu, 18 Jun 2026 00:00:00 GMT",
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "old", "server": "1.1.1.1"}),),
        ),
    )
    fetcher = FakeFetcher(_PROXY_BODY)
    refresher = SourceRefresher(
        sources={"airport_a": _source_config(dns_enabled=False)},
        plugins={},
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=None,
    )

    await refresher.refresh("airport_a")

    assert fetcher.calls == [('"old"', "Thu, 18 Jun 2026 00:00:00 GMT")]


@pytest.mark.asyncio
async def test_refresher_dns_drop_all_records_marks_failure(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    resolver = FakeResolver([], ["dropped HK"])
    refresher = SourceRefresher(
        sources={"airport_a": _source_config(dns_enabled=True, failure="drop")},
        plugins={},
        cache_store=cache_store,
        fetcher=FakeFetcher(_PROXY_BODY),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=resolver,
    )

    result = await refresher.refresh("airport_a")

    assert result.ok is False
    assert result.error is not None
    assert "no usable proxies" in result.error.lower()


@pytest.mark.asyncio
async def test_refresher_raises_when_dns_enabled_but_resolver_missing(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    refresher = SourceRefresher(
        sources={"airport_a": _source_config(dns_enabled=True)},
        plugins={},
        cache_store=cache_store,
        fetcher=FakeFetcher(_PROXY_BODY),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=None,
    )

    result = await refresher.refresh("airport_a")

    assert result.ok is False
    assert result.error is not None
    assert "dns resolver" in result.error.lower()
