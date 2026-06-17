from pathlib import Path

from mihomo_proxy_manager.cli import build_parser, main


def test_build_parser_has_expected_commands() -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore

    assert {"serve", "check", "refresh"} <= set(choices)  # type: ignore


def test_check_command_reports_valid_config(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'''
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[cache]
dir = "{tmp_path / "cache"}"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
''', encoding="utf-8")

    code = main(["check", "-c", str(config)])

    assert code == 0
    assert "OK: configuration is valid" in capsys.readouterr().out
