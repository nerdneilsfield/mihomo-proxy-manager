from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

from .models import AppConfig
from .security import redact_secret

if TYPE_CHECKING:
    from loguru import Record


def _collect_secret_values(config: AppConfig) -> list[str]:
    secrets: list[str] = []
    if config.server.status_path:
        secrets.append(config.server.status_path)
    secrets.extend(route.path for route in config.routes.values())
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
    if isinstance(value, str):
        return redact_secret(value, extra_secrets=secrets)
    if isinstance(value, dict):
        return {key: _redact_value(nested, secrets) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redact_record(record: "Record", secrets: list[str]) -> None:
    record["message"] = redact_secret(str(record["message"]), extra_secrets=secrets)
    for key, value in list(record["extra"].items()):
        record["extra"][key] = _redact_value(value, secrets)


def configure_logging(config: AppConfig) -> None:
    secrets = _collect_secret_values(config)
    logger.remove()
    logger.configure(patcher=lambda record: _redact_record(record, secrets))
    if config.logging_console.enabled:
        logger.add(sys.stderr, level=config.logging_console.level, colorize=config.logging_console.colorize)
    if config.logging_file.enabled and config.logging_file.path:
        config.logging_file.path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            config.logging_file.path,
            level=config.logging_file.level,
            rotation=config.logging_file.rotation,
            retention=config.logging_file.retention,
            compression=config.logging_file.compression,
        )
