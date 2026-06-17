from __future__ import annotations

import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from .models import AppConfig


class RefreshScheduler:
    def __init__(self, config: AppConfig, refresher) -> None:
        self.config = config
        self.refresher = refresher
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self.config.scheduler.startup_refresh:
            if self.config.scheduler.startup_refresh_mode == "blocking":
                await asyncio.gather(*(self.refresher.refresh(name) for name in self.config.sources))
            else:
                for name in self.config.sources:
                    self._tasks.append(asyncio.create_task(self.refresher.refresh(name)))
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

    async def _jitter(self) -> None:
        seconds = self.config.scheduler.jitter.total_seconds()
        if seconds > 0:
            await asyncio.sleep(random.uniform(0, seconds))

    async def _interval_loop(self, source_name: str, interval_seconds: float) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(interval_seconds)
            await self._jitter()
            await self.refresher.refresh(source_name)

    async def _cron_loop(self, source_name: str, expr: str) -> None:
        tz = ZoneInfo(self.config.server.timezone)
        iterator = croniter(expr, datetime.now(tz))
        while not self._stopping.is_set():
            next_at = iterator.get_next(datetime)
            delay = max(0.0, (next_at - datetime.now(tz)).total_seconds())
            await asyncio.sleep(delay)
            await self._jitter()
            await self.refresher.refresh(source_name)
