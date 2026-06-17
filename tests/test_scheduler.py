import asyncio
from datetime import timedelta

import pytest

from mihomo_proxy_manager.models import (
    AppConfig,
    CacheConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    RefreshConfig,
    RenameConfig,
    FilterConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourcePluginConfig,
    FetchConfig,
)
from mihomo_proxy_manager.scheduler import RefreshScheduler


class FakeRefresher:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.done = asyncio.Event()

    async def refresh(self, source_name: str):
        self.calls.append(source_name)
        self.done.set()


def scheduler_config(tmp_path, *, startup_refresh=True, startup_refresh_mode="blocking") -> AppConfig:
    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(interval=None, cron=()),
        rename=RenameConfig(),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )
    return AppConfig(
        server=ServerConfig("127.0.0.1", 8080, "Asia/Shanghai", "/healthz", None, timedelta(seconds=1)),
        cache=CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)),
        logging_console=LoggingSinkConfig(True, "INFO", True),
        logging_file=LoggingSinkConfig(False, "DEBUG"),
        http=HttpConfig(timedelta(seconds=30), "ua", 1024, 3),
        scheduler=SchedulerConfig(startup_refresh, startup_refresh_mode, timedelta(seconds=0), timedelta(seconds=1)),
        security=SecurityConfig(128, False),
        parser=ParserConfig("auto", "skip"),
        output=OutputConfig(False, False),
        sources={"airport_a": source},
        routes={"phone": RouteConfig("phone", "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", ("airport_a",), False, RouteOutputConfig(), RenameConfig(), FilterConfig())},
        plugins={},
    )


@pytest.mark.asyncio
async def test_scheduler_blocking_startup_refreshes_sources(tmp_path) -> None:
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path), refresher)

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


@pytest.mark.asyncio
async def test_scheduler_startup_refresh_can_be_disabled(tmp_path) -> None:
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path, startup_refresh=False), refresher)

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == []


@pytest.mark.asyncio
async def test_scheduler_background_startup_refreshes_sources(tmp_path) -> None:
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path, startup_refresh_mode="background"), refresher)

    await scheduler.start()
    await asyncio.wait_for(refresher.done.wait(), timeout=1.0)
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


class BlockRefresher:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def refresh(self, source_name: str) -> None:
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_scheduler_background_startup_cancellation_is_handled(tmp_path) -> None:
    refresher = BlockRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path, startup_refresh_mode="background"), refresher)

    await scheduler.start()
    await refresher.started.wait()
    await scheduler.stop()

    # stop() must complete without propagating the CancelledError from the pending refresh.


class TimingRefresher:
    def __init__(self) -> None:
        self.timestamps: list[float] = []
        self.done = asyncio.Event()

    async def refresh(self, source_name: str) -> None:
        self.timestamps.append(asyncio.get_running_loop().time())
        if len(self.timestamps) >= 4:
            self.done.set()


@pytest.mark.asyncio
async def test_scheduler_interval_preserves_base_despite_jitter(tmp_path) -> None:
    import dataclasses

    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(interval=timedelta(seconds=0.1), cron=()),
        rename=RenameConfig(),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )
    base_config = scheduler_config(tmp_path, startup_refresh=False)
    config = dataclasses.replace(
        base_config,
        sources={"airport_a": source},
        scheduler=SchedulerConfig(
            startup_refresh=False,
            startup_refresh_mode="blocking",
            jitter=timedelta(seconds=0.05),
            refresh_lock_timeout=timedelta(seconds=1),
        ),
    )
    refresher = TimingRefresher()
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    await asyncio.wait_for(refresher.done.wait(), timeout=2.0)
    await scheduler.stop()

    intervals = [b - a for a, b in zip(refresher.timestamps[:-1], refresher.timestamps[1:])]
    # Jitter is centered around the target, so the base interval should be preserved
    # even though individual intervals vary.
    assert all(0.04 <= iv <= 0.16 for iv in intervals), intervals
    average = sum(intervals) / len(intervals)
    assert 0.08 <= average <= 0.12, average
