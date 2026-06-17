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

    async def refresh(self, source_name: str):
        self.calls.append(source_name)


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
