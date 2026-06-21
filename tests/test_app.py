"""Web 应用路由和生命周期测试。

Web application route and lifecycle tests.
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from starlette.testclient import TestClient

from mihomo_proxy_manager.access_audit import AccessEvent
from mihomo_proxy_manager.app import create_app
from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.config import load_config
from mihomo_proxy_manager.models import (
    AppConfig,
    ProxyRecord,
    RouteAccessConfig,
    RouteOutputConfig,
    SourceCache,
)


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


def app_config_with_route_output(
    tmp_path,
    output: RouteOutputConfig,
    public_base_url: str | None = None,
    allowed_user_agents: tuple[str, ...] = (),
) -> AppConfig:
    """Create app config with a replaced route output."""
    config = load_config(config_file(tmp_path))
    route = config.routes["phone"]
    route = replace(
        route,
        output=output,
        access=RouteAccessConfig(user_agent=tuple(allowed_user_agents)),
    )
    return replace(
        config,
        server=replace(config.server, public_base_url=public_base_url),
        routes={**config.routes, "phone": route},
    )


def auto_app_config(
    tmp_path,
    *,
    auto_default: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = (
        "provider"
    ),
    import_link: bool = True,
    allowed_user_agents: tuple[str, ...] = (),
) -> AppConfig:
    return app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(
            format="auto",
            auto_default=auto_default,
            encoding="plain",
            import_link=import_link,
            resource_tag="MPM",
        ),
        public_base_url="https://mpm.example.com",
        allowed_user_agents=allowed_user_agents,
    )


def ss_node(name: str = "SS 01") -> ProxyRecord:
    return ProxyRecord(
        "airport_a",
        {
            "name": name,
            "type": "ss",
            "server": "example.com",
            "port": 443,
            "cipher": "chacha20-ietf-poly1305",
            "password": "password",
        },
    )


def source_cache_with_nodes(*nodes: ProxyRecord) -> SourceCache:
    """Create a valid source cache containing the given nodes."""
    now = datetime.now(UTC)
    return SourceCache(
        "airport_a",
        1,
        now,
        now,
        None,
        None,
        len(nodes),
        (),
        None,
        nodes,
    )


class FakeAccessAuditStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[AccessEvent] = []
        self.disposed = False

    def record(self, event: AccessEvent) -> None:
        if self.fail:
            raise RuntimeError("audit write failed")
        self.events.append(event)

    def cleanup(self, now_ms: int | None = None) -> None:
        pass

    def stats(self, now_ms: int | None = None):
        raise AssertionError("stats should not be called")

    def dispose(self) -> None:
        self.disposed = True


class FailingCacheStore:
    async def get(self, source_name: str):
        raise RuntimeError("cache read failed")

    async def set(self, source_name: str, cache) -> None:
        raise AssertionError("cache must not be written")

    async def status(self, source_name: str):
        raise AssertionError("cache must not be queried")

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        raise AssertionError("refresh state must not change")

    def cache_path(self, source_name: str) -> str | None:
        return None


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
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM/api")

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
    assert response.headers["content-type"].startswith("application/yaml")
    assert "proxies:" in response.text


@pytest.mark.asyncio
async def test_route_serves_xray_uri_output(tmp_path) -> None:
    """Test that route dispatch serves xray-uri output."""
    config = app_config_with_route_output(
        tmp_path, RouteOutputConfig(format="xray-uri", encoding="plain")
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "Trojan 01",
                    "type": "trojan",
                    "server": "example.com",
                    "port": 443,
                    "password": "secret",
                    "sni": "example.com",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("trojan://")


@pytest.mark.asyncio
async def test_qx_import_endpoint_redirects_with_public_base_url(tmp_path) -> None:
    """Test quantumult-x import companion redirects with public main URL."""
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="quantumult-x", resource_tag="MPM"),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS 01",
                    "type": "ss",
                    "server": "example.com",
                    "port": 443,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "password",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(
            f"{config.routes['phone'].path}-import", follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "quantumult-x:///add-resource?remote-resource="
    )
    assert "https%3A%2F%2Fmpm.example.com" in response.headers["location"]


@pytest.mark.asyncio
async def test_qx_import_endpoint_not_registered_when_disabled(tmp_path) -> None:
    """Test quantumult-x import companion is absent when import_link is disabled."""
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="quantumult-x", import_link=False),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS 01",
                    "type": "ss",
                    "server": "example.com",
                    "port": 443,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "password",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(f"{config.routes['phone'].path}-import")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_surfboard_profile_embeds_public_nodes_url(tmp_path) -> None:
    """Test Surfboard full profile embeds the public nodes companion URL."""
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS 01",
                    "type": "ss",
                    "server": "example.com",
                    "port": 443,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "password",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 200
    assert "policy-path=https://mpm.example.com" in response.text
    assert "FINAL,Main" in response.text


@pytest.mark.asyncio
async def test_surfboard_nodes_companion_uses_same_access_policy(tmp_path) -> None:
    """Test Surfboard nodes companion uses the parent route access policy."""
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        allowed_user_agents=("mihomo/1.19.5",),
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS 01",
                    "type": "ss",
                    "server": "example.com",
                    "port": 443,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "password",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    nodes_path = f"{config.routes['phone'].path}-nodes"

    with TestClient(app) as client:
        missing_ua = client.get(nodes_path, headers={"User-Agent": ""})
        matching_ua = client.get(nodes_path, headers={"User-Agent": "mihomo/1.19.5"})

    assert missing_ua.status_code == 403
    assert matching_ua.status_code == 200
    assert "[Proxy]" not in matching_ua.text
    assert matching_ua.text.startswith("SS 01 = ss,")


@pytest.mark.asyncio
async def test_auto_route_query_targets_each_renderer(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        surfboard = client.get(f"{path}?target=surfboard")
        qx = client.get(f"{path}?format=quanx")
        provider = client.get(f"{path}?flag=meta")
        xray = client.get(f"{path}?client=v2rayn")

    assert surfboard.status_code == 200
    assert "[Proxy]" in surfboard.text
    assert qx.status_code == 200
    assert qx.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in qx.text
    assert provider.status_code == 200
    assert provider.headers["content-type"].startswith("application/yaml")
    assert "proxies:" in provider.text
    assert xray.status_code == 200
    assert xray.headers["content-type"].startswith("text/plain")
    assert xray.text.startswith("ss://")


@pytest.mark.asyncio
async def test_auto_route_query_priority_and_blank_selector(tmp_path) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        target_wins = client.get(f"{path}?target=surfboard&format=quanx")
        format_wins = client.get(f"{path}?format=quanx&flag=meta&client=v2rayn")
        flag_wins = client.get(f"{path}?flag=meta&client=v2rayn")
        blank_suppresses_format = client.get(f"{path}?target=&format=quanx")
        whitespace_suppresses_format = client.get(f"{path}?target=%20%20&format=quanx")

    assert "[Proxy]" in target_wins.text
    assert format_wins.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in format_wins.text
    assert "proxies:" in flag_wins.text
    assert "proxies:" in blank_suppresses_format.text
    assert "proxies:" in whitespace_suppresses_format.text


@pytest.mark.asyncio
async def test_auto_route_user_agent_selection_and_case_insensitivity(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        qx = client.get(path, headers={"User-Agent": "quantumult x/1.0"})
        xray = client.get(path, headers={"User-Agent": "V2RAYN meta"})
        provider = client.get(path, headers={"User-Agent": "sing-box Clash"})

    assert qx.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in qx.text
    assert xray.text.startswith("ss://")
    assert "proxies:" in provider.text


@pytest.mark.asyncio
async def test_auto_route_companion_suffix_beats_user_agent_when_query_auto(
    tmp_path,
) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        nodes = client.get(
            f"{path}-nodes?target=auto",
            headers={"User-Agent": "FlClash/1.0"},
        )
        qx_import = client.get(
            f"{path}-import?target=auto",
            headers={"User-Agent": "FlClash/1.0"},
            follow_redirects=False,
        )

    assert nodes.status_code == 200
    assert nodes.text.startswith("SS 01 = ss,")
    assert qx_import.status_code == 302
    assert "target%3Dquanx" in qx_import.headers["location"]


@pytest.mark.asyncio
async def test_auto_route_future_user_agent_only_logs_warning_and_uses_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    warnings: list[str] = []

    def capture_warning(message: str, *args: object, **kwargs: object) -> None:
        if kwargs:
            warnings.append(message.format(**kwargs))
        elif args:
            warnings.append(message.format(*args))
        else:
            warnings.append(message)

    monkeypatch.setattr("mihomo_proxy_manager.app.logger.warning", capture_warning)

    with TestClient(app) as client:
        response = client.get(
            config.routes["phone"].path,
            headers={"User-Agent": "sing-box/1.0"},
        )

    assert response.status_code == 200
    assert "proxies:" in response.text
    assert len(warnings) == 1
    assert "future User-Agent target" in warnings[0]
    assert "sing-box/1.0" in warnings[0]


@pytest.mark.asyncio
async def test_auto_route_main_target_auto_uses_user_agent_then_default(tmp_path) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        ua_selected = client.get(
            f"{path}?target=auto",
            headers={"User-Agent": "Surfboard/2.0"},
        )
        default_selected = client.get(f"{path}?target=auto")

    assert "[Proxy]" in ua_selected.text
    assert "proxies:" in default_selected.text


@pytest.mark.asyncio
async def test_auto_route_canonical_urls_for_incoming_selector_keys(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        by_target = client.get(f"{path}?target=surfboard")
        by_format = client.get(f"{path}?format=surfboard")
        by_flag = client.get(f"{path}?flag=surfboard")
        by_client = client.get(f"{path}?client=surfboard")

    for response in (by_target, by_format, by_flag, by_client):
        assert response.status_code == 200
        assert f"{path}-nodes?target=surfboard" in response.text


@pytest.mark.asyncio
async def test_auto_route_import_disabled_leaves_import_path_404(tmp_path) -> None:
    config = auto_app_config(tmp_path, import_link=False)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(f"{config.routes['phone'].path}-import")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fixed_provider_and_surfboard_ignore_auto_selectors_and_user_agent(
    tmp_path,
) -> None:
    provider_config = app_config_with_route_output(tmp_path, RouteOutputConfig())
    surfboard_config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )

    for config, expected in (
        (provider_config, "proxies:"),
        (surfboard_config, "[Proxy]"),
    ):
        store = JsonSourceCacheStore(config.cache)
        await store.set("airport_a", source_cache_with_nodes(ss_node()))
        app = create_app(config, cache_store=store, refresher=None, scheduler=None)

        with TestClient(app) as client:
            response = client.get(
                f"{config.routes['phone'].path}?target=quanx&format=v2rayn",
                headers={"User-Agent": "Quantumult X/1.0"},
            )

        assert response.status_code == 200
        assert expected in response.text


@pytest.mark.asyncio
async def test_fixed_surfboard_embedded_urls_stay_queryless(tmp_path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(
            f"{config.routes['phone'].path}?target=quanx",
            headers={"User-Agent": "Quantumult X/1.0"},
        )

    assert response.status_code == 200
    assert f"{config.routes['phone'].path}-nodes" in response.text
    assert "target=" not in response.text


@pytest.mark.asyncio
async def test_auto_route_rejects_unsupported_and_incompatible_targets(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        unknown = client.get(f"{path}?target=not-a-client")
        future = client.get(f"{path}?target=singbox")
        incompatible = client.get(f"{path}-nodes?target=quanx")

    assert unknown.status_code == 400
    assert unknown.text == "unsupported target"
    assert future.status_code == 400
    assert future.text == "unsupported target"
    assert incompatible.status_code == 400
    assert incompatible.text == "target does not support companion"


@pytest.mark.asyncio
async def test_auto_route_does_not_double_decode_query_target(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        once_decoded = client.get(f"{path}?target=clash%2Dmeta")
        double_encoded = client.get(f"{path}?target=clash%252Dmeta")

    assert once_decoded.status_code == 200
    assert "proxies:" in once_decoded.text
    assert double_encoded.status_code == 400
    assert double_encoded.text == "unsupported target"


@pytest.mark.asyncio
async def test_access_audit_records_success_route(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get(
            path,
            headers={
                "cf-connecting-ip": "203.0.113.10",
                "user-agent": "Surfboard/2.24",
            },
        )

    assert response.status_code == 200
    assert len(store.events) == 1
    event = store.events[0]
    assert event.route_name == "phone"
    assert event.path == path
    assert event.companion is None
    assert event.status_code == 200
    assert event.target_format == "provider"
    assert event.response_bytes == len(response.content)
    assert event.real_ip == "203.0.113.10"
    assert event.ip_source == "cf-connecting-ip"
    assert event.user_agent == "Surfboard/2.24"


@pytest.mark.asyncio
async def test_access_audit_records_forbidden_and_bad_target(tmp_path) -> None:
    config = auto_app_config(tmp_path, allowed_user_agents=("allowed",))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        forbidden = client.get(path, headers={"user-agent": "blocked"})
        bad = client.get(
            f"{path}?target=unknown", headers={"user-agent": "allowed"}
        )

    assert forbidden.status_code == 403
    assert bad.status_code == 400
    assert [event.status_code for event in store.events] == [403, 400]
    assert store.events[1].target_format is None


@pytest.mark.asyncio
async def test_access_audit_records_422_for_unsupported_nodes(tmp_path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="xray-uri", encoding="plain"),
    )
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {"name": "bad", "type": "tuic", "server": "example.com", "port": 443},
            )
        ),
    )
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 422
    assert len(store.events) == 1
    event = store.events[0]
    assert event.route_name == "phone"
    assert event.path == path
    assert event.status_code == 422


@pytest.mark.asyncio
async def test_access_audit_records_503(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    empty_store = JsonSourceCacheStore(config.cache)
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=empty_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 503
    assert len(store.events) == 1
    event = store.events[0]
    assert event.route_name == "phone"
    assert event.path == path
    assert event.status_code == 503


def test_access_audit_records_500_for_provider_exception(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=FailingCacheStore(),
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="cache read failed"):
            client.get(path)

    assert len(store.events) == 1
    event = store.events[0]
    assert event.route_name == "phone"
    assert event.path == path
    assert event.status_code == 500


@pytest.mark.asyncio
async def test_access_audit_excludes_health_status_and_unknown(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    assert config.server.status_path is not None

    with TestClient(app) as client:
        client.get(config.server.health_path)
        client.get(config.server.status_path)
        client.get(f"{config.server.status_path}/api")
        client.get("/unknown")

    assert store.events == []


@pytest.mark.asyncio
async def test_access_audit_failure_does_not_change_response(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore(fail=True)
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_access_audit_store_disposed_on_lifespan_shutdown(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    store = FakeAccessAuditStore()
    app = create_app(
        config,
        cache_store=cache_store,
        refresher=None,
        scheduler=None,
        access_audit_store=store,
    )

    with TestClient(app):
        pass

    assert store.disposed


def test_auto_route_access_runs_before_target_validation_and_cache_read(tmp_path) -> None:
    config = auto_app_config(tmp_path, allowed_user_agents=("mihomo/*",))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        bad_target = client.get(
            f"{path}?target=not-a-client",
            headers={"User-Agent": "blocked/1.0"},
        )
        bad_companion = client.get(
            f"{path}-nodes?target=quanx",
            headers={"User-Agent": "blocked/1.0"},
        )

    assert bad_target.status_code == 403
    assert bad_companion.status_code == 403


@pytest.mark.asyncio
async def test_auto_route_embeds_canonical_public_urls(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        surfboard = client.get(f"{path}?format=surfboard")
        qx_import = client.get(f"{path}-import?target=auto", follow_redirects=False)

    assert "policy-path=https://mpm.example.com" in surfboard.text
    assert f"{path}-nodes?target=surfboard" in surfboard.text
    assert qx_import.status_code == 302
    assert "target%3Dquanx" in qx_import.headers["location"]


@pytest.mark.asyncio
async def test_all_skipped_nodes_return_422(tmp_path) -> None:
    """Test route returns 422 when the renderer skips every node."""
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": "VLESS 01",
                    "type": "vless",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 422
    assert "no supported nodes" in response.text


@pytest.mark.asyncio
async def test_render_warnings_redact_secrets(tmp_path, monkeypatch) -> None:
    """Test app-level render warning logs redact configured secrets."""
    from mihomo_proxy_manager import app as app_module

    warnings: list[str] = []

    def capture_warning(message: str, **kwargs: object) -> None:
        warnings.append(message.format(**kwargs))

    monkeypatch.setattr(app_module.logger, "warning", capture_warning)

    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    secret_route_path = "/p/secret-value-CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    route = replace(config.routes["phone"], path=secret_route_path)
    config = replace(config, routes={**config.routes, "phone": route})
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {
                    "name": secret_route_path,
                    "type": "vless",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "password": secret_route_path,
                },
            )
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(secret_route_path)

    rendered_warnings = "\n".join(warnings)
    assert response.status_code == 422
    assert "route render warning" in rendered_warnings
    assert "secret-value" not in rendered_warnings
    assert "***" in rendered_warnings


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
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM/api")

    assert response.status_code == 200
    data = response.json()
    assert route_path not in data["sources"][0]["last_error"]
    assert "***" in data["sources"][0]["last_error"]


class ExplodingCacheStore:
    async def get(self, source_name: str):
        raise AssertionError("cache must not be read")

    async def set(self, source_name: str, cache) -> None:
        raise AssertionError("cache must not be written")

    async def status(self, source_name: str):
        raise AssertionError("cache must not be queried")

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


@pytest.mark.asyncio
async def test_status_root_returns_html_dashboard(tmp_path) -> None:
    """测试状态根路径返回 HTML 页面。"""
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
                ProxyRecord("airport_a", {"name": "JP", "type": "ss"}),
            ),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    text = response.text
    assert "Sources" in text
    assert "Routes" in text
    assert "airport_a" in text
    assert "phone" in text


@pytest.mark.asyncio
async def test_status_api_returns_json_and_route_stats(tmp_path) -> None:
    """测试状态 API 返回 JSON，并包含路由与协议统计。"""
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
                ProxyRecord("airport_a", {"name": "JP", "type": "ss"}),
            ),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get("/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM/api")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["summary"]["sources"]["total"] == 1
    assert data["summary"]["routes"]["total"] == 1
    assert data["summary"]["protocols"] == {"ss": 1, "vmess": 1}
    assert data["routes"][0]["name"] == "phone"
    assert data["routes"][0]["node_count"] == 2
    assert data["routes"][0]["protocols"] == {"ss": 1, "vmess": 1}
    assert data["sources"][0]["protocols"] == {"ss": 1, "vmess": 1}
    assert data["sources"][0]["healthy"] is True
