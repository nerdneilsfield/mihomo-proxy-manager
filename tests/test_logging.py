"""日志脱敏和敏感值收集测试。

Logging redaction and secret value collection tests.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from mihomo_proxy_manager.logging import _collect_secret_values, _redact_record
from mihomo_proxy_manager.models import (
    AppConfig,
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    PluginConfig,
    RefreshConfig,
    RenameConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourcePluginConfig,
)

if TYPE_CHECKING:
    from loguru import Record


def _minimal_config(tmp_path, plugins=None):
    """创建最小应用配置用于测试。

    Create a minimal app config for testing.

    Args:
        tmp_path: 临时目录路径 / Temporary directory path.
        plugins: 插件配置字典 / Plugin config dict.

    Returns:
        AppConfig: 应用配置对象 / App config object.
    """
    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timeout=timedelta(seconds=30), user_agent="ua", headers={}),
        refresh=RefreshConfig(interval=None, cron=()),
        rename=RenameConfig(),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )
    return AppConfig(
        server=ServerConfig("127.0.0.1", 8080, "Asia/Shanghai", "/healthz", None, timedelta(seconds=1)),
        cache=CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)),
        logging_console=LoggingSinkConfig(True, "INFO", True),
        logging_file=LoggingSinkConfig(False, "DEBUG"),
        http=HttpConfig(timedelta(seconds=30), "ua", 1024, 3),
        scheduler=SchedulerConfig(True, "background", timedelta(seconds=0), timedelta(seconds=1)),
        security=SecurityConfig(128, False),
        parser=ParserConfig("auto", "skip"),
        output=OutputConfig(False, False),
        sources={"airport_a": source},
        routes={"phone": RouteConfig("phone", "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", ("airport_a",), False, RouteOutputConfig(), RenameConfig(), FilterConfig())},
        plugins=plugins or {},
    )


def test_log_record_redaction_covers_message_and_extra() -> None:
    """测试日志记录脱敏覆盖消息和 extra 字段。

    Test that log record redaction covers message and extra fields.
    """
    secret_path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    record = {
        "message": f"GET {secret_path} https://x.test/sub?token=secret Authorization=Bearer abc",
        "extra": {"url": "https://x.test/sub?token=secret", "path": secret_path},
    }

    _redact_record(cast("Record", record), [secret_path])

    rendered = str(record)
    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in rendered
    assert "token=secret" not in rendered
    assert "Bearer abc" not in rendered


def test_log_record_redaction_recursively_covers_nested_extra() -> None:
    """测试日志记录脱敏递归覆盖嵌套的 extra 字段。

    Test that log record redaction recursively covers nested extra fields.
    """
    secret_path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    record = {
        "message": "ok",
        "extra": {
            "top": secret_path,
            "nested": {"deep": secret_path, "list": ["ok", secret_path]},
            "items": [{"url": secret_path}, "Bearer abc"],
        },
    }

    _redact_record(cast("Record", record), [secret_path])

    rendered = str(record)
    assert secret_path not in rendered
    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in rendered


def test_collect_secret_values_includes_plugin_body(tmp_path) -> None:
    """测试收集敏感值包含插件请求体。

    Test that secret value collection includes plugin body.
    """
    body = "sensitive-plugin-body"
    config = _minimal_config(
        tmp_path,
        plugins={
            "turn_on": PluginConfig(
                name="turn_on",
                type="http_action",
                method="POST",
                url="https://example.com/action",
                headers={},
                success_status=(200,),
                timeout=timedelta(seconds=5),
                allow_private_network=False,
                body=body,
            )
        },
    )

    secrets = _collect_secret_values(config)

    assert body in secrets
