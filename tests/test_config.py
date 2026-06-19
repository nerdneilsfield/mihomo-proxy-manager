"""配置加载、解析和验证测试。

Configuration loading, parsing, and validation tests.
"""

from pathlib import Path

import pytest

from mihomo_proxy_manager.config import (
    load_config,
    parse_duration,
    parse_file_mode,
    parse_size,
)


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
        ('format = "full-config"', "output format is unsupported"),
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
