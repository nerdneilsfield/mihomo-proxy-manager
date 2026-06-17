from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from loguru import logger
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .cache import SourceCacheStore
from .models import AppConfig, ProxyRecord, SourceConfig
from .render import ProviderRenderer
from .status import build_status


def _is_still_valid(cache, max_stale) -> bool:
    return bool(cache and cache.last_success_at and datetime.now(UTC) - cache.last_success_at <= max_stale)


def _is_due(cache, source: SourceConfig, timezone: str) -> bool:
    if not cache or not cache.last_success_at:
        return True
    now = datetime.now(UTC)
    reference = cache.last_attempt_at or cache.last_success_at
    if source.refresh.interval and now - reference >= source.refresh.interval:
        return True
    if source.refresh.cron:
        tz = ZoneInfo(timezone)
        last_attempt = reference.astimezone(tz)
        now_tz = now.astimezone(tz)
        for expr in source.refresh.cron:
            previous = croniter(expr, now_tz).get_prev(datetime)
            if previous > last_attempt:
                return True
    return False


def _track_background_refresh(task: asyncio.Task, source_name: str) -> None:
    try:
        result = task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("background refresh failed for source {source}: {error}", source=source_name, error=exc)
        return
    if result is not None and not getattr(result, "ok", True):
        logger.warning(
            "background refresh failed for source {source}: {error}",
            source=source_name,
            error=getattr(result, "error", None),
        )


def create_app(config: AppConfig, *, cache_store: SourceCacheStore, refresher, scheduler) -> Starlette:
    renderer = ProviderRenderer(yaml_sort_keys=config.output.yaml_sort_keys)
    route_by_path = {route.path: route for route in config.routes.values()}

    async def health(request):
        return JSONResponse({"ok": True})

    async def status(request):
        return JSONResponse(await build_status(cache_store, list(config.sources)))

    async def provider(request):
        route = route_by_path.get(request.url.path)
        if route is None:
            return PlainTextResponse("not found", status_code=404)

        records: list[ProxyRecord] = []
        missing: list[str] = []
        due: list[str] = []
        for source_name in route.sources:
            cache = await cache_store.get(source_name)
            if _is_still_valid(cache, config.cache.max_stale):
                records.extend(cache.proxies)
                if _is_due(cache, config.sources[source_name], config.server.timezone):
                    due.append(source_name)
            else:
                missing.append(source_name)

        for source_name in due:
            if refresher is not None:
                task = asyncio.create_task(refresher.refresh(source_name))
                task.add_done_callback(lambda item, name=source_name: _track_background_refresh(item, name))
        if due:
            await asyncio.sleep(0)

        if missing and refresher is not None:
            tasks = [asyncio.create_task(refresher.refresh(name)) for name in missing]
            if route.require_all_sources or not records:
                await asyncio.wait(tasks, timeout=config.server.route_refresh_wait.total_seconds())
                records.clear()
                for source_name in route.sources:
                    cache = await cache_store.get(source_name)
                    if _is_still_valid(cache, config.cache.max_stale):
                        records.extend(cache.proxies)
                    elif route.require_all_sources:
                        return PlainTextResponse("route unavailable", status_code=503)
            else:
                for source_name, task in zip(missing, tasks, strict=False):
                    task.add_done_callback(lambda item, name=source_name: _track_background_refresh(item, name))

        if not records:
            return PlainTextResponse("route unavailable", status_code=503)

        body = await renderer.render(route, records)
        return Response(body, media_type="application/yaml; charset=utf-8")

    routes = [Route(config.server.health_path, health)]
    if config.server.status_path:
        routes.append(Route(config.server.status_path, status))
    routes.append(Route("/{path:path}", provider))

    async def lifespan(app):
        if scheduler:
            await scheduler.start()
        yield
        if scheduler:
            await scheduler.stop()

    return Starlette(routes=routes, lifespan=lifespan)
