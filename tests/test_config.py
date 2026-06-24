"""配置加载、解析和验证测试。

Configuration loading, parsing, and validation tests.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from mihomo_proxy_manager.config import (
    load_config,
    parse_duration,
    parse_file_mode,
    parse_size,
)

from tests.conftest import CLASH_CONFIG_TEMPLATE_BODY, write_clash_template


def write_config(path: Path, body: str) -> Path:
    """将配置内容写入文件。

    Write config content to a file.

    Args:
        path: 文件路径 / File path.
        body: 配置内容 / Config content.

    Returns:
        Path: 写入的文件路径 / The written file path.
    """
    path.write_text(body, encoding="utf-8")
    return path


def minimal_config() -> str:
    """生成最小有效配置。

    Generate a minimal valid configuration.

    Returns:
        str: TOML 格式的配置字符串 / TOML config string.
    """
    return """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""


def _write_base_config(
    path: Path,
    output: str,
    *,
    server: str = 'public_base_url = "https://mpm.example.com/base"',
    route_path: str = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
) -> Path:
    return write_config(
        path,
        f"""
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
{server}

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "{route_path}"
sources = ["airport_a"]

[routes.phone.output]
{output}
""",
    )


def test_parse_duration() -> None:
    """测试持续时间解析函数 / Test the duration parsing function."""
    assert parse_duration("30s").total_seconds() == 30
    assert parse_duration("5m").total_seconds() == 300
    assert parse_duration("2h").total_seconds() == 7200
    assert parse_duration("7d").total_seconds() == 604800


def test_parse_size() -> None:
    """测试大小解析函数 / Test the size parsing function."""
    assert parse_size("10 B") == 10
    assert parse_size("10 KB") == 10 * 1024
    assert parse_size("10 MB") == 10 * 1024 * 1024


def test_load_config_applies_defaults(temp_config_path: Path) -> None:
    """测试加载配置时应用默认值。

    Test that loading a config applies default values.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    config = load_config(write_config(temp_config_path, minimal_config()))

    assert config.server.host == "0.0.0.0"
    assert config.cache.file_mode == 0o600
    assert config.http.user_agent == "mihomo/1.19.5"
    assert config.sources["airport_a"].format == "auto"
    assert config.routes["phone"].sources == ("airport_a",)


def test_access_log_defaults(temp_config_path: Path) -> None:
    config = load_config(
        write_config(temp_config_path, minimal_config()), validate=False
    )
    access = config.access_log

    assert access.enabled is True
    assert access.db_path == Path("data/access/access.sqlite3")
    assert access.retention == timedelta(days=30)
    assert tuple(str(item) for item in access.trusted_proxies) == (
        "127.0.0.1/32",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
    assert access.real_ip_headers == (
        "cf-connecting-ip",
        "true-client-ip",
        "x-forwarded-for",
        "x-real-ip",
    )
    assert access.file.enabled is True
    assert access.file.path == Path("logs/access.log")
    assert access.file.rotation == "10 MB"
    assert access.file.retention == "30 days"
    assert access.file.compression == "gz"
    assert access.headers.max_value_length == 512
    assert access.headers.stats_allowlist == (
        "user-agent",
        "host",
        "cf-ipcountry",
        "cf-ray",
    )
    assert access.headers.stats_max_rows == 5000
    assert access.status.enabled is True
    assert access.status.mask_ips is True
    assert access.status.include_recent is False
    assert access.status.recent_limit == 20
    assert access.status.top_limit == 20


def test_access_log_parses_nested_config(temp_config_path: Path) -> None:
    path = write_config(
        temp_config_path,
        minimal_config()
        + """
        [access_log]
        enabled = true
        db_path = "audit/access.sqlite3"
        retention = "7d"
        trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]
        real_ip_headers = ["x-real-ip", "x-forwarded-for"]

        [access_log.file]
        enabled = true
        path = "audit/access.log"
        rotation = "5 MB"
        retention = "7 days"
        compression = "gz"

        [access_log.headers]
        max_value_length = 128
        stats_allowlist = ["user-agent", "referer"]
        stats_max_rows = 100

        [access_log.status]
        enabled = false
        mask_ips = false
        include_recent = true
        recent_limit = 5
        top_limit = 10
        """,
    )
    config = load_config(path, validate=False)
    access = config.access_log

    assert access.db_path == Path("audit/access.sqlite3")
    assert access.retention == timedelta(days=7)
    assert tuple(str(item) for item in access.trusted_proxies) == (
        "127.0.0.1/32",
        "10.0.0.0/8",
    )
    assert access.real_ip_headers == ("x-real-ip", "x-forwarded-for")
    assert access.file.path == Path("audit/access.log")
    assert access.headers.max_value_length == 128
    assert access.headers.stats_allowlist == ("user-agent", "referer")
    assert access.status.enabled is False
    assert access.status.mask_ips is False
    assert access.status.include_recent is True
    assert access.status.recent_limit == 5
    assert access.status.top_limit == 10


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("[access_log]\nunknown = true\n", "access_log key is unsupported"),
        ("[access_log.file]\nunknown = true\n", "access_log.file key is unsupported"),
        (
            "[access_log.headers]\nunknown = true\n",
            "access_log.headers key is unsupported",
        ),
        (
            "[access_log.status]\nunknown = true\n",
            "access_log.status key is unsupported",
        ),
        ('[access_log]\nretention = "0s"\n', "access_log.retention must be positive"),
        (
            '[access_log]\ntrusted_proxies = ["not-a-network"]\n',
            "trusted proxy is invalid",
        ),
        (
            "[access_log]\ntrusted_proxies = [127]\n",
            "access_log.trusted_proxies must contain string values",
        ),
        (
            '[access_log]\nreal_ip_headers = ["forwarded"]\n',
            "real_ip_headers value is unsupported",
        ),
        (
            "[access_log]\nreal_ip_headers = [true]\n",
            "access_log.real_ip_headers must contain string values",
        ),
        (
            "[access_log.headers]\nmax_value_length = 0\n",
            "max_value_length must be positive",
        ),
        (
            "[access_log.headers]\nmax_value_length = true\n",
            "max_value_length must be positive",
        ),
        (
            "[access_log.headers]\nmax_value_length = 1.5\n",
            "max_value_length must be positive",
        ),
        (
            '[access_log.headers]\nmax_value_length = "1.5"\n',
            "max_value_length must be positive",
        ),
        (
            "[access_log.headers]\nmax_value_length = -1\n",
            "max_value_length must be positive",
        ),
        (
            "[access_log.headers]\nstats_allowlist = [1]\n",
            "access_log.headers.stats_allowlist must contain string values",
        ),
        (
            "[access_log.headers]\nstats_max_rows = 0\n",
            "stats_max_rows must be positive",
        ),
        (
            "[access_log.status]\nrecent_limit = 0\n",
            "recent_limit must be positive",
        ),
        ("[access_log.status]\ntop_limit = 0\n", "top_limit must be positive"),
    ],
)
def test_access_log_rejects_invalid_config(
    temp_config_path: Path, snippet: str, message: str
) -> None:
    path = write_config(temp_config_path, minimal_config() + "\n" + snippet)
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_access_log_filesystem_checks_create_dirs(
    temp_config_path: Path, tmp_path: Path
) -> None:
    config_path = write_config(
        temp_config_path,
        minimal_config()
        + f"""
        [access_log]
        enabled = true
        db_path = "{tmp_path / "data" / "access.sqlite3"}"

        [access_log.file]
        enabled = true
        path = "{tmp_path / "logs" / "access.log"}"
        """,
    )
    config = load_config(config_path)

    assert config.check_filesystem() == []
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert not (tmp_path / "data" / "access.sqlite3").exists()
    assert not (tmp_path / "logs" / "access.log").exists()


def test_access_log_disabled_skips_access_dirs(
    temp_config_path: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "disabled" / "access.sqlite3"
    log_path = tmp_path / "disabled-logs" / "access.log"
    config_path = write_config(
        temp_config_path,
        minimal_config()
        + f"""
        [access_log]
        enabled = false
        db_path = "{db_path}"

        [access_log.file]
        enabled = true
        path = "{log_path}"
        """,
    )
    config = load_config(config_path)

    assert config.check_filesystem() == []
    assert not db_path.parent.exists()
    assert not log_path.parent.exists()


def test_sqlalchemy_dependency_is_declared() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("uv.lock").read_text(encoding="utf-8")

    assert '"sqlalchemy>=2.0"' in pyproject
    assert "sqlalchemy==" in requirements.lower()
    assert "greenlet==3.5.2" in requirements.lower()
    assert 'name = "sqlalchemy"' in lock
    assert 'name = "greenlet"' in lock


def test_route_output_new_format_fields_are_parsed(temp_config_path: Path) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"

[sources.a]
url = "https://example.com/sub"

[routes.surf]
path = "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
sources = ["a"]

[routes.surf.output]
format = "surfboard"
mode = "full-profile"
test_url = "http://www.gstatic.com/generate_204"
test_interval = 300
test_timeout = 4
test_tolerance = 50

[routes.qx]
path = "/p/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
sources = ["a"]

[routes.qx.output]
format = "quantumult-x"
mode = "server-remote"
import_link = true
import_response = "plain"
import_target = "universal-link"
resource_tag = "Phones"

[routes.v2rayn]
path = "/p/cccccccccccccccccccccccccccccccccccccccc"
sources = ["a"]

[routes.v2rayn.output]
format = "xray-uri"
encoding = "plain"
"""
    loaded = load_config(write_config(temp_config_path, body), validate=False)

    assert loaded.server.public_base_url == "https://mpm.example.com/base"

    surf_output = loaded.routes["surf"].output
    assert surf_output.format == "surfboard"
    assert surf_output.include_meta_comments is False
    assert surf_output.mode == "full-profile"
    assert surf_output.test_url == "http://www.gstatic.com/generate_204"
    assert surf_output.test_interval == 300
    assert surf_output.test_timeout == 4
    assert surf_output.test_tolerance == 50

    qx_output = loaded.routes["qx"].output
    assert qx_output.format == "quantumult-x"
    assert qx_output.mode == "server-remote"
    assert qx_output.import_link is True
    assert qx_output.import_response == "plain"
    assert qx_output.import_target == "universal-link"
    assert qx_output.resource_tag == "Phones"

    v2rayn_output = loaded.routes["v2rayn"].output
    assert v2rayn_output.format == "xray-uri"
    assert v2rayn_output.encoding == "plain"


def test_auto_route_output_fields_are_parsed(temp_config_path: Path) -> None:
    config = load_config(
        _write_base_config(
            temp_config_path,
            """
format = "auto"
auto_default = "xray-uri"
include_meta_comments = true
encoding = "plain"
import_link = true
resource_tag = "Phones"
test_url = "http://www.gstatic.com/generate_204"
test_interval = 300
test_timeout = 4
test_tolerance = 50
""",
        )
    )

    output = config.routes["phone"].output
    assert output.format == "auto"
    assert output.auto_default == "xray-uri"
    assert output.include_meta_comments is True
    assert output.encoding == "plain"
    assert output.import_link is True
    assert output.resource_tag == "Phones"
    assert output.test_interval == 300
    assert output.test_timeout == 4
    assert output.test_tolerance == 50


def test_route_output_unknown_key_raises_clear_error(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[routes.phone.output]
unexpected = "value"
"""
    )

    with pytest.raises(ValueError, match="output key is unsupported"):
        load_config(write_config(temp_config_path, body), validate=False)


@pytest.mark.parametrize(
    ("output", "message"),
    (
        ('format = "unknown-format"', "output format is unsupported"),
        (
            'format = "xray-uri"\nencoding = "hex"',
            "encoding is unsupported",
        ),
        (
            'format = "quantumult-x"\nimport_response = "json"',
            "import_response is unsupported",
        ),
        (
            'format = "quantumult-x"\nimport_target = "browser"',
            "import_target is unsupported",
        ),
        (
            'format = "surfboard"\ntest_url = "https://www.gstatic.com/generate_204"',
            "test_url must use http://",
        ),
        (
            'format = "surfboard"\ntest_interval = 0',
            "test_interval must be between",
        ),
        (
            'format = "xray-uri"\ninclude_meta_comments = true',
            "include_meta_comments is only supported",
        ),
    ),
)
def test_route_output_validation_rejects_invalid_values(
    temp_config_path: Path, output: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_write_base_config(temp_config_path, output))


@pytest.mark.parametrize(
    ("output", "message"),
    (
        ('format = "auto"\nauto_default = "auto"', "auto_default is unsupported"),
        (
            'format = "auto"\nauto_default = "sing-box"',
            "auto_default is unsupported",
        ),
        (
            'format = "provider"\nauto_default = "auto"',
            "auto_default is unsupported",
        ),
        (
            'format = "provider"\nauto_default = "sing-box"',
            "auto_default is unsupported",
        ),
        ('format = "auto"\nmode = "full-profile"', "auto output mode must be default"),
        (
            'format = "auto"\nmode = "server-remote"',
            "auto output mode must be default",
        ),
    ),
)
def test_auto_route_output_validation_rejects_invalid_values(
    temp_config_path: Path, output: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_write_base_config(temp_config_path, output))


@pytest.mark.parametrize(
    "public_base_url",
    (
        "mpm.example.com/base",
        "ftp://mpm.example.com/base",
        "https:///base",
        "https://mpm.example.com/base/",
        "https://mpm.example.com/base?token=1",
        "https://mpm.example.com/base#fragment",
    ),
)
def test_server_public_base_url_must_be_http_url_without_suffix_parts(
    temp_config_path: Path, public_base_url: str
) -> None:
    server = f'public_base_url = "{public_base_url}"'

    with pytest.raises(ValueError, match="public_base_url"):
        load_config(
            _write_base_config(temp_config_path, 'format = "provider"', server=server)
        )


@pytest.mark.parametrize(
    "output",
    (
        'format = "surfboard"',
        'format = "quantumult-x"\nimport_link = true',
    ),
)
def test_route_output_formats_requiring_import_links_need_public_base_url(
    temp_config_path: Path, output: str
) -> None:
    with pytest.raises(ValueError, match="public_base_url is required"):
        load_config(_write_base_config(temp_config_path, output, server=""))


def test_auto_route_requires_public_base_url(temp_config_path: Path) -> None:
    with pytest.raises(
        ValueError, match="public_base_url is required for auto output"
    ) as exc_info:
        load_config(
            _write_base_config(
                temp_config_path,
                'format = "auto"',
                server="",
            )
        )
    # ``auto`` delegates to each implemented format for validation. Make sure
    # the per-format public_base_url errors are suppressed so the caller gets
    # exactly one auto-level error, not a stack of duplicate messages.
    message = str(exc_info.value)
    assert message.count("public_base_url is required") == 1


def test_route_output_companion_path_collision_is_rejected(
    temp_config_path: Path,
) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.surf]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a"]

[routes.surf.output]
format = "surfboard"

[routes.nodes]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL-nodes"
sources = ["airport_a"]
"""

    with pytest.raises(ValueError, match="path collision"):
        load_config(write_config(temp_config_path, body))


def test_auto_route_import_companion_not_registered_when_disabled(
    temp_config_path: Path,
) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.auto]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a"]

[routes.auto.output]
format = "auto"
import_link = false

[routes.normal_import]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL-import"
sources = ["airport_a"]
"""

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["auto"].output.format == "auto"
    assert config.routes["normal_import"].path.endswith("-import")


def test_auto_route_companion_path_collision_is_rejected(
    temp_config_path: Path,
) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.auto]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a"]

[routes.auto.output]
format = "auto"

[routes.nodes]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL-nodes"
sources = ["airport_a"]
"""

    with pytest.raises(ValueError, match="path collision"):
        load_config(write_config(temp_config_path, body))


def test_non_provider_route_does_not_inherit_global_meta_comments_default(
    temp_config_path: Path,
) -> None:
    body = """
[output]
default_include_meta_comments = true

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "xray-uri"
"""

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].output.include_meta_comments is False


def test_provider_route_inherits_global_meta_comments_default(
    temp_config_path: Path,
) -> None:
    body = """
[output]
default_include_meta_comments = true

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].output.include_meta_comments is True


def test_non_provider_route_explicit_meta_comments_true_is_rejected(
    temp_config_path: Path,
) -> None:
    body = """
[output]
default_include_meta_comments = true

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "xray-uri"
include_meta_comments = true
"""

    with pytest.raises(ValueError, match="include_meta_comments is only supported"):
        load_config(write_config(temp_config_path, body))


def test_status_api_path_collision_is_rejected(temp_config_path: Path) -> None:
    body = """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM/api"
sources = ["airport_a"]
"""

    with pytest.raises(ValueError, match="path collision"):
        load_config(write_config(temp_config_path, body))


def test_validation_collects_multiple_errors(temp_config_path: Path) -> None:
    """测试验证收集多个错误。

    Test that validation collects multiple errors.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
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


def test_validation_rejects_invalid_enums_and_route_regex(
    temp_config_path: Path,
) -> None:
    """测试验证拒绝无效的枚举值和路由正则表达式。

    Test that validation rejects invalid enums and route regex.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
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
format = "unknown-format"

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


def test_validation_rejects_invalid_user_agents(temp_config_path: Path) -> None:
    """测试验证拒绝非 Mihomo/Clash Meta 格式的 User-Agent。

    Test that validation rejects non-Mihomo/Clash Meta User-Agent values.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = """
[http]
user_agent = "mihomo-proxy-manager/0.1"

[sources.airport_a]
url = "https://example.com/sub"

[sources.airport_a.fetch]
user_agent = "bad-client/1.19.5"

[sources.airport_a.fetch.headers]
User-Agent = "custom-UA"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)
    joined = "\n".join(report.errors)

    assert not report.ok
    assert "http user_agent must use" in joined
    assert "source 'airport_a' fetch user_agent must use" in joined
    assert "source 'airport_a' fetch header User-Agent user_agent must use" in joined


def test_validation_accepts_mihomo_and_clash_meta_user_agents(
    temp_config_path: Path,
) -> None:
    """测试验证接受 Mihomo 和 Clash Meta 格式的 User-Agent。

    Test that validation accepts Mihomo and Clash Meta User-Agent values.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = """
[http]
user_agent = "mihomo/1.19.5"

[sources.airport_a]
url = "https://example.com/sub"

[sources.airport_a.fetch]
user_agent = "clash.meta/1.19.5"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert report.ok


def test_validation_accepts_clash_meta_dash_user_agent(
    temp_config_path: Path,
) -> None:
    """测试验证接受 clash-meta 格式的 User-Agent。

    Test that validation accepts the clash-meta User-Agent format.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = """
[http]
user_agent = "clash-meta/1.19.5"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert report.ok


@pytest.mark.parametrize(
    "user_agent",
    (
        "FlClash/v0.8.93 clash-verge Platform/macos",
        "FlClash/v0.8.76 clash-verge Platform/android",
        "clash-verge/v2.4.0",
    ),
)
def test_validation_accepts_common_clash_client_user_agents(
    temp_config_path: Path, user_agent: str
) -> None:
    body = f"""
[http]
user_agent = "{user_agent}"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
"""
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert report.ok


def test_file_mode_accepts_toml_integer(temp_config_path: Path) -> None:
    """测试文件模式接受 TOML 整数值。

    Test that file mode accepts TOML integer values.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = (
        minimal_config()
        + """
[cache]
file_mode = 0o600
"""
    )
    config = load_config(write_config(temp_config_path, body))

    assert config.cache.file_mode == 0o600


def test_parse_file_mode() -> None:
    """测试文件模式解析函数 / Test the file mode parsing function."""
    assert parse_file_mode(0o600) == 0o600
    assert parse_file_mode("0600") == 0o600
    assert parse_file_mode("0o600") == 0o600
    assert parse_file_mode(384) == 384
    assert parse_file_mode("600") == 600


def test_parse_file_mode_invalid_octal_raises_clear_error() -> None:
    """测试无效八进制文件模式抛出明确错误 / Test that invalid octal file mode raises a clear error."""
    with pytest.raises(ValueError, match="invalid file mode"):
        parse_file_mode("09")


def test_cron_accepts_single_string(temp_config_path: Path) -> None:
    """测试 cron 字段接受单个字符串。

    Test that the cron field accepts a single string.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = (
        minimal_config()
        + """
[sources.airport_a.refresh]
cron = "0 * * * *"
"""
    )
    config = load_config(write_config(temp_config_path, body))
    assert config.sources["airport_a"].refresh.cron == ("0 * * * *",)


def test_success_status_accepts_single_int(temp_config_path: Path) -> None:
    """测试 success_status 接受单个整数。

    Test that success_status accepts a single integer.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = (
        minimal_config()
        + """
[plugins.turn_on]
url = "https://example.com/action"
success_status = 204
"""
    )
    config = load_config(write_config(temp_config_path, body))
    assert config.plugins["turn_on"].success_status == (204,)


def test_file_logging_defaults_to_disabled(temp_config_path: Path) -> None:
    """测试文件日志默认禁用。

    Test that file logging defaults to disabled.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    config = load_config(write_config(temp_config_path, minimal_config()))
    assert not config.logging_file.enabled


def test_validation_rejects_missing_source_url(temp_config_path: Path) -> None:
    """测试验证拒绝缺少 URL 的源。

    Test that validation rejects a source missing a URL.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = minimal_config().replace(
        '[sources.airport_a]\nurl = "https://example.com/sub"',
        "[sources.airport_a]",
    )
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert not report.ok
    assert "source 'airport_a' URL is required" in "\n".join(report.errors)


def test_source_plugin_on_failure_must_be_abort_or_continue(
    temp_config_path: Path,
) -> None:
    """测试源插件 on_failure 必须是 abort 或 continue。

    Test that source plugin on_failure must be abort or continue.

    Args:
        temp_config_path: 临时配置文件路径 / Temporary config file path.
    """
    body = (
        minimal_config()
        + """
[sources.airport_a.plugins.before_fetch.turn_on]
on_failure = "panic"

[plugins.turn_on]
url = "https://example.com/action"
"""
    )
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)

    assert not report.ok
    joined = "\n".join(report.errors)
    assert "on_failure" in joined
    assert "'panic'" in joined


def test_dns_config_defaults_and_source_overrides(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://1.1.1.1:53", "https://dns.google/dns-query"]
timeout = "5s"
failure = "keep"

[sources.airport_a.dns]
enabled = true
servers = ["tls://1.1.1.1:853?servername=cloudflare-dns.com"]
timeout = "3s"
failure = "drop"

[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*"]
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.dns.servers == ("udp://1.1.1.1:53", "https://dns.google/dns-query")
    assert config.dns.timeout.total_seconds() == 5
    assert config.dns.failure == "keep"
    assert config.sources["airport_a"].dns.enabled is True
    assert config.sources["airport_a"].dns.servers == (
        "tls://1.1.1.1:853?servername=cloudflare-dns.com",
    )
    assert config.sources["airport_a"].dns.timeout.total_seconds() == 3
    assert config.sources["airport_a"].dns.failure == "drop"
    assert config.dns.enable_ipv6 is False
    assert config.sources["airport_a"].dns.enable_ipv6 is False
    assert config.routes["phone"].access.user_agent == ("mihomo/*", "clash-meta/*")


def test_dns_enable_ipv6_global_and_source_override(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://1.1.1.1:53"]
enable_ipv6 = true

[sources.airport_a.dns]
enabled = true
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.dns.enable_ipv6 is True
    assert config.sources["airport_a"].dns.enable_ipv6 is True


def test_source_dns_can_override_global_enable_ipv6(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://1.1.1.1:53"]
enable_ipv6 = true

[sources.airport_a.dns]
enabled = true
enable_ipv6 = false
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.dns.enable_ipv6 is True
    assert config.sources["airport_a"].dns.enable_ipv6 is False


def test_source_dns_defaults_to_disabled_with_global_defaults(
    temp_config_path: Path,
) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["tcp://8.8.8.8:53"]
timeout = "4s"
failure = "fail"
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.sources["airport_a"].dns.enabled is False
    assert config.sources["airport_a"].dns.servers == ("tcp://8.8.8.8:53",)
    assert config.sources["airport_a"].dns.timeout.total_seconds() == 4
    assert config.sources["airport_a"].dns.failure == "fail"


def test_validation_rejects_invalid_dns_config(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://127.0.0.1:53", "ftp://example.com/dns"]
failure = "explode"

[sources.airport_a.dns]
enabled = true
servers = []
failure = "panic"
"""
    )
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)
    joined = "\n".join(report.errors)

    assert not report.ok
    assert "dns server resolves to non-public address" in joined
    assert "unsupported DNS server scheme" in joined
    assert "dns failure must be" in joined
    assert "source 'airport_a' dns servers must not be empty" in joined
    assert "source 'airport_a' dns failure must be" in joined


def test_route_access_empty_user_agent_list_keeps_route_open(
    temp_config_path: Path,
) -> None:
    body = (
        minimal_config()
        + """
[routes.phone.access]
user_agent = []
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].access.user_agent == ()


# ---------------------------------------------------------------------------
# clash-config tests
# ---------------------------------------------------------------------------


def _write_template(
    directory: Path, body: str = CLASH_CONFIG_TEMPLATE_BODY
) -> Path:
    # Thin shim around the conftest helper so tests can override ``body`` for
    # edge cases such as templates missing the {{proxies}} placeholder.
    return write_clash_template(directory, body=body)


def test_clash_config_route_parses_template_path_relative_to_config(
    temp_config_path: Path,
) -> None:
    template = _write_template(temp_config_path.parent)
    body = (
        minimal_config()
        + f"""
[routes.phone.output]
format = "clash-config"
template_path = "{template.name}"
"""
    )

    config = load_config(write_config(temp_config_path, body))

    route_template = config.routes["phone"].output.template_path
    assert route_template is not None
    assert route_template.is_absolute()
    assert route_template.resolve() == template.resolve()


def test_clash_config_route_accepts_absolute_template_path(
    temp_config_path: Path,
) -> None:
    template = _write_template(temp_config_path.parent)
    body = (
        minimal_config()
        + f"""
[routes.phone.output]
format = "clash-config"
template_path = "{template}"
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].output.template_path == template


def test_clash_config_route_requires_template_path(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[routes.phone.output]
format = "clash-config"
"""
    )

    with pytest.raises(
        ValueError, match="template_path is required for clash-config output"
    ):
        load_config(write_config(temp_config_path, body))


def test_clash_config_route_rejects_missing_template_file(
    temp_config_path: Path,
) -> None:
    body = (
        minimal_config()
        + """
[routes.phone.output]
format = "clash-config"
template_path = "does-not-exist.yaml"
"""
    )

    with pytest.raises(ValueError, match="template_path does not exist"):
        load_config(write_config(temp_config_path, body))


def test_clash_config_route_rejects_template_without_placeholder(
    temp_config_path: Path,
) -> None:
    template = _write_template(
        temp_config_path.parent,
        body="port: 7890\nproxies: []\n",
    )
    body = (
        minimal_config()
        + f"""
[routes.phone.output]
format = "clash-config"
template_path = "{template.name}"
"""
    )

    with pytest.raises(
        ValueError, match="template must contain a line with only '{{proxies}}'"
    ):
        load_config(write_config(temp_config_path, body))


def test_clash_config_validation_fails_when_template_body_unreadable(
    temp_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If load_config could not cache the template body, validation must reject
    the route instead of re-reading the file, so renderer and validator stay
    aligned on the same content (the renderer only serves template_body).
    """
    template = _write_template(temp_config_path.parent)
    body = (
        minimal_config()
        + f"""
[routes.phone.output]
format = "clash-config"
template_path = "{template.name}"
"""
    )
    config_path = write_config(temp_config_path, body)

    original_read_text = Path.read_text

    def failing_read_text(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if self.name == template.name:
            raise OSError("simulated read failure")
        return original_read_text(self, encoding, errors)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    with pytest.raises(ValueError, match="template_path cannot be read"):
        load_config(config_path)


@pytest.mark.parametrize(
    "fmt", ["provider", "surfboard", "quantumult-x", "xray-uri"]
)
def test_template_path_rejected_on_non_clash_config_fixed_routes(
    temp_config_path: Path, fmt: str
) -> None:
    template = _write_template(temp_config_path.parent)
    output = f'format = "{fmt}"\ntemplate_path = "{template.name}"'

    with pytest.raises(
        ValueError,
        match="template_path is only supported for clash-config or auto output",
    ):
        load_config(_write_base_config(temp_config_path, output))


def test_auto_route_allows_optional_template_path(temp_config_path: Path) -> None:
    template = _write_template(temp_config_path.parent)
    body = """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
public_base_url = "https://mpm.example.com"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "auto"
template_path = "%s"
""" % template.name

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].output.template_path is not None


def test_auto_route_requires_template_path_when_auto_default_is_clash_config(
    temp_config_path: Path,
) -> None:
    body = """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
public_base_url = "https://mpm.example.com"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "auto"
auto_default = "clash-config"
"""

    with pytest.raises(
        ValueError,
        match="template_path is required when auto_default is 'clash-config'",
    ):
        load_config(write_config(temp_config_path, body))


def test_auto_route_with_clash_config_auto_default_and_template_passes(
    temp_config_path: Path,
) -> None:
    template = _write_template(temp_config_path.parent)
    body = """
[server]
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
public_base_url = "https://mpm.example.com"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]

[routes.phone.output]
format = "auto"
auto_default = "clash-config"
template_path = "%s"
""" % template.name

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].output.auto_default == "clash-config"
    assert config.routes["phone"].output.template_path is not None


def test_relative_template_path_cannot_escape_config_dir(
    temp_config_path: Path, tmp_path: Path
) -> None:
    """Relative template_path traversing out of the config directory must be
    rejected at load time. Operators that intentionally want a path outside
    the config directory must use an absolute path.
    """
    outside = tmp_path.parent / "outside.tpl.yaml"
    outside.write_text(CLASH_CONFIG_TEMPLATE_BODY, encoding="utf-8")

    body = (
        minimal_config()
        + """
[routes.phone.output]
format = "clash-config"
template_path = "../outside.tpl.yaml"
"""
    )

    with pytest.raises(
        ValueError, match="template_path escapes the config directory"
    ):
        load_config(write_config(temp_config_path, body))


def test_symlink_template_path_is_rejected(
    temp_config_path: Path, tmp_path: Path
) -> None:
    real_template = write_clash_template(temp_config_path.parent, name="real.yaml")
    link_path = temp_config_path.parent / "linked.yaml"
    link_path.symlink_to(real_template)

    body = (
        minimal_config()
        + """
[routes.phone.output]
format = "clash-config"
template_path = "linked.yaml"
"""
    )

    with pytest.raises(
        ValueError, match="template_path must not be a symlink"
    ):
        load_config(write_config(temp_config_path, body))


def test_validation_error_messages_do_not_leak_template_absolute_path(
    temp_config_path: Path,
) -> None:
    """Validation errors must not include the resolved absolute template path
    (avoids leaking deployment layout into logs / API errors).
    """
    body = (
        minimal_config()
        + """
[routes.phone.output]
format = "clash-config"
template_path = "does-not-exist.yaml"
"""
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(write_config(temp_config_path, body))

    message = str(excinfo.value)
    assert "template_path does not exist" in message
    assert str(temp_config_path.parent) not in message

