"""CLI 解析器和命令冒烟测试。

CLI parser and command smoke tests.
"""

import asyncio
from pathlib import Path

import pytest

from mihomo_proxy_manager.cli import _build_runtime, build_parser, main
from mihomo_proxy_manager.refresher import RefreshResult


def test_build_parser_has_expected_commands() -> None:
    """测试 CLI 解析器包含预期的子命令。

    Test that the CLI parser has the expected subcommands.
    """
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore

    assert {"serve", "check", "refresh"} <= set(choices)  # type: ignore


def test_check_command_reports_valid_config(tmp_path: Path, capsys) -> None:
    """测试 check 命令报告配置有效。

    Test that the check command reports a valid configuration.

    Args:
        tmp_path: 临时目录路径 / Temporary directory path.
        capsys: pytest 标准输出捕获夹具 / pytest stdout capture fixture.
    """
    config = tmp_path / "config.toml"
    config.write_text(
        f'''
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[cache]
dir = "{tmp_path / "cache"}"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
''',
        encoding="utf-8",
    )

    code = main(["check", "-c", str(config)])

    assert code == 0
    assert "OK: configuration is valid" in capsys.readouterr().out


class FailingRefresher:
    """模拟刷新失败的刷新器。

    A fake refresher that always fails.
    """

    def __init__(self, **kwargs: object) -> None:
        """初始化 FailingRefresher。

        Initialize FailingRefresher.

        Args:
            **kwargs: 任意关键字参数（被忽略） / Arbitrary keyword arguments (ignored).
        """
        pass

    async def refresh(self, source_name: str) -> RefreshResult:
        """模拟刷新并返回失败结果。

        Simulate a refresh and return a failed result.

        Args:
            source_name: 源名称 / Source name.

        Returns:
            RefreshResult: 包含错误信息的失败结果 / Failed result with error info.
        """
        return RefreshResult(
            False, source_name, node_count=0, warning_count=1, error="boom"
        )


def test_refresh_command_failure_includes_node_and_warning_counts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """测试 refresh 命令失败时包含节点数和警告数。

    Test that the refresh command failure includes node and warning counts.

    Args:
        tmp_path: 临时目录路径 / Temporary directory path.
        monkeypatch: pytest monkeypatch 夹具 / pytest monkeypatch fixture.
        capsys: pytest 标准输出捕获夹具 / pytest stdout capture fixture.
    """
    config = tmp_path / "config.toml"
    config.write_text(
        f'''
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[cache]
dir = "{tmp_path / "cache"}"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
''',
        encoding="utf-8",
    )
    monkeypatch.setattr("mihomo_proxy_manager.cli.SourceRefresher", FailingRefresher)

    code = main(["refresh", "-c", str(config), "airport_a"])
    output = capsys.readouterr().out

    assert code == 1
    assert "nodes=0" in output
    assert "warnings=1" in output
    assert "error=boom" in output


def _write_cli_config(tmp_path: Path, *, access_enabled: bool = True) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f'''
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[cache]
dir = "{tmp_path / "cache"}"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[access_log]
enabled = {str(access_enabled).lower()}
db_path = "{tmp_path / "access" / "access.sqlite3"}"
''',
        encoding="utf-8",
    )
    return config


def test_serve_runtime_initializes_access_store_when_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    created = {}

    class FakeStore:
        def __init__(self, config):
            created["db_path"] = config.db_path

        def cleanup(self, now_ms=None):
            pass

        def record(self, event):
            pass

        def stats(self, now_ms=None):
            raise AssertionError("not used")

        def dispose(self):
            pass

    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", FakeStore)
    runtime = asyncio.run(
        _build_runtime(str(_write_cli_config(tmp_path)), debug=False, access_audit=True)
    )
    try:
        assert runtime.access_audit_store is not None
    finally:
        asyncio.run(runtime.client.aclose())
        runtime.access_audit_store.dispose()
    assert created["db_path"].name == "access.sqlite3"


def test_serve_runtime_does_not_initialize_access_store_when_disabled(
    monkeypatch, tmp_path: Path
) -> None:
    def fail_store(config):
        raise AssertionError("store should not be created")

    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", fail_store)
    runtime = asyncio.run(
        _build_runtime(
            str(_write_cli_config(tmp_path, access_enabled=False)),
            debug=False,
            access_audit=True,
        )
    )
    try:
        assert runtime.access_audit_store is None
    finally:
        asyncio.run(runtime.client.aclose())


def test_build_runtime_does_not_initialize_access_store_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    def fail_store(config):
        raise AssertionError("store should not be created")

    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", fail_store)
    runtime = asyncio.run(_build_runtime(str(_write_cli_config(tmp_path)), debug=False))
    try:
        assert runtime.access_audit_store is None
    finally:
        asyncio.run(runtime.client.aclose())


def test_serve_cleans_up_runtime_when_app_setup_fails(
    monkeypatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def aclose(self) -> None:
            events.append("client_closed")

    class FakeStore:
        def __init__(self, config: object) -> None:
            pass

        def dispose(self) -> None:
            events.append("store_disposed")

    def raise_setup_error(*args: object, **kwargs: object) -> object:
        raise RuntimeError("app setup failed")

    monkeypatch.setattr("mihomo_proxy_manager.cli.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", FakeStore)
    monkeypatch.setattr("mihomo_proxy_manager.cli.create_app", raise_setup_error)

    with pytest.raises(RuntimeError, match="app setup failed"):
        main(["serve", "-c", str(_write_cli_config(tmp_path))])

    assert events == ["store_disposed", "client_closed"]


def test_serve_cleans_up_runtime_when_server_serve_fails(
    monkeypatch, tmp_path: Path
) -> None:
    events: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def aclose(self) -> None:
            events.append("client_closed")

    class FakeStore:
        def __init__(self, config: object) -> None:
            pass

        def dispose(self) -> None:
            events.append("store_disposed")

    class FailingServer:
        def __init__(self, config: object) -> None:
            pass

        async def serve(self) -> None:
            raise RuntimeError("serve failed")

    monkeypatch.setattr("mihomo_proxy_manager.cli.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", FakeStore)
    monkeypatch.setattr("mihomo_proxy_manager.cli.uvicorn.Server", FailingServer)

    with pytest.raises(RuntimeError, match="serve failed"):
        main(["serve", "-c", str(_write_cli_config(tmp_path))])

    assert events == ["store_disposed", "client_closed"]
