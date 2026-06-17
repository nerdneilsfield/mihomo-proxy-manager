"""CLI 解析器和命令冒烟测试。

CLI parser and command smoke tests.
"""

from pathlib import Path


from mihomo_proxy_manager.cli import build_parser, main
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
