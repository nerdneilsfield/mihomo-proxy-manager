"""Starlette Web 应用，提供健康检查、状态查询和 provider 路由。

Starlette web application providing health, status, and provider routes.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, TypeGuard, cast
from zoneinfo import ZoneInfo

from croniter import croniter
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .access import sanitize_user_agent, user_agent_allowed
from .access_audit import (
    AccessAuditStore,
    AccessEvent,
    format_access_log_line,
    now_epoch_ms,
    resolve_real_ip,
    sanitize_headers,
)
from .cache import SourceCacheStore
from .logging import _collect_secret_values
from .models import AppConfig, ProxyRecord, RouteConfig, SourceCache, SourceConfig
from .render import RenderRequest, build_renderer_registry
from .route_targets import (
    COMPANION_TARGETS,
    IMPLEMENTED_FORMATS,
    canonical_target_for_format,
    has_future_user_agent_signal,
    resolve_query_selection,
    resolve_user_agent_format,
)
from .security import redact_secret
from .status import build_status, render_status_html

ImplementedOutputFormat = Literal[
    "provider", "surfboard", "quantumult-x", "xray-uri", "clash-config"
]


class _Refresher(Protocol):
    """刷新器协议，定义异步刷新接口。

    Refresher protocol defining the asynchronous refresh interface.
    """

    async def refresh(self, source_name: str) -> Any:
        """异步刷新指定名称的源。

        Asynchronously refresh the source identified by the given name.

        Args:
            source_name: 要刷新的源名称 / The name of the source to refresh.

        Returns:
            刷新结果，具体类型由实现决定 / The refresh result, type depends on implementation.
        """


class _Scheduler(Protocol):
    """调度器协议，定义异步启动和停止接口。

    Scheduler protocol defining the asynchronous start and stop interface.
    """

    async def start(self) -> None:
        """启动调度器。

        Start the scheduler.
        """

    async def stop(self) -> None:
        """停止调度器。

        Stop the scheduler.
        """


def _is_still_valid(
    cache: SourceCache | None, max_stale: timedelta
) -> TypeGuard[SourceCache]:
    """检查缓存是否仍在最大过期时间内有效。

    Check whether the cache is still valid within the maximum staleness duration.

    Args:
        cache: 源缓存对象，可能为 None / The source cache object, may be None.
        max_stale: 允许的最大过期时间 / The maximum allowed staleness.

    Returns:
        如果缓存存在且未超过过期时间则返回 True，否则返回 False /
        True if the cache exists and has not exceeded the staleness threshold, False otherwise.
    """
    if cache is None or cache.last_success_at is None:
        return False
    return datetime.now(UTC) - cache.last_success_at <= max_stale


def _is_due(cache: SourceCache | None, source: SourceConfig, timezone: str) -> bool:
    """根据间隔或 cron 表达式判断源是否该刷新了。

    Determine whether a source is due for refresh based on interval or cron expression.

    Args:
        cache: 源缓存对象，可能为 None / The source cache object, may be None.
        source: 源配置 / The source configuration.
        timezone: 用于 cron 计算的时区字符串 / The timezone string used for cron evaluation.

    Returns:
        如果源需要刷新则返回 True，否则返回 False /
        True if the source is due for a refresh, False otherwise.
    """
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


def _track_background_refresh(task: asyncio.Task[Any], source_name: str) -> None:
    """记录后台刷新任务的结果（成功或失败）。

    Log the result (success or failure) of a background refresh task.

    Args:
        task: 已完成的后台刷新任务 / The completed background refresh task.
        source_name: 被刷新的源名称 / The name of the source that was refreshed.
    """
    try:
        result = task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning(
            "background refresh failed for source {source}: {error}",
            source=source_name,
            error=exc,
        )
        return
    if result is not None and not getattr(result, "ok", True):
        error = getattr(result, "error", None) or "unknown error"
        logger.warning(
            "background refresh failed for source {source}: {error}",
            source=source_name,
            error=error,
        )


def create_app(
    config: AppConfig,
    *,
    cache_store: SourceCacheStore,
    refresher: _Refresher | None,
    scheduler: _Scheduler | None,
    access_audit_store: AccessAuditStore | None = None,
) -> Starlette:
    """创建并配置 Starlette 应用。

    Create and configure the Starlette application.

    注册健康检查、状态和 provider 路由，并管理生命周期（调度器启停、后台任务清理）。
    Registers health, status, and provider routes, and manages the lifecycle
    (scheduler start/stop, background task cleanup).

    Args:
        config: 应用配置 / The application configuration.
        cache_store: 源缓存存储实例 / The source cache store instance.
        refresher: 可选的刷新器，用于刷新源缓存 / Optional refresher for refreshing source caches.
        scheduler: 可选的调度器，用于定时刷新 / Optional scheduler for periodic refreshes.

    Returns:
        配置完成的 Starlette 应用实例 / The configured Starlette application instance.
    """
    renderers = build_renderer_registry(yaml_sort_keys=config.output.yaml_sort_keys)
    background_tasks: set[asyncio.Task[Any]] = set()
    secrets = _collect_secret_values(config)

    def _public_url(path: str) -> str:
        if config.server.public_base_url:
            return f"{config.server.public_base_url}{path}"
        return path

    def _public_url_with_target(path: str, output_format: str) -> str:
        separator = "&" if "?" in path else "?"
        target = canonical_target_for_format(output_format)
        return f"{_public_url(path)}{separator}target={target}"

    def _query_values(request: Request, key: str) -> list[str]:
        return request.query_params.getlist(key)

    def _query_selection(request: Request):
        return resolve_query_selection(
            {
                "target": _query_values(request, "target"),
                "format": _query_values(request, "format"),
                "flag": _query_values(request, "flag"),
                "client": _query_values(request, "client"),
            }
        )

    def _effective_output_format(
        route: RouteConfig, companion: str | None, request: Request
    ) -> tuple[ImplementedOutputFormat | None, str | None]:
        if route.output.format != "auto":
            return route.output.format, None

        selection = _query_selection(request)
        if selection.explicit:
            if selection.format not in IMPLEMENTED_FORMATS:
                return None, "unsupported target"
            selected_format = cast(ImplementedOutputFormat, selection.format)
            if companion and COMPANION_TARGETS.get(companion) != selected_format:
                return None, "target does not support companion"
            return selected_format, None

        if companion:
            implied = COMPANION_TARGETS.get(companion)
            if implied is None:
                return None, "target does not support companion"
            return cast(ImplementedOutputFormat, implied), None

        request_user_agent = request.headers.get("user-agent")
        ua_format = resolve_user_agent_format(request_user_agent)
        if ua_format is None and has_future_user_agent_signal(request_user_agent):
            logger.warning(
                "auto route future User-Agent target ignored: route={route} "
                "user_agent={user_agent} fallback={fallback}",
                route=route.name,
                user_agent=sanitize_user_agent(request_user_agent),
                fallback=route.output.auto_default,
            )
        if ua_format is not None:
            return cast(ImplementedOutputFormat, ua_format), None
        return route.output.auto_default, None

    def _render_route_for_format(
        route: RouteConfig, output_format: ImplementedOutputFormat
    ) -> RouteConfig:
        return replace(route, output=replace(route.output, format=output_format))

    def _main_public_url(
        route: RouteConfig, output_format: ImplementedOutputFormat
    ) -> str:
        if route.output.format == "auto":
            return _public_url_with_target(route.path, output_format)
        return _public_url(route.path)

    def _companion_public_urls(route: RouteConfig) -> dict[str, str]:
        if route.output.format == "auto":
            urls = {
                "nodes": _public_url_with_target(f"{route.path}-nodes", "surfboard")
            }
            if route.output.import_link:
                urls["import"] = _public_url_with_target(route.path, "quantumult-x")
            return urls
        return companion_public_urls_by_route.get(route.name, {})

    async def _record_access_event(event: AccessEvent) -> None:
        if access_audit_store is None or not config.access_log.enabled:
            return
        try:
            await asyncio.to_thread(access_audit_store.record, event)
            if config.access_log.file.enabled:
                logger.bind(access_log=True).info(format_access_log_line(event))
        except Exception as exc:
            logger.warning("access audit write failed: {error}", error=exc)

    def _response_bytes(response: Response) -> int:
        body = getattr(response, "body", b"")
        return len(body) if isinstance(body, (bytes, bytearray)) else 0

    def _access_event(
        *,
        request: Request,
        route: RouteConfig,
        companion: str | None,
        start_ms: int,
        response: Response,
        target_format: str | None,
    ) -> AccessEvent:
        resolved = resolve_real_ip(
            client_host=request.client.host if request.client else None,
            headers=dict(request.headers),
            trusted_proxies=config.access_log.trusted_proxies,
            header_order=config.access_log.real_ip_headers,
        )
        headers = sanitize_headers(
            dict(request.headers),
            max_value_length=config.access_log.headers.max_value_length,
            extra_secrets=secrets,
        )
        return AccessEvent(
            visited_at=start_ms,
            route_name=route.name,
            path=request.url.path,
            companion=companion,
            method=request.method,
            status_code=response.status_code,
            real_ip=resolved.real_ip,
            ip_source=resolved.ip_source,
            user_agent=headers.get("user-agent"),
            headers=headers,
            target_format=target_format,
            response_bytes=_response_bytes(response),
            duration_ms=max(0, now_epoch_ms() - start_ms),
        )

    route_by_path: dict[str, tuple[RouteConfig, str | None]] = {}
    companion_public_urls_by_route: dict[str, dict[str, str]] = {}
    for route in config.routes.values():
        route_by_path[route.path] = (route, None)
        if route.output.format == "auto":
            route_companion_urls = {
                "nodes": _public_url_with_target(f"{route.path}-nodes", "surfboard")
            }
            companion_paths = [(f"{route.path}-nodes", "nodes")]
            if route.output.import_link:
                companion_paths.append((f"{route.path}-import", "import"))
                route_companion_urls["import"] = _public_url_with_target(
                    route.path, "quantumult-x"
                )
        else:
            route_companion_urls = {}
            renderer = renderers[route.output.format]
            companion_paths = []
            for companion_path in renderer.companion_paths(route):
                prefix = f"{route.path}-"
                companion = (
                    companion_path[len(prefix) :]
                    if companion_path.startswith(prefix)
                    else companion_path
                )
                companion_paths.append((companion_path, companion))
                route_companion_urls[companion] = _public_url(companion_path)

        for companion_path, companion in companion_paths:
            route_by_path[companion_path] = (route, companion)
        companion_public_urls_by_route[route.name] = route_companion_urls

    async def health(request):
        """健康检查端点。

        Health check endpoint.

        Args:
            request: HTTP 请求对象 / The HTTP request object.

        Returns:
            包含 ok 状态的 JSON 响应 / A JSON response containing the ok status.
        """
        return JSONResponse({"ok": True})

    api_path = (
        f"{config.server.status_path.rstrip('/')}/api"
        if config.server.status_path
        else None
    )

    async def status(request):
        """状态端点：根路径返回 HTML，/api 子路径返回 JSON。

        Status endpoint: the root path returns an HTML dashboard; the ``/api``
        sub-path returns the JSON API.

        Args:
            request: HTTP 请求对象 / The HTTP request object.

        Returns:
            HTML 响应或 JSON 响应 / HTML or JSON response.
        """
        data = await build_status(
            cache_store,
            config,
            extra_secrets=secrets,
            access_audit_store=access_audit_store,
        )
        if api_path is not None and request.url.path == api_path:
            return JSONResponse(data)
        return HTMLResponse(render_status_html(data))

    def _spawn_background_refresh(source_name: str) -> None:
        """在后台触发源的刷新。

        Trigger a background refresh for the given source.

        Args:
            source_name: 要刷新的源名称 / The name of the source to refresh.
        """
        if refresher is None:
            return
        task = asyncio.create_task(refresher.refresh(source_name))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        task.add_done_callback(
            lambda item, name=source_name: _track_background_refresh(item, name)
        )

    async def provider(request):
        """Provider 端点，返回合并后的代理 YAML。

        Provider endpoint returning the merged proxy YAML.

        Args:
            request: HTTP 请求对象 / The HTTP request object.

        Returns:
            YAML 格式的响应，包含合并后的代理配置 /
            A YAML response containing the merged proxy configuration.

        Raises:
            不直接抛出异常，但在源不可用时返回 404 或 503 状态码 /
            Does not raise exceptions directly, but returns 404 or 503 status codes
            when sources are unavailable.
        """
        route_match = route_by_path.get(request.url.path)
        if route_match is None:
            logger.debug("provider 404: path={path}", path=request.url.path)
            return PlainTextResponse("not found", status_code=404)
        route, companion = route_match
        start_ms = now_epoch_ms()
        target_format_for_audit: str | None = None
        response: Response | None = None
        try:
            request_user_agent = request.headers.get("user-agent")
            if not user_agent_allowed(route.access, request_user_agent):
                logger.info(
                    "provider forbidden: route={route} user_agent={user_agent}",
                    route=route.name,
                    user_agent=sanitize_user_agent(request_user_agent),
                )
                response = PlainTextResponse("forbidden", status_code=403)
                return response

            output_format, target_error = _effective_output_format(
                route, companion, request
            )
            if target_error is not None or output_format is None:
                response = PlainTextResponse(
                    target_error or "unsupported target", status_code=400
                )
                return response
            target_format_for_audit = output_format

            records: list[ProxyRecord] = []
            missing: list[str] = []
            due: list[str] = []
            for source_name in route.sources:
                cache = await cache_store.get(source_name)
                if _is_still_valid(cache, config.cache.max_stale):
                    records.extend(cache.proxies)
                    if _is_due(
                        cache, config.sources[source_name], config.server.timezone
                    ):
                        due.append(source_name)
                else:
                    missing.append(source_name)
            logger.debug(
                "provider request: route={route} sources={sources} valid={valid} missing={missing} due={due}",
                route=route.name,
                sources=len(route.sources),
                valid=len(route.sources) - len(missing),
                missing=missing,
                due=due,
            )

            for source_name in due:
                _spawn_background_refresh(source_name)
            if due:
                await asyncio.sleep(0)

            if missing and refresher is not None:
                logger.debug(
                    "provider triggering missing refresh: route={route} missing={missing} require_all={require_all}",
                    route=route.name,
                    missing=missing,
                    require_all=route.require_all_sources,
                )
                tasks = [
                    asyncio.create_task(refresher.refresh(name)) for name in missing
                ]
                task_by_source = dict(zip(missing, tasks, strict=False))
                if route.require_all_sources or not records:
                    done, pending = await asyncio.wait(
                        tasks, timeout=config.server.route_refresh_wait.total_seconds()
                    )
                    for source_name, task in task_by_source.items():
                        if task in pending:
                            background_tasks.add(task)
                            task.add_done_callback(background_tasks.discard)
                            task.add_done_callback(
                                lambda item, name=source_name: (
                                    _track_background_refresh(item, name)
                                )
                            )
                            continue
                        try:
                            task.result()
                        except Exception as exc:
                            logger.warning(
                                "route refresh failed for source {source}: {error}",
                                source=source_name,
                                error=exc,
                            )
                    records.clear()
                    for source_name in route.sources:
                        cache = await cache_store.get(source_name)
                        if _is_still_valid(cache, config.cache.max_stale):
                            records.extend(cache.proxies)
                        elif route.require_all_sources:
                            logger.warning(
                                "provider 503 (require_all): route={route} unavailable_source={source}",
                                route=route.name,
                                source=source_name,
                            )
                            response = PlainTextResponse(
                                "route unavailable", status_code=503
                            )
                            return response
                else:
                    for source_name, task in zip(missing, tasks, strict=False):
                        background_tasks.add(task)
                        task.add_done_callback(background_tasks.discard)
                        task.add_done_callback(
                            lambda item, name=source_name: _track_background_refresh(
                                item, name
                            )
                        )

            if not records:
                logger.warning(
                    "provider 503: route={route} no records available",
                    route=route.name,
                )
                response = PlainTextResponse("route unavailable", status_code=503)
                return response

            render_route = _render_route_for_format(route, output_format)
            renderer = renderers[output_format]
            render_response = renderer.render(
                RenderRequest(
                    render_route,
                    records,
                    request_base_url=str(request.base_url),
                    main_public_url=_main_public_url(route, output_format),
                    companion_public_urls=_companion_public_urls(route),
                    companion=companion,
                )
            )
            for warning in render_response.warnings:
                logger.warning(
                    "route render warning: route={route} warning={warning}",
                    route=route.name,
                    warning=redact_secret(warning, extra_secrets=secrets),
                )
            logger.info(
                "provider served: route={route} nodes={nodes} bytes={bytes}",
                route=route.name,
                nodes=len(records),
                bytes=len(render_response.body),
            )
            response = Response(
                render_response.body,
                status_code=render_response.status_code,
                media_type=render_response.media_type,
                headers=render_response.headers,
            )
            return response
        finally:
            if response is not None:
                await _record_access_event(
                    _access_event(
                        request=request,
                        route=route,
                        companion=companion,
                        start_ms=start_ms,
                        response=response,
                        target_format=target_format_for_audit,
                    )
                )

    routes = [Route(config.server.health_path, health)]
    if config.server.status_path:
        routes.append(Route(config.server.status_path, status))
        if api_path:
            routes.append(Route(api_path, status))
    routes.append(Route("/{path:path}", provider))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        """应用生命周期：启动调度器，关闭时停止调度器并取消后台任务。

        Application lifecycle: start the scheduler on startup, stop the scheduler
        and cancel background tasks on shutdown.

        Args:
            app: Starlette 应用实例 / The Starlette application instance.

        Yields:
            无 / None.
        """
        if scheduler:
            try:
                await scheduler.start()
            except Exception:
                await scheduler.stop()
                raise
        try:
            yield
        finally:
            if scheduler:
                await scheduler.stop()
            if background_tasks:
                for task in list(background_tasks):
                    task.cancel()
                await asyncio.gather(*background_tasks, return_exceptions=True)
            if access_audit_store is not None:
                try:
                    await asyncio.to_thread(access_audit_store.dispose)
                except Exception as exc:
                    logger.warning(
                        "access audit store dispose failed: {error}", error=exc
                    )

    return Starlette(routes=routes, lifespan=lifespan)
