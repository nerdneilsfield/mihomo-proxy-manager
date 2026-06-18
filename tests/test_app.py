"""Web 应用路由和生命周期测试。

Web application route and lifecycle tests.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from mihomo_proxy_manager.app import create_app
from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.config import load_config
from mihomo_proxy_manager.models import ProxyRecord, SourceCache


def config_file(tmp_path):
    """创建一个临时配置文件。

    Create a temporary config file.

    Args:
        tmp_path: pytest 临时目录 / pytest temporary directory.

    Returns:
        Path: 配置文件路径 / Config file path.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        f'''
[server]
health_path = "/healthz"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
route_refresh_wait = "1s"

[cache]
dir = "{tmp_path / "cache"}"
max_stale = "7d"

[sources.airport_a]
url = "https://example.com/sub"

[sources.airport_a.refresh]
interval = "1h"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
''',
        encoding="utf-8",
    )
    return path


class FakeRefresher:
    """模拟刷新器，记录被调用的源名称。

    A fake refresher that records which sources were refreshed.
    """

    def __init__(self) -> None:
        """初始化 FakeRefresher，记录列表为空。

        Initialize FakeRefresher with an empty call list.
        """
        self.called: list[str] = []

    async def refresh(self, source_name: str):
        """记录被刷新的源名称。

        Record the source name being refreshed.

        Args:
            source_name: 源名称 / Source name.
        """
        self.called.append(source_name)


@dataclass(frozen=True)
class FailedResult:
    """表示一个失败的结果。

    Represents a failed result.
    """

    ok: bool = False
    error: str | None = None


@pytest.mark.asyncio
async def test_status_endpoint_returns_source_states(tmp_path) -> None:
    """测试状态端点返回源状态信息。

    Test that the status endpoint returns source state information.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    now = datetime.now(UTC)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            now,
            now,
            None,
            None,
            2,
            (),
            None,
            (
                ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),
                ProxyRecord("airport_a", {"name": "JP", "type": "vmess"}),
            ),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM")

    assert response.status_code == 200
    data = response.json()
    assert data["sources"][0]["source"] == "airport_a"
    assert data["sources"][0]["node_count"] == 2
    assert data["sources"][0]["last_error"] is None


@pytest.mark.asyncio
async def test_provider_route_returns_yaml(tmp_path) -> None:
    """测试提供者路由返回 YAML 内容。

    Test that the provider route returns YAML content.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            datetime.now(UTC),
            datetime.now(UTC),
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 200
    assert "proxies:" in response.text


def test_health_and_unknown_path(tmp_path) -> None:
    """测试健康检查和未知路径返回正确的状态码。

    Test that health check and unknown paths return correct status codes.
    """
    config = load_config(config_file(tmp_path))
    app = create_app(
        config,
        cache_store=JsonSourceCacheStore(config.cache),
        refresher=None,
        scheduler=None,
    )

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/missing").status_code == 404


@pytest.mark.asyncio
async def test_provider_serves_stale_valid_cache_and_triggers_refresh(tmp_path) -> None:
    """测试提供者在缓存过期时提供旧缓存并触发后台刷新。

    Test that the provider serves stale cache and triggers a background refresh.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    old_success = datetime.now(UTC) - timedelta(hours=2)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            old_success,
            old_success,
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    refresher = FakeRefresher()
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 200
    assert refresher.called == ["airport_a"]


@pytest.mark.asyncio
async def test_provider_uses_last_attempt_to_avoid_refresh_storm(tmp_path) -> None:
    """测试提供者使用最近尝试时间来避免刷新风暴。

    Test that the provider uses last attempt time to avoid a refresh storm.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    old_success = datetime.now(UTC) - timedelta(hours=2)
    recent_attempt = datetime.now(UTC)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            recent_attempt,
            old_success,
            None,
            None,
            1,
            (),
            "recent failure",
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    refresher = FakeRefresher()
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 200
    assert refresher.called == []


class FailingRefresher:
    """模拟刷新失败。

    A fake refresher that fails on refresh.
    """

    def __init__(self, error: str | None = None) -> None:
        """初始化 FailingRefresher。

        Initialize FailingRefresher.

        Args:
            error: 可选的错误信息 / Optional error message.
        """
        self.called: list[str] = []
        self.error = error

    async def refresh(self, source_name: str):
        """模拟刷新并返回失败结果。

        Simulate a refresh and return a failed result.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            FailedResult: 失败结果 / Failed result.
        """
        self.called.append(source_name)
        return FailedResult(ok=False, error=self.error)


@pytest.mark.asyncio
async def test_background_refresh_failure_without_error_is_handled(tmp_path) -> None:
    """测试后台刷新失败（无错误信息）时仍能正确处理。

    Test that background refresh failure without error is handled gracefully.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    old_success = datetime.now(UTC) - timedelta(hours=2)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            old_success,
            old_success,
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    refresher = FailingRefresher(error=None)
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 200
    assert refresher.called == ["airport_a"]


class RaisingRefresher:
    """模拟刷新时抛出异常。

    A fake refresher that raises an exception on refresh.
    """

    def __init__(self) -> None:
        """初始化 RaisingRefresher。

        Initialize RaisingRefresher.
        """
        self.called: list[str] = []

    async def refresh(self, source_name: str):
        """模拟刷新并抛出异常。

        Simulate a refresh and raise an exception.

        Args:
            source_name: 源名称 / Source name.

        Raises:
            RuntimeError: 总是抛出 / Always raised.
        """
        self.called.append(source_name)
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_provider_serves_stale_cache_and_logs_background_refresh_exception(
    tmp_path, monkeypatch
) -> None:
    """测试提供者在后台刷新异常时提供旧缓存并记录警告。

    Test that the provider serves stale cache and logs a warning on background refresh exception.
    """
    from mihomo_proxy_manager import app as app_module

    warnings: list[str] = []
    monkeypatch.setattr(
        app_module.logger, "warning", lambda msg, **kwargs: warnings.append(msg)
    )

    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    old_success = datetime.now(UTC) - timedelta(hours=2)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            old_success,
            old_success,
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    refresher = RaisingRefresher()
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 200
    assert "proxies:" in response.text
    assert refresher.called == ["airport_a"]
    assert any("background refresh failed" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_provider_logs_awaited_refresh_exception_and_returns_503(
    tmp_path, monkeypatch
) -> None:
    """测试提供者在等待刷新异常时记录警告并返回 503。

    Test that the provider logs a warning and returns 503 on awaited refresh exception.
    """
    from mihomo_proxy_manager import app as app_module

    warnings: list[str] = []
    monkeypatch.setattr(
        app_module.logger, "warning", lambda msg, **kwargs: warnings.append(msg)
    )

    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    refresher = RaisingRefresher()
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 503
    assert refresher.called == ["airport_a"]
    assert any("route refresh failed" in msg for msg in warnings)


class FailingScheduler:
    """模拟启动时失败的调度器。

    A fake scheduler that fails on start.
    """

    def __init__(self) -> None:
        """初始化 FailingScheduler。

        Initialize FailingScheduler.
        """
        self.stop_called = False

    async def start(self) -> None:
        """模拟启动并抛出异常。

        Simulate start and raise an exception.

        Raises:
            RuntimeError: 总是抛出 / Always raised.
        """
        raise RuntimeError("startup refresh failed")

    async def stop(self) -> None:
        """记录 stop 被调用。

        Record that stop was called.
        """
        self.stop_called = True


def test_lifespan_stops_scheduler_when_startup_fails(tmp_path) -> None:
    """测试应用生命周期在启动失败时停止调度器。

    Test that the app lifespan stops the scheduler when startup fails.
    """
    config = load_config(config_file(tmp_path))
    scheduler = FailingScheduler()
    app = create_app(
        config,
        cache_store=JsonSourceCacheStore(config.cache),
        refresher=None,
        scheduler=scheduler,
    )

    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass

    assert scheduler.stop_called


class SleepRefresher:
    """模拟长时间休眠的刷新器，用于测试取消。

    A fake refresher that sleeps, used to test cancellation.
    """

    def __init__(self) -> None:
        """初始化 SleepRefresher。

        Initialize SleepRefresher.
        """
        self.cancelled = False

    async def refresh(self, source_name: str) -> None:
        """模拟长时间运行的可取消操作。

        Simulate a long-running cancellable operation.

        Args:
            source_name: 源名称 / Source name.

        Raises:
            asyncio.CancelledError: 当任务被取消时 / When the task is cancelled.
        """
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_lifespan_cancels_background_refreshes_on_shutdown(tmp_path) -> None:
    """测试应用关闭时取消后台刷新任务。

    Test that the app lifespan cancels background refreshes on shutdown.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    refresher = SleepRefresher()
    app = create_app(config, cache_store=store, refresher=refresher, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml")

    assert response.status_code == 503
    assert refresher.cancelled


@pytest.mark.asyncio
async def test_status_endpoint_redacts_route_path_in_last_error(tmp_path) -> None:
    """测试状态端点对 last_error 中的路由路径进行脱敏。

    Test that the status endpoint redacts the route path in last_error.
    """
    config = load_config(config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    now = datetime.now(UTC)
    route_path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            now,
            now,
            None,
            None,
            0,
            (),
            f"failed to fetch {route_path}",
            (),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM")

    assert response.status_code == 200
    data = response.json()
    assert route_path not in data["sources"][0]["last_error"]
    assert "***" in data["sources"][0]["last_error"]


class ExplodingCacheStore:
    async def get(self, source_name: str):
        raise AssertionError("cache must not be read")

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        raise AssertionError("refresh state must not change")

    def cache_path(self, source_name: str) -> str | None:
        return None


def access_config_file(tmp_path):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + """
[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*"]
""",
        encoding="utf-8",
    )
    return path


def test_provider_forbids_missing_user_agent_before_cache_read(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": ""},
        )

    assert response.status_code == 403


def test_provider_forbids_non_matching_user_agent_before_cache_read(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": "Mihomo/1.19.5"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_provider_allows_matching_user_agent(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            datetime.now(UTC),
            datetime.now(UTC),
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": "mihomo/1.19.5"},
        )

    assert response.status_code == 200
    assert "proxies:" in response.text


def test_health_ignores_route_user_agent_access(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=JsonSourceCacheStore(config.cache),
        refresher=None,
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get("/healthz", headers={"User-Agent": ""})

    assert response.status_code == 200
