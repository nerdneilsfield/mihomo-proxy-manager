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
    def __init__(self, config: AppConfig, refresher) -> None:
        self.config = config
        self.refresher = refresher
        self._tasks: list[asyncio.Task[Any]] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self.config.scheduler.startup_refresh:
            if self.config.scheduler.startup_refresh_mode == "blocking":
                await asyncio.gather(*(self.refresher.refresh(name) for name in self.config.sources))
            else:
                for name in self.config.sources:
                    task = asyncio.create_task(self.refresher.refresh(name))
                    task.add_done_callback(lambda item, name=name: self._track_startup_refresh(item, name))
                    self._tasks.append(task)
        for name, source in self.config.sources.items():
            if source.refresh.interval:
                self._tasks.append(asyncio.create_task(self._interval_loop(name, source.refresh.interval.total_seconds())))
            for expr in source.refresh.cron:
                self._tasks.append(asyncio.create_task(self._cron_loop(name, expr)))

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _track_startup_refresh(self, task: asyncio.Task[Any], source_name: str) -> None:
        try:
            result = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("startup refresh failed for source {source}: {error}", source=source_name, error=exc)
            return
        if result is not None and not getattr(result, "ok", True):
            error = getattr(result, "error", None) or "unknown error"
            logger.warning("startup refresh failed for source {source}: {error}", source=source_name, error=error)

    def _jitter_seconds(self) -> float:
        seconds = self.config.scheduler.jitter.total_seconds()
        if seconds > 0:
            return random.uniform(0, seconds)
        return 0.0

    async def _interval_loop(self, source_name: str, interval_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        next_target = loop.time() + interval_seconds
        jitter_seconds = self.config.scheduler.jitter.total_seconds()
        while not self._stopping.is_set():
            # Apply jitter as a one-time offset around the target so the base
            # interval stays aligned and does not drift across cycles.
            jitter = random.uniform(-jitter_seconds / 2, jitter_seconds / 2) if jitter_seconds > 0 else 0.0
            delay = max(0.0, next_target + jitter - loop.time())
            await asyncio.sleep(delay)
            if self._stopping.is_set():
                return
            await self.refresher.refresh(source_name)
            next_target += interval_seconds

    async def _cron_loop(self, source_name: str, expr: str) -> None:
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
            await asyncio.sleep(delay)
            if self._stopping.is_set():
                return
            await asyncio.sleep(self._jitter_seconds())
            await self.refresher.refresh(source_name)
