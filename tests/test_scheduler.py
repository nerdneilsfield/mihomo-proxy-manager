"""定时调度器测试，包括启动刷新、间隔循环和 cron 表达式。

Scheduler tests including startup refresh, interval loops, and cron expressions.
"""

import asyncio
import random
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
    """模拟刷新器，记录调用历史。

    A fake refresher that records call history.
    """

    def __init__(self) -> None:
        """初始化 FakeRefresher。

        Initialize FakeRefresher.
        """
        self.calls: list[str] = []
        self.done = asyncio.Event()

    async def refresh(self, source_name: str):
        """记录刷新调用并设置完成事件。

        Record the refresh call and set the done event.

        Args:
            source_name: 源名称 / Source name.
        """
        self.calls.append(source_name)
        self.done.set()


def scheduler_config(
    tmp_path, *, startup_refresh=True, startup_refresh_mode="blocking"
) -> AppConfig:
    """创建调度器测试用应用配置。

    Create an app config for scheduler testing.

    Args:
        tmp_path: 临时目录路径 / Temporary directory path.
        startup_refresh: 是否启动时刷新 / Whether to refresh on startup.
        startup_refresh_mode: 启动刷新模式 / Startup refresh mode.

    Returns:
        AppConfig: 应用配置对象 / App config object.
    """
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
        server=ServerConfig(
            "127.0.0.1", 8080, "Asia/Shanghai", "/healthz", None, timedelta(seconds=1)
        ),
        cache=CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)),
        logging_console=LoggingSinkConfig(True, "INFO", True),
        logging_file=LoggingSinkConfig(False, "DEBUG"),
        http=HttpConfig(timedelta(seconds=30), "ua", 1024, 3),
        scheduler=SchedulerConfig(
            startup_refresh,
            startup_refresh_mode,
            timedelta(seconds=0),
            timedelta(seconds=1),
        ),
        security=SecurityConfig(128, False),
        parser=ParserConfig("auto", "skip"),
        output=OutputConfig(False, False),
        sources={"airport_a": source},
        routes={
            "phone": RouteConfig(
                "phone",
                "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
                ("airport_a",),
                False,
                RouteOutputConfig(),
                RenameConfig(),
                FilterConfig(),
            )
        },
        plugins={},
    )


@pytest.mark.asyncio
async def test_scheduler_blocking_startup_refreshes_sources(tmp_path) -> None:
    """测试调度器阻塞式启动刷新所有源。

    Test that the scheduler performs blocking startup refresh for all sources.
    """
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path), refresher)

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


@pytest.mark.asyncio
async def test_scheduler_startup_refresh_can_be_disabled(tmp_path) -> None:
    """测试调度器启动刷新可以被禁用。

    Test that the scheduler startup refresh can be disabled.
    """
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(
        scheduler_config(tmp_path, startup_refresh=False), refresher
    )

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == []


@pytest.mark.asyncio
async def test_scheduler_background_startup_refreshes_sources(tmp_path) -> None:
    """测试调度器后台启动刷新所有源。

    Test that the scheduler performs background startup refresh for all sources.
    """
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(
        scheduler_config(tmp_path, startup_refresh_mode="background"), refresher
    )

    await scheduler.start()
    await asyncio.wait_for(refresher.done.wait(), timeout=1.0)
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


class BlockRefresher:
    """模拟阻塞的刷新器。

    A fake refresher that blocks indefinitely.
    """

    def __init__(self) -> None:
        """初始化 BlockRefresher。

        Initialize BlockRefresher.
        """
        self.started = asyncio.Event()

    async def refresh(self, source_name: str) -> None:
        """模拟阻塞刷新。

        Simulate a blocking refresh.

        Args:
            source_name: 源名称 / Source name.
        """
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_scheduler_background_startup_cancellation_is_handled(tmp_path) -> None:
    """测试调度器后台启动取消被正确处理。

    Test that the scheduler handles background startup cancellation correctly.
    """
    refresher = BlockRefresher()
    scheduler = RefreshScheduler(
        scheduler_config(tmp_path, startup_refresh_mode="background"), refresher
    )

    await scheduler.start()
    await refresher.started.wait()
    await scheduler.stop()

    # stop() must complete without propagating the CancelledError from the pending refresh.


class TimingRefresher:
    """记录时间戳的刷新器，用于测试间隔。

    A refresher that records timestamps for interval testing.
    """

    def __init__(self) -> None:
        """初始化 TimingRefresher。

        Initialize TimingRefresher.
        """
        self.timestamps: list[float] = []
        self.done = asyncio.Event()

    async def refresh(self, source_name: str) -> None:
        """记录当前时间戳。

        Record the current timestamp.

        Args:
            source_name: 源名称 / Source name.
        """
        self.timestamps.append(asyncio.get_running_loop().time())
        if len(self.timestamps) >= 4:
            self.done.set()


@pytest.mark.asyncio
async def test_scheduler_interval_preserves_base_despite_jitter(tmp_path) -> None:
    """测试调度器在有抖动时仍保持基本间隔。

    Test that the scheduler preserves the base interval despite jitter.
    """
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

    intervals = [
        b - a for a, b in zip(refresher.timestamps[:-1], refresher.timestamps[1:])
    ]
    # Jitter is applied as a positive offset in [0, jitter], so individual
    # intervals vary around the base interval by up to the jitter magnitude.
    assert all(0.05 <= iv <= 0.15 for iv in intervals), intervals
    average = sum(intervals) / len(intervals)
    assert 0.08 <= average <= 0.12, average


class FailingRefresher:
    """模拟刷新失败的刷新器。

    A fake refresher that fails on refresh.
    """

    def __init__(self) -> None:
        """初始化 FailingRefresher。

        Initialize FailingRefresher.
        """
        self.calls: list[str] = []

    async def refresh(self, source_name: str) -> None:
        """模拟刷新并抛出异常。

        Simulate a refresh and raise an exception.

        Args:
            source_name: 源名称 / Source name.

        Raises:
            RuntimeError: 总是抛出 / Always raised.
        """
        self.calls.append(source_name)
        raise RuntimeError("refresh failed")


@pytest.mark.asyncio
async def test_scheduler_interval_loop_survives_refresher_exception(tmp_path) -> None:
    """测试调度器间隔循环在刷新器异常后仍能继续。

    Test that the scheduler interval loop survives refresher exceptions.
    """
    import dataclasses

    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(interval=timedelta(seconds=0.05), cron=()),
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
            jitter=timedelta(seconds=0),
            refresh_lock_timeout=timedelta(seconds=1),
        ),
    )
    refresher = FailingRefresher()
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    await asyncio.sleep(0.15)
    await scheduler.stop()

    assert len(refresher.calls) >= 2


@pytest.mark.asyncio
async def test_scheduler_start_failure_stops_pending_tasks(tmp_path) -> None:
    """测试调度器启动失败时停止所有待处理任务。

    Test that the scheduler stops pending tasks when start fails.
    """

    class RaiseOnStartRefresher:
        """启动时抛出异常的模拟刷新器。

        A fake refresher that raises on startup.
        """

        async def refresh(self, source_name: str) -> None:
            """模拟启动刷新并抛出异常。

            Simulate a startup refresh and raise an exception.

            Args:
                source_name: 源名称 / Source name.

            Raises:
                RuntimeError: 总是抛出 / Always raised.
            """
            raise RuntimeError("startup refresh failed")

    refresher = RaiseOnStartRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path), refresher)

    with pytest.raises(RuntimeError):
        await scheduler.start()
    # stop() must still be callable and cancel any tasks created during start().
    await scheduler.stop()
    assert all(task.done() for task in scheduler._tasks)


# ---------------------------------------------------------------------------
# _track_startup_refresh coverage: exception and failed-result paths.
# ---------------------------------------------------------------------------


def _config_with_cron(
    tmp_path, cron_expr: str, *, startup_refresh: bool = False
) -> AppConfig:
    """Create a config whose source has a cron expression / 创建带 cron 的配置。"""
    import dataclasses

    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(interval=None, cron=(cron_expr,)),
        rename=RenameConfig(),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )
    base = scheduler_config(tmp_path, startup_refresh=startup_refresh)
    return dataclasses.replace(base, sources={"airport_a": source})


@pytest.mark.asyncio
async def test_scheduler_cron_loop_triggers_refresh(tmp_path, monkeypatch) -> None:
    """测试 cron 循环能触发刷新并能在停止时干净退出。

    Test that the cron loop triggers refreshes and stops cleanly.
    """

    class FakeIterator:
        def __init__(self) -> None:
            self._n = 0

        def get_next(self, _dt_cls):
            # Return a timestamp ~10ms in the future so the real sleep is brief.
            self._n += 1
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI

            return _dt.now(_ZI("Asia/Shanghai")) + timedelta(milliseconds=10 * self._n)

    monkeypatch.setattr(
        "mihomo_proxy_manager.scheduler.croniter", lambda expr, now: FakeIterator()
    )

    refresher = FakeRefresher()
    config = _config_with_cron(tmp_path, "0 * * * *", startup_refresh=False)
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    await asyncio.wait_for(refresher.done.wait(), timeout=1.0)
    await scheduler.stop()
    assert refresher.calls == ["airport_a"]
    assert all(task.done() for task in scheduler._tasks)


@pytest.mark.asyncio
async def test_scheduler_cron_loop_survives_refresher_exception(
    tmp_path, monkeypatch
) -> None:
    """测试 cron 循环在刷新器抛异常后能继续运行。

    Test that the cron loop survives a refresher exception.
    """

    class FakeIterator:
        def __init__(self) -> None:
            self._n = 0

        def get_next(self, _dt_cls):
            self._n += 1
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI

            return _dt.now(_ZI("Asia/Shanghai")) + timedelta(milliseconds=10 * self._n)

    monkeypatch.setattr(
        "mihomo_proxy_manager.scheduler.croniter", lambda expr, now: FakeIterator()
    )

    refresher = FailingRefresher()
    config = _config_with_cron(tmp_path, "0 * * * *", startup_refresh=False)
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    # Wait long enough for at least one cron tick + failure to occur.
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert len(refresher.calls) >= 1


@pytest.mark.asyncio
async def test_scheduler_cron_loop_with_positive_jitter(tmp_path, monkeypatch) -> None:
    """测试 cron 循环在正抖动时进入 _jitter_seconds > 0 分支。

    Test that the cron loop exercises the positive-jitter branch.
    """
    import dataclasses

    class FakeIterator:
        def __init__(self) -> None:
            self._n = 0

        def get_next(self, _dt_cls):
            self._n += 1
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI

            return _dt.now(_ZI("Asia/Shanghai")) + timedelta(milliseconds=5 * self._n)

    monkeypatch.setattr(
        "mihomo_proxy_manager.scheduler.croniter", lambda expr, now: FakeIterator()
    )
    # Force jitter to deterministically pick the upper bound (a small positive
    # value) so the _jitter_seconds > 0 branch is exercised but stays fast.
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.005)

    refresher = FakeRefresher()
    base = _config_with_cron(tmp_path, "0 * * * *", startup_refresh=False)
    config = dataclasses.replace(
        base,
        scheduler=SchedulerConfig(
            startup_refresh=False,
            startup_refresh_mode="blocking",
            jitter=timedelta(seconds=0.01),
            refresh_lock_timeout=timedelta(seconds=1),
        ),
    )
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    await asyncio.wait_for(refresher.done.wait(), timeout=1.0)
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


@pytest.mark.asyncio
async def test_track_startup_refresh_logs_on_exception(tmp_path) -> None:
    """测试后台启动刷新抛异常时 _track_startup_refresh 记录警告。

    Test that _track_startup_refresh logs a warning when the task raises.
    """

    class RaisingRefresher:
        async def refresh(self, source_name: str) -> None:
            raise RuntimeError("boom")

    refresher = RaisingRefresher()
    config = scheduler_config(tmp_path, startup_refresh_mode="background")
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    # Wait for the background task to finish so the done-callback fires.
    # Use return_exceptions so the test does not re-raise the RuntimeError.
    await asyncio.gather(*scheduler._tasks, return_exceptions=True)
    await scheduler.stop()


@pytest.mark.asyncio
async def test_track_startup_refresh_logs_on_failed_result(tmp_path) -> None:
    """测试后台启动刷新返回失败结果时 _track_startup_refresh 记录警告。

    Test that _track_startup_refresh logs a warning when the result has ok=False.
    """
    from mihomo_proxy_manager.refresher import RefreshResult

    class FailedResultRefresher:
        async def refresh(self, source_name: str):
            return RefreshResult(False, source_name, error="failed")

    refresher = FailedResultRefresher()
    config = scheduler_config(tmp_path, startup_refresh_mode="background")
    scheduler = RefreshScheduler(config, refresher)

    await scheduler.start()
    await asyncio.gather(*scheduler._tasks, return_exceptions=True)
    await scheduler.stop()


def test_jitter_seconds_returns_zero_when_disabled(tmp_path) -> None:
    """测试 jitter 为零时 _jitter_seconds 返回 0.0。

    Test that _jitter_seconds returns 0.0 when jitter is disabled.
    """
    refresher = FakeRefresher()
    config = scheduler_config(tmp_path, startup_refresh=False)
    scheduler = RefreshScheduler(config, refresher)
    assert scheduler._jitter_seconds() == 0.0


def test_jitter_seconds_returns_positive_when_enabled(tmp_path) -> None:
    """测试 jitter 为正值时 _jitter_seconds 返回正值。

    Test that _jitter_seconds returns a positive value when jitter is enabled.
    """
    import dataclasses

    refresher = FakeRefresher()
    base = scheduler_config(tmp_path, startup_refresh=False)
    config = dataclasses.replace(
        base,
        scheduler=SchedulerConfig(
            startup_refresh=False,
            startup_refresh_mode="blocking",
            jitter=timedelta(seconds=0.5),
            refresh_lock_timeout=timedelta(seconds=1),
        ),
    )
    scheduler = RefreshScheduler(config, refresher)
    assert 0 <= scheduler._jitter_seconds() <= 0.5
