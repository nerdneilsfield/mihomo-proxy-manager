"""定时刷新调度器，支持固定间隔和 cron 表达式，带随机抖动。

Refresh scheduler supporting fixed intervals and cron expressions with jitter.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from loguru import logger

from .models import AppConfig


class RefreshScheduler:
    """定时刷新调度器，管理所有订阅源的定时刷新任务。

    Refresh scheduler that manages scheduled refresh tasks for all sources.
    """

    def __init__(self, config: AppConfig, refresher) -> None:
        """初始化 RefreshScheduler。

        Initialize RefreshScheduler.

        Args:
            config: 应用配置 / Application configuration.
            refresher: SourceRefresher 实例 / SourceRefresher instance.
        """
        self.config = config
        self.refresher = refresher
        self._tasks: list[asyncio.Task[Any]] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """启动调度器，执行启动刷新并注册定时任务。

        Start the scheduler, perform startup refresh and register scheduled tasks.
        """
        source_count = len(self.config.sources)
        if self.config.scheduler.startup_refresh:
            mode = self.config.scheduler.startup_refresh_mode
            logger.info(
                "scheduler start: startup_refresh={mode} sources={sources}",
                mode=mode,
                sources=source_count,
            )
            if mode == "blocking":
                await asyncio.gather(
                    *(self.refresher.refresh(name) for name in self.config.sources)
                )
            else:
                for name in self.config.sources:
                    task = asyncio.create_task(self.refresher.refresh(name))
                    task.add_done_callback(
                        lambda item, name=name: self._track_startup_refresh(item, name)
                    )
                    self._tasks.append(task)
        else:
            logger.info(
                "scheduler start: startup_refresh=skipped sources={sources}",
                sources=source_count,
            )
        for name, source in self.config.sources.items():
            if source.refresh.interval:
                interval_s = source.refresh.interval.total_seconds()
                self._tasks.append(
                    asyncio.create_task(self._interval_loop(name, interval_s))
                )
                logger.debug(
                    "scheduler registered interval: source={source} interval={interval}s",
                    source=name,
                    interval=interval_s,
                )
            for expr in source.refresh.cron:
                self._tasks.append(asyncio.create_task(self._cron_loop(name, expr)))
                logger.debug(
                    "scheduler registered cron: source={source} expr={expr}",
                    source=name,
                    expr=expr,
                )

    async def stop(self) -> None:
        """停止调度器，取消所有运行中的任务。

        Stop the scheduler and cancel all running tasks.
        """
        logger.info("scheduler stop: cancelling {tasks} tasks", tasks=len(self._tasks))
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("scheduler stopped")

    def _track_startup_refresh(self, task: asyncio.Task[Any], source_name: str) -> None:
        """跟踪启动刷新任务的结果并记录警告。

        Track startup refresh task results and log warnings.

        Args:
            task: 异步任务 / Async task.
            source_name: 订阅源名称 / Source name.
        """
        try:
            result = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "startup refresh failed for source {source}: {error}",
                source=source_name,
                error=exc,
            )
            return
        if result is not None and not getattr(result, "ok", True):
            error = getattr(result, "error", None) or "unknown error"
            logger.warning(
                "startup refresh failed for source {source}: {error}",
                source=source_name,
                error=error,
            )

    def _jitter_seconds(self) -> float:
        """计算随机抖动的秒数。

        Calculate random jitter seconds.

        Returns:
            抖动秒数 / Jitter seconds.
        """
        seconds = self.config.scheduler.jitter.total_seconds()
        if seconds > 0:
            return random.uniform(0, seconds)
        return 0.0

    async def _interval_loop(self, source_name: str, interval_seconds: float) -> None:
        """固定间隔刷新循环。

        Fixed-interval refresh loop.

        Args:
            source_name: 订阅源名称 / Source name.
            interval_seconds: 间隔秒数 / Interval in seconds.
        """
        loop = asyncio.get_running_loop()
        next_target = loop.time() + interval_seconds
        jitter_seconds = self.config.scheduler.jitter.total_seconds()
        while not self._stopping.is_set():
            jitter = random.uniform(0, jitter_seconds) if jitter_seconds > 0 else 0.0
            delay = max(0.0, next_target + jitter - loop.time())
            logger.debug(
                "interval loop: source={source} delay={delay:.1f}s jitter={jitter:.1f}s",
                source=source_name,
                delay=delay,
                jitter=jitter,
            )
            await asyncio.sleep(delay)
            if self._stopping.is_set():
                return
            try:
                await self.refresher.refresh(source_name)
            except Exception as exc:
                logger.warning(
                    "scheduled refresh failed for source {source}: {error}",
                    source=source_name,
                    error=exc,
                )
            next_target += interval_seconds

    async def _cron_loop(self, source_name: str, expr: str) -> None:
        """Cron 表达式刷新循环。

        Cron-expression refresh loop.

        Args:
            source_name: 订阅源名称 / Source name.
            expr: Cron 表达式 / Cron expression.
        """
        tz = ZoneInfo(self.config.server.timezone)
        iterator = croniter(expr, datetime.now(tz))
        while not self._stopping.is_set():
            next_at = iterator.get_next(datetime)
            now = datetime.now(tz)
            # Skip any occurrences that have already passed (e.g. after a long refresh).
            while next_at <= now:
                next_at = iterator.get_next(datetime)
                now = datetime.now(tz)
            delay = (next_at - now).total_seconds()
            logger.debug(
                "cron loop: source={source} expr={expr} next_at={next_at} delay={delay:.1f}s",
                source=source_name,
                expr=expr,
                next_at=next_at.isoformat(),
                delay=delay,
            )
            await asyncio.sleep(delay)
            if self._stopping.is_set():
                return
            jitter = self._jitter_seconds()
            if jitter > 0:
                logger.debug(
                    "cron jitter: source={source} jitter={jitter:.1f}s",
                    source=source_name,
                    jitter=jitter,
                )
            await asyncio.sleep(jitter)
            if self._stopping.is_set():
                return
            try:
                await self.refresher.refresh(source_name)
            except Exception as exc:
                logger.warning(
                    "scheduled refresh failed for source {source}: {error}",
                    source=source_name,
                    error=exc,
                )
