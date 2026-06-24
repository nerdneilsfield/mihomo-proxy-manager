"""基于 Loguru 的日志配置，自动对消息和 extra 中的敏感信息进行脱敏。

Loguru-based logging configuration with automatic secret redaction in messages and extras.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

from .models import AppConfig
from .security import redact_secret

if TYPE_CHECKING:
    from loguru import Record


def _collect_secret_values(
    config: AppConfig, *, include_route_paths: bool = True
) -> list[str]:
    """从配置中收集所有需要脱敏的敏感值。

    Collect all sensitive values from config that need redaction.

    Args:
        config: 应用配置 / Application configuration.

    Returns:
        需要脱敏的字符串列表 / List of strings that need redaction.
    """
    secrets: list[str] = []
    if config.server.status_path:
        secrets.append(config.server.status_path)
    if include_route_paths:
        secrets.extend(route.path for route in config.routes.values())
    for route in config.routes.values():
        # Clash-config templates can embed credentials and operator-controlled
        # endpoints; treat both the body and the on-disk location as secrets.
        if isinstance(route.output.template_body, str):
            secrets.append(route.output.template_body)
        if route.output.template_path is not None:
            secrets.append(str(route.output.template_path))
    for source in config.sources.values():
        secrets.append(source.url)
        secrets.extend(source.fetch.headers.values())
    for plugin in config.plugins.values():
        secrets.append(plugin.url)
        secrets.extend(plugin.headers.values())
        if isinstance(plugin.body, str):
            secrets.append(plugin.body)
    return [secret for secret in secrets if secret]


def _redact_value(value: object, secrets: list[str]) -> object:
    """递归脱敏值中的敏感信息。

    Recursively redact sensitive information from a value.

    Exception instances are rendered via ``str(value)`` and redacted; without
    this, ``logger.warning("... {error}", error=exc)`` would bypass the
    patcher because loguru only stringifies extras at format time, leaving the
    raw ``Exception`` object to be converted by the formatter after the patcher
    has already run. See security.py:redact_secret for the redaction rules.

    Args:
        value: 需要脱敏的值 / The value to redact.
        secrets: 敏感字符串列表 / List of sensitive strings.

    Returns:
        脱敏后的值 / The redacted value.
    """
    if isinstance(value, str):
        return redact_secret(value, extra_secrets=secrets)
    if isinstance(value, BaseException):
        return redact_secret(str(value), extra_secrets=secrets)
    if isinstance(value, dict):
        return {key: _redact_value(nested, secrets) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redact_record(record: "Record", secrets: list[str]) -> None:
    """对日志记录的消息和 extra 字段进行脱敏。

    Redact sensitive information from log record message and extra fields.

    Args:
        record: Loguru 日志记录 / Loguru log record.
        secrets: 敏感字符串列表 / List of sensitive strings.
    """
    record["message"] = redact_secret(str(record["message"]), extra_secrets=secrets)
    for key, value in list(record["extra"].items()):
        record["extra"][key] = _redact_value(value, secrets)


def _normal_log_filter(record: "Record") -> bool:
    return record["extra"].get("access_log") is not True


def _access_log_filter(record: "Record") -> bool:
    return record["extra"].get("access_log") is True


def configure_logging(config: AppConfig, *, debug: bool = False) -> None:
    """配置 Loguru 日志系统，包含敏感信息脱敏。

    Configure the Loguru logging system with sensitive information redaction.

    Args:
        config: 应用配置 / Application configuration.
        debug: 是否强制控制台输出为 DEBUG 级别（用于 --debug 命令行覆盖） /
            Force console level to DEBUG (for --debug CLI override).
    """
    normal_secrets = _collect_secret_values(config)
    access_secrets = _collect_secret_values(config, include_route_paths=False)
    logger.remove()
    logger.configure(
        patcher=lambda record: _redact_record(
            record,
            access_secrets
            if record["extra"].get("access_log") is True
            else normal_secrets,
        )
    )
    if config.logging_console.enabled:
        level = "DEBUG" if debug else config.logging_console.level
        logger.add(
            sys.stderr,
            level=level,
            colorize=config.logging_console.colorize,
            backtrace=True,
            diagnose=False,
            filter=_normal_log_filter,
        )
    if config.logging_file.enabled and config.logging_file.path:
        config.logging_file.path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            config.logging_file.path,
            level=config.logging_file.level,
            rotation=config.logging_file.rotation,
            retention=config.logging_file.retention,
            compression=config.logging_file.compression,
            backtrace=True,
            diagnose=False,
            filter=_normal_log_filter,
        )
    if config.access_log.enabled and config.access_log.file.enabled:
        access_file = config.access_log.file
        access_file.path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            access_file.path,
            level="INFO",
            rotation=access_file.rotation,
            retention=access_file.retention,
            compression=access_file.compression,
            backtrace=False,
            diagnose=False,
            filter=_access_log_filter,
            format="{message}",
        )
