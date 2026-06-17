from pathlib import Path

from mihomo_proxy_manager.config import load_config, parse_duration, parse_file_mode, parse_size


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def minimal_config() -> str:
    return """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""


def test_parse_duration() -> None:
    assert parse_duration("30s").total_seconds() == 30
    assert parse_duration("5m").total_seconds() == 300
    assert parse_duration("2h").total_seconds() == 7200
    assert parse_duration("7d").total_seconds() == 604800


def test_parse_size() -> None:
    assert parse_size("10 B") == 10
    assert parse_size("10 KB") == 10 * 1024
    assert parse_size("10 MB") == 10 * 1024 * 1024


def test_load_config_applies_defaults(temp_config_path: Path) -> None:
    config = load_config(write_config(temp_config_path, minimal_config()))

    assert config.server.host == "0.0.0.0"
    assert config.cache.file_mode == 0o600
    assert config.sources["airport_a"].format == "auto"
    assert config.routes["phone"].sources == ("airport_a",)


def test_validation_collects_multiple_errors(temp_config_path: Path) -> None:
    body = """
[server]
health_path = "/same"
status_path = "/same"

[sources.airport_a]
url = "ftp://example.com/sub"

[routes.phone]
path = "not-starting-with-slash"
sources = ["missing"]
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert not report.ok
    joined = "\n".join(report.errors)
    assert "route 'phone' path must start with '/'" in joined
    assert "route 'phone' references missing source 'missing'" in joined
    assert "unsupported URL scheme" in joined
    assert "health_path and status_path collide" in joined


def test_validation_rejects_invalid_enums_and_route_regex(temp_config_path: Path) -> None:
    body = """
[scheduler]
startup_refresh_mode = "sideways"

[sources.airport_a]
url = "https://example.com/sub"
parse_error = "explode"

[plugins.turn_on]
type = "shell"
url = "https://example.com/action"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "full-config"

[routes.phone.filter]
include = "["
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)
    joined = "\n".join(report.errors)

    assert "startup_refresh_mode" in joined
    assert "parse_error" in joined
    assert "plugin 'turn_on' type is unsupported" in joined
    assert "route 'phone' output format is unsupported" in joined
    assert "route 'phone' include regex is invalid" in joined


def test_file_mode_accepts_toml_integer(temp_config_path: Path) -> None:
    body = minimal_config() + """
[cache]
file_mode = 0o600
"""
    config = load_config(write_config(temp_config_path, body))

    assert config.cache.file_mode == 0o600


def test_parse_file_mode() -> None:
    assert parse_file_mode(0o600) == 0o600
    assert parse_file_mode("0600") == 0o600
    assert parse_file_mode("0o600") == 0o600
    assert parse_file_mode(384) == 384
    assert parse_file_mode("600") == 600


def test_cron_accepts_single_string(temp_config_path: Path) -> None:
    body = minimal_config() + """
[sources.airport_a.refresh]
cron = "0 * * * *"
"""
    config = load_config(write_config(temp_config_path, body))
    assert config.sources["airport_a"].refresh.cron == ("0 * * * *",)


def test_success_status_accepts_single_int(temp_config_path: Path) -> None:
    body = minimal_config() + """
[plugins.turn_on]
url = "https://example.com/action"
success_status = 204
"""
    config = load_config(write_config(temp_config_path, body))
    assert config.plugins["turn_on"].success_status == (204,)


def test_file_logging_defaults_to_disabled(temp_config_path: Path) -> None:
    config = load_config(write_config(temp_config_path, minimal_config()))
    assert not config.logging_file.enabled
