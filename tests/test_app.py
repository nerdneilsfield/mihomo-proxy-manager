from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from mihomo_proxy_manager.app import create_app
from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.config import load_config
from mihomo_proxy_manager.models import ProxyRecord, SourceCache


def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'''
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
''', encoding="utf-8")
    return path


class FakeRefresher:
    def __init__(self) -> None:
        self.called: list[str] = []

    async def refresh(self, source_name: str):
        self.called.append(source_name)


@dataclass(frozen=True)
class FailedResult:
    ok: bool = False
    error: str | None = None


@pytest.mark.asyncio
async def test_status_endpoint_returns_source_states(tmp_path) -> None:
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
    config = load_config(config_file(tmp_path))
    app = create_app(config, cache_store=JsonSourceCacheStore(config.cache), refresher=None, scheduler=None)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/missing").status_code == 404


@pytest.mark.asyncio
async def test_provider_serves_stale_valid_cache_and_triggers_refresh(tmp_path) -> None:
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
    def __init__(self, error: str | None = None) -> None:
        self.called: list[str] = []
        self.error = error

    async def refresh(self, source_name: str):
        self.called.append(source_name)
        return FailedResult(ok=False, error=self.error)


@pytest.mark.asyncio
async def test_background_refresh_failure_without_error_is_handled(tmp_path) -> None:
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
