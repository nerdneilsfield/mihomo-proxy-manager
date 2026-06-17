# Provider Upstream Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an async Python service that aggregates upstream Clash/Mihomo subscriptions into hidden Mihomo provider endpoints with source-level JSON caching.

**Architecture:** Implement a greenfield Python package with small modules for config, security, parsing, transforms, cache, fetching, plugins, refreshing, rendering, scheduler, Starlette app, and CLI. Build pure functions first, then I/O modules, then orchestration and HTTP surfaces. Keep cache reads/writes behind `SourceCacheStore` so the JSON-backed MVP can be replaced without changing parsers, transforms, or renderers.

**Tech Stack:** Python 3.11+, `uv`, `starlette`, `uvicorn`, `httpx`, `PyYAML`, `croniter`, `filelock`, `loguru`, Astral `ty`, `pytest`, `pytest-asyncio`.

---

## Source Spec

Implement against [`docs/superpowers/specs/2026-06-17-provider-upstream-service-design.md`](../specs/2026-06-17-provider-upstream-service-design.md).

## Scope Check

This is one MVP plan for a single deployable service. It includes the provider renderer only. It excludes Redis, route output persistence, management APIs, hot reload, full Clash/Mihomo config output, and download proxy support.

## File Structure

Create this structure:

```text
pyproject.toml
README.md
examples/config.toml
src/mihomo_proxy_manager/
  __init__.py
  __main__.py
  app.py
  cache.py
  cli.py
  config.py
  fetcher.py
  logging.py
  models.py
  refresher.py
  render.py
  scheduler.py
  security.py
  status.py
  transform.py
  parsers/
    __init__.py
    share_links.py
    yaml.py
  plugins/
    __init__.py
    http_action.py
tests/
  conftest.py
  test_app.py
  test_cache.py
  test_config.py
  test_fetcher.py
  test_parsers.py
  test_plugins_refresher.py
  test_render.py
  test_scheduler.py
  test_security.py
  test_transform.py
```

Responsibilities:

- `models.py`: shared dataclasses and typed result objects.
- `config.py`: TOML loading, defaults, duration/size parsing, validation aggregation.
- `security.py`: URL scheme/network checks, hidden path entropy checks, secret redaction.
- `transform.py`: name/type filters, prefix/suffix templates, duplicate name repair.
- `parsers/yaml.py`: Clash/Mihomo YAML extraction and per-type required-field validation.
- `parsers/share_links.py`: plain/base64 share-link parsing for `ss`, `vmess`, `vless`, `trojan`, and `hysteria2`.
- `cache.py`: `SourceCacheStore` interface and JSON read-through implementation with atomic writes, file mode, and file locks.
- `fetcher.py`: async `httpx` download with conditional requests, redirect safety, size limits.
- `plugins/http_action.py`: plugin registry and HTTP action implementation.
- `refresher.py`: per-source refresh pipeline, in-flight refresh de-duplication, status updates.
- `render.py`: provider YAML renderer that strips internal metadata and uses safe serialization.
- `scheduler.py`: interval/cron scheduling, jitter, startup refresh modes.
- `app.py`: Starlette app factory and route handling.
- `cli.py`: `mpm serve`, `mpm check`, and `mpm refresh`.

## Implementation Notes

- Use dataclasses, not Pydantic, to keep the dependency set tight.
- Use `yaml.safe_load` and `yaml.safe_dump` with `sort_keys` from config.
- Use `httpx.AsyncClient(follow_redirects=False)` and manually validate each redirect target.
- Keep `mpm check` offline: it validates schemes, hosts, literal IPs, references, regexes, and enum values, but never performs DNS resolution or network I/O.
- Put redirect handling, response size enforcement, and runtime DNS/private-network checks in one shared HTTP helper used by both source fetches and HTTP action plugins.
- Use `filelock.FileLock` via `asyncio.to_thread` inside async cache methods.
- Store internal source metadata under `ProxyRecord.source` rather than adding `_source` into public proxy dictionaries.
- Treat empty refreshed proxy lists as refresh failure.
- A route path is a bearer secret. Never log it raw.
- Disable uvicorn access logs by default so hidden provider paths are not logged.
- `mpm check` emits errors and warnings; errors return non-zero, warnings do not.

---

### Task 1: Project Scaffold and Tooling

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `examples/config.toml`
- Create: `src/mihomo_proxy_manager/__init__.py`
- Create: `src/mihomo_proxy_manager/__main__.py`
- Create: `src/mihomo_proxy_manager/cli.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the initial CLI smoke test**

Create `tests/test_cli_smoke.py`:

```python
from mihomo_proxy_manager.cli import build_parser


def test_build_parser_has_expected_commands() -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices

    assert {"serve", "check", "refresh"} <= set(choices)
```

- [ ] **Step 2: Run the smoke test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli_smoke.py -v
```

Expected: FAIL because `mihomo_proxy_manager` or `build_parser` does not exist.

- [ ] **Step 3: Add project metadata and dependencies**

Create `pyproject.toml`:

```toml
[project]
name = "mihomo-proxy-manager"
version = "0.1.0"
description = "Async upstream provider service for Clash/Mihomo proxy subscriptions"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "croniter>=2.0.0",
  "filelock>=3.15.0",
  "httpx>=0.27.0",
  "loguru>=0.7.2",
  "pyyaml>=6.0.2",
  "starlette>=0.37.0",
  "uvicorn[standard]>=0.30.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-asyncio>=0.23.0",
  "ty>=0.0.1a0",
]

[project.scripts]
mpm = "mihomo_proxy_manager.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mihomo_proxy_manager"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ty]
src = ["src", "tests"]
```

Create `README.md`:

````markdown
# mihomo-proxy-manager

Async provider upstream service for aggregating Clash/Mihomo subscriptions.

## Commands

```bash
mpm check -c examples/config.toml
mpm serve -c examples/config.toml
mpm refresh -c examples/config.toml airport_a
```
````

Create `examples/config.toml`:

```toml
[server]
host = "127.0.0.1"
port = 8080
timezone = "Asia/Shanghai"
health_path = "/healthz"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
route_refresh_wait = "10s"

[cache]
dir = "data/cache"
write_indent = 2
file_mode = "0600"
max_stale = "7d"

[logging.console]
enabled = true
level = "INFO"
colorize = true

[logging.file]
enabled = false
path = "logs/mihomo-proxy-manager.log"
level = "DEBUG"
rotation = "10 MB"
retention = "14 days"
compression = "gz"

[http]
timeout = "30s"
user_agent = "mihomo-proxy-manager/0.1"
max_response_size = "10 MB"
max_redirects = 3

[scheduler]
startup_refresh = true
startup_refresh_mode = "background"
jitter = "30s"
refresh_lock_timeout = "35s"

[security]
hidden_path_min_entropy_bits = 128
allow_private_network_urls = false

[parser]
default_format = "auto"
default_parse_error = "skip"

[output]
yaml_sort_keys = false
default_include_meta_comments = false

[sources.airport_a]
url = "https://example.com/sub"
format = "auto"
parse_error = "skip"

[sources.airport_a.refresh]
interval = "1h"

[sources.airport_a.rename]
prefix = "[{source}] "

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
require_all_sources = false

[routes.phone.output]
format = "provider"
include_meta_comments = false
```

Create package files:

```python
# src/mihomo_proxy_manager/__init__.py
__version__ = "0.1.0"
```

```python
# src/mihomo_proxy_manager/__main__.py
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# src/mihomo_proxy_manager/cli.py
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
```

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_proxy() -> dict[str, object]:
    return {
        "name": "HK 01",
        "type": "vmess",
        "server": "example.com",
        "port": 443,
        "uuid": "00000000-0000-0000-0000-000000000000",
        "cipher": "auto",
    }


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"
```

- [ ] **Step 4: Run smoke test and verify it passes**

Run:

```bash
uv run pytest tests/test_cli_smoke.py -v
```

Expected: PASS.

- [ ] **Step 5: Run type checker**

Run:

```bash
uv run ty check
```

Expected: PASS or no diagnostics.

- [ ] **Step 6: Commit scaffold**

```bash
git add pyproject.toml README.md examples/config.toml src/mihomo_proxy_manager tests
git commit -m "chore: scaffold python service"
```

---

### Task 2: Shared Models, Config Loading, and Validation

**Files:**
- Create: `src/mihomo_proxy_manager/models.py`
- Create: `src/mihomo_proxy_manager/config.py`
- Test: `tests/test_config.py`
- Modify: `src/mihomo_proxy_manager/cli.py`

- [ ] **Step 1: Write config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from mihomo_proxy_manager.config import load_config, parse_duration, parse_size


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
    assert config.routes["phone"].sources == ["airport_a"]


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
    joined = "\\n".join(report.errors)
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
    joined = "\\n".join(report.errors)

    assert "startup_refresh_mode" in joined
    assert "parse_error" in joined
    assert "plugin 'turn_on' type is unsupported" in joined
    assert "route 'phone' output format is unsupported" in joined
    assert "route 'phone' include regex is invalid" in joined
```

- [ ] **Step 2: Run config tests and verify they fail**

Run:

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL because `models.py` and `config.py` do not exist.

- [ ] **Step 3: Implement shared dataclasses**

Create `src/mihomo_proxy_manager/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

ProxyDict = dict[str, Any]


@dataclass(frozen=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class FilterConfig:
    include: str | None = None
    exclude: str | None = None
    include_types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenameConfig:
    prefix: str = ""
    suffix: str = ""


@dataclass(frozen=True)
class FetchConfig:
    timeout: timedelta
    user_agent: str
    headers: dict[str, str] = field(default_factory=dict)
    allow_private_network: bool = False


@dataclass(frozen=True)
class RefreshConfig:
    interval: timedelta | None = None
    cron: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginRefConfig:
    on_failure: Literal["abort", "continue"] = "abort"


@dataclass(frozen=True)
class SourcePluginConfig:
    before_fetch: dict[str, PluginRefConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    format: Literal["auto", "yaml", "share-links"]
    parse_error: Literal["skip", "fail"]
    fetch: FetchConfig
    refresh: RefreshConfig
    rename: RenameConfig
    filter: FilterConfig
    plugins: SourcePluginConfig


@dataclass(frozen=True)
class RouteOutputConfig:
    format: Literal["provider"] = "provider"
    include_meta_comments: bool = False


@dataclass(frozen=True)
class RouteConfig:
    name: str
    path: str
    sources: tuple[str, ...]
    require_all_sources: bool
    output: RouteOutputConfig
    rename: RenameConfig
    filter: FilterConfig


@dataclass(frozen=True)
class PluginConfig:
    name: str
    type: Literal["http_action"]
    method: str
    url: str
    headers: dict[str, str]
    success_status: tuple[int, ...]
    timeout: timedelta
    allow_private_network: bool
    body: str | None = None


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    timezone: str
    health_path: str
    status_path: str | None
    route_refresh_wait: timedelta


@dataclass(frozen=True)
class CacheConfig:
    dir: Path
    write_indent: int
    file_mode: int
    max_stale: timedelta


@dataclass(frozen=True)
class LoggingSinkConfig:
    enabled: bool
    level: str
    colorize: bool = False
    path: Path | None = None
    rotation: str | None = None
    retention: str | None = None
    compression: str | None = None


@dataclass(frozen=True)
class HttpConfig:
    timeout: timedelta
    user_agent: str
    max_response_size: int
    max_redirects: int


@dataclass(frozen=True)
class SchedulerConfig:
    startup_refresh: bool
    startup_refresh_mode: Literal["background", "blocking"]
    jitter: timedelta
    refresh_lock_timeout: timedelta


@dataclass(frozen=True)
class SecurityConfig:
    hidden_path_min_entropy_bits: int
    allow_private_network_urls: bool


@dataclass(frozen=True)
class ParserConfig:
    default_format: Literal["auto", "yaml", "share-links"]
    default_parse_error: Literal["skip", "fail"]


@dataclass(frozen=True)
class OutputConfig:
    yaml_sort_keys: bool
    default_include_meta_comments: bool


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    cache: CacheConfig
    logging_console: LoggingSinkConfig
    logging_file: LoggingSinkConfig
    http: HttpConfig
    scheduler: SchedulerConfig
    security: SecurityConfig
    parser: ParserConfig
    output: OutputConfig
    sources: dict[str, SourceConfig]
    routes: dict[str, RouteConfig]
    plugins: dict[str, PluginConfig]


@dataclass(frozen=True)
class ProxyRecord:
    source: str
    data: ProxyDict


@dataclass(frozen=True)
class SourceCache:
    source: str
    schema_version: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    etag: str | None
    last_modified: str | None
    node_count: int
    warnings: tuple[str, ...]
    last_error: str | None
    proxies: tuple[ProxyRecord, ...]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    node_count: int
    last_error: str | None
    refreshing: bool = False
```

- [ ] **Step 4: Implement config loading and validation**

Create `src/mihomo_proxy_manager/config.py` with these public functions and classes:

```python
from __future__ import annotations

import os
import re
import stat
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .models import (
    AppConfig,
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    PluginConfig,
    PluginRefConfig,
    RefreshConfig,
    RenameConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourcePluginConfig,
    ValidationReport,
)


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\\d+)(s|m|h|d)", value.strip())
    if not match:
        raise ValueError(f"invalid duration {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def parse_size(value: str) -> int:
    match = re.fullmatch(r"(\\d+)\\s*(B|KB|MB)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid size {value!r}")
    amount = int(match.group(1))
    unit = match.group(2).upper()
    return amount * {"B": 1, "KB": 1024, "MB": 1024 * 1024}[unit]


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def _filter(data: dict[str, Any]) -> FilterConfig:
    return FilterConfig(
        include=data.get("include"),
        exclude=data.get("exclude"),
        include_types=tuple(data.get("include_types", ())),
        exclude_types=tuple(data.get("exclude_types", ())),
    )


def _rename(data: dict[str, Any]) -> RenameConfig:
    return RenameConfig(prefix=data.get("prefix", ""), suffix=data.get("suffix", ""))


def _fetch(data: dict[str, Any], http: HttpConfig, security: SecurityConfig) -> FetchConfig:
    headers = _table(data, "headers")
    return FetchConfig(
        timeout=parse_duration(data.get("timeout", f"{int(http.timeout.total_seconds())}s")),
        user_agent=data.get("user_agent", http.user_agent),
        headers={str(k): str(v) for k, v in headers.items()},
        allow_private_network=bool(data.get("allow_private_network", security.allow_private_network_urls)),
    )


def _refresh(data: dict[str, Any]) -> RefreshConfig:
    interval = data.get("interval")
    cron = data.get("cron", ())
    return RefreshConfig(
        interval=parse_duration(interval) if interval else None,
        cron=tuple(cron),
    )


def _source_plugins(data: dict[str, Any]) -> SourcePluginConfig:
    before_fetch_table = _table(data, "before_fetch")
    before_fetch = {
        name: PluginRefConfig(on_failure=values.get("on_failure", "abort"))
        for name, values in before_fetch_table.items()
    }
    return SourcePluginConfig(before_fetch=before_fetch)


class LoadedConfig(AppConfig):
    def validate(self, config_path: Path | None = None) -> ValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        paths: dict[str, str] = {self.server.health_path: "health_path"}
        if self.server.status_path:
            if self.server.status_path in paths:
                errors.append("health_path and status_path collide")
            paths[self.server.status_path] = "status_path"
        for route in self.routes.values():
            if not route.path.startswith("/"):
                errors.append(f"route {route.name!r} path must start with '/'")
            key = f"route {route.name!r}"
            if route.path in paths:
                errors.append(f"path collision for {key} with {paths[route.path]}")
            paths[route.path] = key
            for source in route.sources:
                if source not in self.sources:
                    errors.append(f"route {route.name!r} references missing source {source!r}")
            for pattern_name, pattern in (("include", route.filter.include), ("exclude", route.filter.exclude)):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"route {route.name!r} {pattern_name} regex is invalid: {exc}")
            if route.output.format != "provider":
                errors.append(f"route {route.name!r} output format is unsupported: {route.output.format!r}")

        for source in self.sources.values():
            if source.format not in {"auto", "yaml", "share-links"}:
                errors.append(f"source {source.name!r} format is unsupported: {source.format!r}")
            if source.parse_error not in {"skip", "fail"}:
                errors.append(f"source {source.name!r} parse_error is unsupported: {source.parse_error!r}")
            for pattern_name, pattern in (("include", source.filter.include), ("exclude", source.filter.exclude)):
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"source {source.name!r} {pattern_name} regex is invalid: {exc}")
            for plugin_name in source.plugins.before_fetch:
                if plugin_name not in self.plugins:
                    errors.append(f"source {source.name!r} references missing plugin {plugin_name!r}")
            for expr in source.refresh.cron:
                if not croniter.is_valid(expr):
                    errors.append(f"source {source.name!r} cron expression is invalid: {expr!r}")
            parsed = urlparse(source.url)
            if parsed.scheme not in {"http", "https"}:
                errors.append(f"source {source.name!r} has unsupported URL scheme {parsed.scheme!r}")
            if not parsed.hostname:
                errors.append(f"source {source.name!r} URL host is required")

        for plugin in self.plugins.values():
            if plugin.type != "http_action":
                errors.append(f"plugin {plugin.name!r} type is unsupported: {plugin.type!r}")
            parsed = urlparse(plugin.url)
            if parsed.scheme not in {"http", "https"}:
                errors.append(f"plugin {plugin.name!r} has unsupported URL scheme {parsed.scheme!r}")
            if not parsed.hostname:
                errors.append(f"plugin {plugin.name!r} URL host is required")

        try:
            ZoneInfo(self.server.timezone)
        except ZoneInfoNotFoundError:
            errors.append(f"server timezone is invalid: {self.server.timezone!r}")

        if self.scheduler.startup_refresh_mode not in {"background", "blocking"}:
            errors.append(f"startup_refresh_mode is unsupported: {self.scheduler.startup_refresh_mode!r}")

        self.cache.dir.mkdir(parents=True, exist_ok=True)
        if not os.access(self.cache.dir, os.W_OK):
            errors.append(f"cache directory is not writable: {self.cache.dir}")
        if self.logging_file.enabled and self.logging_file.path:
            self.logging_file.path.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(self.logging_file.path.parent, os.W_OK):
                errors.append(f"log directory is not writable: {self.logging_file.path.parent}")

        if config_path and config_path.exists():
            mode = stat.S_IMODE(config_path.stat().st_mode)
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                warnings.append("config file is group/world-readable; use chmod 600")

        return ValidationReport(errors=errors, warnings=warnings)


def load_config(path: Path, *, validate: bool = True) -> LoadedConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed_top_level = {
        "server", "cache", "logging", "http", "scheduler", "security",
        "parser", "output", "sources", "routes", "plugins",
    }
    unknown_top_level = sorted(set(raw) - allowed_top_level)
    if unknown_top_level:
        raise ValueError("\\n".join(f"unsupported top-level table {name!r}" for name in unknown_top_level))

    server_raw = _table(raw, "server")
    cache_raw = _table(raw, "cache")
    http_raw = _table(raw, "http")
    security_raw = _table(raw, "security")
    scheduler_raw = _table(raw, "scheduler")
    parser_raw = _table(raw, "parser")
    output_raw = _table(raw, "output")
    logging_raw = _table(raw, "logging")

    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(server_raw.get("port", 8080)),
        timezone=server_raw.get("timezone", "Asia/Shanghai"),
        health_path=server_raw.get("health_path", "/healthz"),
        status_path=server_raw.get("status_path"),
        route_refresh_wait=parse_duration(server_raw.get("route_refresh_wait", "10s")),
    )
    cache = CacheConfig(
        dir=Path(cache_raw.get("dir", "data/cache")),
        write_indent=int(cache_raw.get("write_indent", 2)),
        file_mode=int(str(cache_raw.get("file_mode", "0600")), 8),
        max_stale=parse_duration(cache_raw.get("max_stale", "7d")),
    )
    http = HttpConfig(
        timeout=parse_duration(http_raw.get("timeout", "30s")),
        user_agent=http_raw.get("user_agent", "mihomo-proxy-manager/0.1"),
        max_response_size=parse_size(http_raw.get("max_response_size", "10 MB")),
        max_redirects=int(http_raw.get("max_redirects", 3)),
    )
    security = SecurityConfig(
        hidden_path_min_entropy_bits=int(security_raw.get("hidden_path_min_entropy_bits", 128)),
        allow_private_network_urls=bool(security_raw.get("allow_private_network_urls", False)),
    )
    scheduler = SchedulerConfig(
        startup_refresh=bool(scheduler_raw.get("startup_refresh", True)),
        startup_refresh_mode=scheduler_raw.get("startup_refresh_mode", "background"),
        jitter=parse_duration(scheduler_raw.get("jitter", "30s")),
        refresh_lock_timeout=parse_duration(scheduler_raw.get("refresh_lock_timeout", "35s")),
    )
    parser = ParserConfig(
        default_format=parser_raw.get("default_format", "auto"),
        default_parse_error=parser_raw.get("default_parse_error", "skip"),
    )
    output = OutputConfig(
        yaml_sort_keys=bool(output_raw.get("yaml_sort_keys", False)),
        default_include_meta_comments=bool(output_raw.get("default_include_meta_comments", False)),
    )
    console_raw = _table(logging_raw, "console")
    file_raw = _table(logging_raw, "file")
    logging_console = LoggingSinkConfig(
        enabled=bool(console_raw.get("enabled", True)),
        level=console_raw.get("level", "INFO"),
        colorize=bool(console_raw.get("colorize", True)),
    )
    logging_file = LoggingSinkConfig(
        enabled=bool(file_raw.get("enabled", True)),
        level=file_raw.get("level", "DEBUG"),
        path=Path(file_raw.get("path", "logs/mihomo-proxy-manager.log")),
        rotation=file_raw.get("rotation", "10 MB"),
        retention=file_raw.get("retention", "14 days"),
        compression=file_raw.get("compression", "gz"),
    )

    plugins = {}
    for name, values in _table(raw, "plugins").items():
        plugins[name] = PluginConfig(
            name=name,
            type=values.get("type", "http_action"),
            method=values.get("method", "GET"),
            url=values.get("url", ""),
            headers={str(k): str(v) for k, v in _table(values, "headers").items()},
            success_status=tuple(values.get("success_status", (200,))),
            timeout=parse_duration(values.get("timeout", f"{int(http.timeout.total_seconds())}s")),
            allow_private_network=bool(values.get("allow_private_network", security.allow_private_network_urls)),
            body=values.get("body"),
        )

    sources = {}
    for name, values in _table(raw, "sources").items():
        source_fetch = _fetch(_table(values, "fetch"), http, security)
        sources[name] = SourceConfig(
            name=name,
            url=values.get("url", ""),
            format=values.get("format", parser.default_format),
            parse_error=values.get("parse_error", parser.default_parse_error),
            fetch=source_fetch,
            refresh=_refresh(_table(values, "refresh")),
            rename=_rename(_table(values, "rename")),
            filter=_filter(_table(values, "filter")),
            plugins=_source_plugins(_table(values, "plugins")),
        )

    routes = {}
    for name, values in _table(raw, "routes").items():
        output_values = _table(values, "output")
        routes[name] = RouteConfig(
            name=name,
            path=values.get("path", ""),
            sources=tuple(values.get("sources", ())),
            require_all_sources=bool(values.get("require_all_sources", False)),
            output=RouteOutputConfig(
                format=output_values.get("format", "provider"),
                include_meta_comments=bool(output_values.get("include_meta_comments", output.default_include_meta_comments)),
            ),
            rename=_rename(_table(values, "rename")),
            filter=_filter(_table(values, "filter")),
        )

    config = LoadedConfig(
        server=server,
        cache=cache,
        logging_console=logging_console,
        logging_file=logging_file,
        http=http,
        scheduler=scheduler,
        security=security,
        parser=parser,
        output=output,
        sources=sources,
        routes=routes,
        plugins=plugins,
    )
    if validate:
        report = config.validate(config_path=path)
        if not report.ok:
            raise ValueError("\\n".join(report.errors))
    return config
```

- [ ] **Step 5: Wire `check` command to config validation**

Replace `src/mihomo_proxy_manager/cli.py` with:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")

    return parser


def _cmd_check(config_path: str) -> int:
    config_file = Path(config_path)
    config = load_config(config_file, validate=False)
    report = config.validate(config_path=config_file)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.ok:
        print("OK: configuration is valid")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.config)
    return 0
```

- [ ] **Step 6: Run config tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli_smoke.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit config foundation**

```bash
git add src/mihomo_proxy_manager tests/test_config.py src/mihomo_proxy_manager/cli.py
git commit -m "feat: add configuration loading and validation"
```

---

### Task 3: Security Utilities

**Files:**
- Create: `src/mihomo_proxy_manager/security.py`
- Test: `tests/test_security.py`
- Modify: `src/mihomo_proxy_manager/config.py`

- [ ] **Step 1: Write security tests**

Create `tests/test_security.py`:

```python
import pytest

from mihomo_proxy_manager.security import SecurityError, assert_safe_url, has_path_entropy, redact_secret


def test_rejects_private_network_url() -> None:
    with pytest.raises(SecurityError):
        assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=False)


def test_allows_private_network_when_opted_in() -> None:
    assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=True)


def test_rejects_unsupported_scheme() -> None:
    with pytest.raises(SecurityError):
        assert_safe_url("ftp://example.com/sub", allow_private_network=False)


def test_hidden_path_entropy() -> None:
    assert has_path_entropy("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", min_bits=128)
    assert not has_path_entropy("/p/short.yaml", min_bits=128)


def test_redact_secret() -> None:
    text = "GET /p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml https://x.test/sub?token=secret Authorization=Bearer abc"
    redacted = redact_secret(text, extra_secrets=["/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"])

    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in redacted
    assert "token=secret" not in redacted
    assert "Bearer abc" not in redacted
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_security.py -v
```

Expected: FAIL because `security.py` does not exist.

- [ ] **Step 3: Implement security utilities**

Create `src/mihomo_proxy_manager/security.py`:

```python
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class SecurityError(ValueError):
    pass


_SECRET_QUERY_KEYS = {"token", "secret", "key", "apikey", "api_key", "access_token"}
_BEARER_RE = re.compile(r"Bearer\\s+[A-Za-z0-9._~+\\-/=]+")


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({ipaddress.ip_address(info[4][0]) for info in infos}, key=str)


_BASE64URL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def assert_safe_url(url: str, *, allow_private_network: bool, resolve_dns: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError(f"unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise SecurityError("URL host is required")
    if allow_private_network:
        return
    try:
        ips = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        if not resolve_dns:
            return
        ips = _resolve_host(parsed.hostname)
    for ip in ips:
        if not _is_public_ip(ip):
            raise SecurityError(f"URL resolves to non-public address: {ip}")


def has_path_entropy(path: str, *, min_bits: int) -> bool:
    token = path.rsplit("/", 1)[-1].split(".", 1)[0]
    if not token or not _BASE64URL_TOKEN_RE.fullmatch(token):
        return False
    return len(token) * 6 >= min_bits


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "***" if key.lower() in _SECRET_QUERY_KEYS else value))
    return urlunparse(parsed._replace(query=urlencode(query)))


def redact_secret(text: str, *, extra_secrets: list[str] | None = None) -> str:
    redacted = _BEARER_RE.sub("Bearer ***", text)
    redacted = re.sub(r"(?i)(Authorization=)([^\\s]+)(\\s+Bearer\\s+[^\\s]+)?", r"\\1***", redacted)
    redacted = re.sub(r"([?&](?:token|secret|key|apikey|api_key|access_token)=)[^&\\s]+", r"\\1***", redacted)
    for secret in extra_secrets or []:
        redacted = redacted.replace(secret, "***")
    return redacted
```

- [ ] **Step 4: Use entropy and URL safety in config validation**

Modify `src/mihomo_proxy_manager/config.py` imports:

```python
from .security import SecurityError, assert_safe_url, has_path_entropy
```

Inside `LoadedConfig.validate`, after route path starts-with slash check:

```python
            if not has_path_entropy(route.path, min_bits=self.security.hidden_path_min_entropy_bits):
                errors.append(f"route {route.name!r} path does not satisfy hidden path entropy requirement")
```

After status path collision check:

```python
        if self.server.status_path and not has_path_entropy(
            self.server.status_path,
            min_bits=self.security.hidden_path_min_entropy_bits,
        ):
            errors.append("status_path does not satisfy hidden path entropy requirement")
```

Replace URL scheme checks with:

```python
            try:
                assert_safe_url(source.url, allow_private_network=source.fetch.allow_private_network, resolve_dns=False)
            except SecurityError as exc:
                errors.append(f"source {source.name!r} URL is unsafe: {exc}")
```

Add plugin URL checks:

```python
        for plugin in self.plugins.values():
            try:
                assert_safe_url(plugin.url, allow_private_network=plugin.allow_private_network, resolve_dns=False)
            except SecurityError as exc:
                errors.append(f"plugin {plugin.name!r} URL is unsafe: {exc}")
```

- [ ] **Step 5: Run security and config tests**

Run:

```bash
uv run pytest tests/test_security.py tests/test_config.py -v
```

Expected: PASS. If `test_validation_collects_multiple_errors` now reports the route entropy error too, keep the existing assertions and allow extra errors.

- [ ] **Step 6: Commit security utilities**

```bash
git add src/mihomo_proxy_manager/security.py src/mihomo_proxy_manager/config.py tests/test_security.py tests/test_config.py
git commit -m "feat: add URL safety and secret redaction"
```

---

### Task 4: Transform Pipeline

**Files:**
- Create: `src/mihomo_proxy_manager/transform.py`
- Test: `tests/test_transform.py`

- [ ] **Step 1: Write transform tests**

Create `tests/test_transform.py`:

```python
from mihomo_proxy_manager.models import FilterConfig, ProxyRecord, RenameConfig
from mihomo_proxy_manager.transform import apply_transform, repair_duplicate_names


def records() -> list[ProxyRecord]:
    return [
        ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"}),
        ProxyRecord("airport_a", {"name": "JP 01", "type": "ss"}),
        ProxyRecord("airport_a", {"name": "官网", "type": "http"}),
    ]


def test_filters_by_name_and_type() -> None:
    result = apply_transform(
        records(),
        filter_config=FilterConfig(include="HK|JP", exclude="官网", exclude_types=("http",)),
        rename_config=RenameConfig(),
    )

    assert [item.data["name"] for item in result] == ["HK 01", "JP 01"]


def test_renames_with_source_template() -> None:
    result = apply_transform(
        [ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"})],
        filter_config=FilterConfig(),
        rename_config=RenameConfig(prefix="[{source}] ", suffix=" | auto"),
    )

    assert result[0].data["name"] == "[airport_a] HK 01 | auto"
    assert result[0].source == "airport_a"


def test_duplicate_name_repair_is_iterative() -> None:
    result = repair_duplicate_names(
        [
            ProxyRecord("a", {"name": "HK", "type": "vmess"}),
            ProxyRecord("b", {"name": "HK", "type": "vmess"}),
            ProxyRecord("c", {"name": "HK #2", "type": "vmess"}),
        ]
    )

    assert [item.data["name"] for item in result] == ["HK", "HK #3", "HK #2"]
```

- [ ] **Step 2: Run transform tests and verify they fail**

Run:

```bash
uv run pytest tests/test_transform.py -v
```

Expected: FAIL because `transform.py` does not exist.

- [ ] **Step 3: Implement transform functions**

Create `src/mihomo_proxy_manager/transform.py`:

```python
from __future__ import annotations

import re
from copy import deepcopy

from .models import FilterConfig, ProxyRecord, RenameConfig


def _matches_name(name: str, pattern: str | None) -> bool:
    return bool(pattern and re.search(pattern, name))


def _matches_type(proxy_type: str, types: tuple[str, ...]) -> bool:
    wanted = {item.lower() for item in types}
    return proxy_type.lower() in wanted


def _kept(record: ProxyRecord, config: FilterConfig) -> bool:
    name = str(record.data.get("name", ""))
    proxy_type = str(record.data.get("type", ""))
    if config.include and not _matches_name(name, config.include):
        return False
    if config.exclude and _matches_name(name, config.exclude):
        return False
    if config.include_types and not _matches_type(proxy_type, config.include_types):
        return False
    if config.exclude_types and _matches_type(proxy_type, config.exclude_types):
        return False
    return True


def _render_template(value: str, record: ProxyRecord) -> str:
    return value.replace("{source}", record.source)


def apply_transform(
    records: list[ProxyRecord],
    *,
    filter_config: FilterConfig,
    rename_config: RenameConfig,
) -> list[ProxyRecord]:
    output: list[ProxyRecord] = []
    for record in records:
        if not _kept(record, filter_config):
            continue
        data = deepcopy(record.data)
        old_name = str(data.get("name", ""))
        prefix = _render_template(rename_config.prefix, record)
        suffix = _render_template(rename_config.suffix, record)
        data["name"] = f"{prefix}{old_name}{suffix}"
        output.append(ProxyRecord(source=record.source, data=data))
    return output


def repair_duplicate_names(records: list[ProxyRecord]) -> list[ProxyRecord]:
    used: set[str] = set()
    output: list[ProxyRecord] = []
    for record in records:
        data = deepcopy(record.data)
        base = str(data.get("name", ""))
        candidate = base
        counter = 2
        while candidate in used:
            candidate = f"{base} #{counter}"
            counter += 1
        data["name"] = candidate
        used.add(candidate)
        output.append(ProxyRecord(source=record.source, data=data))
    return output
```

- [ ] **Step 4: Run transform tests**

Run:

```bash
uv run pytest tests/test_transform.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit transform pipeline**

```bash
git add src/mihomo_proxy_manager/transform.py tests/test_transform.py
git commit -m "feat: add proxy transform pipeline"
```

---

### Task 5: YAML and Share-Link Parsers

**Files:**
- Create: `src/mihomo_proxy_manager/parsers/__init__.py`
- Create: `src/mihomo_proxy_manager/parsers/yaml.py`
- Create: `src/mihomo_proxy_manager/parsers/share_links.py`
- Test: `tests/test_parsers.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_parsers.py`:

```python
import base64
import json

import pytest

from mihomo_proxy_manager.parsers import ParseError, parse_subscription
from mihomo_proxy_manager.parsers.yaml import validate_required_fields


def test_parse_yaml_provider_payload() -> None:
    body = b"""
proxies:
  - name: HK 01
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
"""
    result = parse_subscription(body, source="airport_a", fmt="yaml", parse_error="fail")

    assert result.warnings == []
    assert result.records[0].source == "airport_a"
    assert result.records[0].data["name"] == "HK 01"


def test_parse_yaml_full_config() -> None:
    body = b"""
port: 7890
proxies:
  - name: JP 01
    type: ss
    server: example.com
    port: 443
    cipher: chacha20-ietf-poly1305
    password: secret
"""
    result = parse_subscription(body, source="airport_a", fmt="auto", parse_error="fail")

    assert result.records[0].data["type"] == "ss"


def test_required_field_validation() -> None:
    missing = validate_required_fields({"name": "bad", "type": "vmess", "server": "x"})
    assert "missing required field" in missing[0]


def test_plain_share_links() -> None:
    body = b"trojan://password@example.com:443?sni=example.com#TR%2001\\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["name"] == "TR 01"
    assert result.records[0].data["type"] == "trojan"
    assert result.records[0].data["password"] == "password"


def test_ss_sip002_share_link() -> None:
    body = b"ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpzZWNyZXQ@example.com:443#SS%2001\\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    proxy = result.records[0].data
    assert proxy["type"] == "ss"
    assert proxy["cipher"] == "chacha20-ietf-poly1305"
    assert proxy["password"] == "secret"
    assert proxy["server"] == "example.com"


def test_vless_share_link() -> None:
    body = b"vless://00000000-0000-0000-0000-000000000000@example.com:443?encryption=none&security=tls&sni=example.com#VL%2001\\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["type"] == "vless"
    assert result.records[0].data["uuid"] == "00000000-0000-0000-0000-000000000000"


def test_hysteria2_share_link() -> None:
    body = b"hysteria2://password@example.com:443?sni=example.com#HY2%2001\\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="fail")

    assert result.records[0].data["type"] == "hysteria2"
    assert result.records[0].data["password"] == "password"


def test_base64_share_links() -> None:
    vmess = {
        "v": "2",
        "ps": "VM 01",
        "add": "example.com",
        "port": "443",
        "id": "00000000-0000-0000-0000-000000000000",
        "aid": "0",
        "scy": "auto",
        "tls": "tls",
    }
    link = "vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode()
    encoded = base64.b64encode(link.encode())

    result = parse_subscription(encoded, source="airport_a", fmt="auto", parse_error="fail")

    assert result.records[0].data["type"] == "vmess"
    assert result.records[0].data["name"] == "VM 01"


def test_parse_error_skip_bad_nodes() -> None:
    body = b"not-a-node\\ntrojan://password@example.com:443#TR%2001\\n"
    result = parse_subscription(body, source="airport_a", fmt="share-links", parse_error="skip")

    assert len(result.records) == 1
    assert result.warnings


def test_parse_error_fail_bad_nodes() -> None:
    with pytest.raises(ParseError):
        parse_subscription(b"not-a-node\\n", source="airport_a", fmt="share-links", parse_error="fail")
```

- [ ] **Step 2: Run parser tests and verify they fail**

Run:

```bash
uv run pytest tests/test_parsers.py -v
```

Expected: FAIL because parser modules do not exist.

- [ ] **Step 3: Implement YAML parser**

Create `src/mihomo_proxy_manager/parsers/yaml.py`:

```python
from __future__ import annotations

from typing import Any

import yaml

from mihomo_proxy_manager.models import ProxyRecord

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ss": ("server", "port", "cipher", "password"),
    "vmess": ("server", "port", "uuid", "cipher"),
    "vless": ("server", "port", "uuid"),
    "trojan": ("server", "port", "password"),
    "hysteria2": ("server", "port", "password"),
    "hy2": ("server", "port", "password"),
    "http": ("server", "port"),
    "socks5": ("server", "port"),
}


def validate_required_fields(proxy: dict[str, Any]) -> list[str]:
    proxy_type = str(proxy.get("type", "")).lower()
    warnings: list[str] = []
    for field in REQUIRED_FIELDS.get(proxy_type, ("name", "type")):
        if field not in proxy or proxy[field] in (None, ""):
            warnings.append(f"proxy {proxy.get('name', '<unnamed>')!r} missing required field {field!r}")
    if "name" not in proxy or "type" not in proxy:
        warnings.append("proxy missing required field 'name' or 'type'")
    return warnings


def parse_yaml_subscription(body: bytes, *, source: str) -> tuple[list[ProxyRecord], list[str]]:
    loaded = yaml.safe_load(body.decode("utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError("YAML subscription must be a mapping")
    proxies = loaded.get("proxies")
    if not isinstance(proxies, list):
        raise ValueError("YAML subscription has no proxies list")

    records: list[ProxyRecord] = []
    warnings: list[str] = []
    for item in proxies:
        if not isinstance(item, dict):
            warnings.append("proxy entry is not a mapping")
            continue
        proxy = dict(item)
        item_warnings = validate_required_fields(proxy)
        warnings.extend(item_warnings)
        if not item_warnings:
            records.append(ProxyRecord(source=source, data=proxy))
    return records, warnings
```

- [ ] **Step 4: Implement share-link parser and dispatch**

Create `src/mihomo_proxy_manager/parsers/share_links.py`:

```python
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, unquote, urlparse

from mihomo_proxy_manager.models import ProxyRecord
from mihomo_proxy_manager.parsers.yaml import validate_required_fields


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode())


def _name(fragment: str, fallback: str) -> str:
    return unquote(fragment) if fragment else fallback


def _parse_vmess(link: str) -> dict[str, object]:
    raw = link.removeprefix("vmess://")
    data = json.loads(_b64decode(raw))
    proxy = {
        "name": data.get("ps") or data.get("add") or "vmess",
        "type": "vmess",
        "server": data.get("add"),
        "port": int(data.get("port", 0)),
        "uuid": data.get("id"),
        "alterId": int(data.get("aid", 0)),
        "cipher": data.get("scy") or data.get("cipher") or "auto",
    }
    if data.get("tls"):
        proxy["tls"] = data.get("tls") == "tls"
    if data.get("net"):
        proxy["network"] = data.get("net")
    if data.get("host") or data.get("path"):
        proxy["ws-opts"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}}
    return proxy


def _parse_ss(link: str) -> dict[str, object]:
    parsed = urlparse(link)
    if parsed.hostname and parsed.username:
        userinfo = unquote(parsed.username)
        try:
            decoded = _b64decode(userinfo).decode()
        except Exception:
            decoded = userinfo
        cipher, password = decoded.split(":", 1)
        return {
            "name": _name(parsed.fragment, parsed.hostname),
            "type": "ss",
            "server": parsed.hostname,
            "port": parsed.port,
            "cipher": cipher,
            "password": password,
        }
    raw = link.removeprefix("ss://").split("#", 1)[0]
    decoded = _b64decode(raw).decode()
    method_password, endpoint = decoded.rsplit("@", 1)
    cipher, password = method_password.split(":", 1)
    server, port = endpoint.rsplit(":", 1)
    return {
        "name": _name(parsed.fragment, server),
        "type": "ss",
        "server": server,
        "port": int(port),
        "cipher": cipher,
        "password": password,
    }


def _parse_url_link(link: str) -> dict[str, object]:
    if link.startswith("ss://"):
        return _parse_ss(link)
    parsed = urlparse(link)
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    scheme = parsed.scheme.lower()
    proxy_type = "hysteria2" if scheme in {"hysteria2", "hy2"} else scheme
    proxy: dict[str, object] = {
        "name": _name(parsed.fragment, parsed.hostname or proxy_type),
        "type": proxy_type,
        "server": parsed.hostname,
        "port": parsed.port,
    }
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")

    if scheme == "vless":
        proxy.update({"uuid": username, "encryption": query.get("encryption", "none")})
    elif scheme == "trojan":
        proxy.update({"password": username})
    elif scheme in {"hysteria2", "hy2"}:
        proxy.update({"password": username or password})

    if "sni" in query:
        proxy["sni"] = query["sni"]
    if "alpn" in query:
        proxy["alpn"] = query["alpn"].split(",")
    if "security" in query:
        proxy["security"] = query["security"]
    if "flow" in query:
        proxy["flow"] = query["flow"]
    if "allowInsecure" in query or "insecure" in query:
        proxy["skip-cert-verify"] = query.get("allowInsecure", query.get("insecure")) in {"1", "true", "True"}
    return proxy


def parse_share_links_text(text: str, *, source: str) -> tuple[list[ProxyRecord], list[str]]:
    records: list[ProxyRecord] = []
    warnings: list[str] = []
    for line in (item.strip() for item in text.splitlines()):
        if not line:
            continue
        try:
            if line.startswith("vmess://"):
                proxy = _parse_vmess(line)
            elif line.startswith(("ss://", "vless://", "trojan://", "hysteria2://", "hy2://")):
                proxy = _parse_url_link(line)
            else:
                raise ValueError("unsupported share link")
            item_warnings = validate_required_fields(proxy)
            if item_warnings:
                warnings.extend(item_warnings)
                continue
            records.append(ProxyRecord(source=source, data=proxy))
        except Exception as exc:
            warnings.append(f"failed to parse share link {line[:16]!r}: {exc}")
    return records, warnings
```

Create `src/mihomo_proxy_manager/parsers/__init__.py`:

```python
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

from mihomo_proxy_manager.models import ProxyRecord

from .share_links import parse_share_links_text
from .yaml import parse_yaml_subscription


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParseResult:
    records: list[ProxyRecord]
    warnings: list[str]


def _decode_text(body: bytes) -> str:
    return body.decode("utf-8-sig")


def _try_base64_text(body: bytes) -> str:
    raw = body.strip()
    padding = b"=" * (-len(raw) % 4)
    return base64.b64decode(raw + padding).decode("utf-8-sig")


def _finalize(records: list[ProxyRecord], warnings: list[str], *, parse_error: Literal["skip", "fail"]) -> ParseResult:
    if parse_error == "fail" and warnings:
        raise ParseError("; ".join(warnings))
    if not records:
        raise ParseError("; ".join(warnings) if warnings else "no usable proxies")
    return ParseResult(records=records, warnings=warnings)


def parse_subscription(
    body: bytes,
    *,
    source: str,
    fmt: Literal["auto", "yaml", "share-links"],
    parse_error: Literal["skip", "fail"],
) -> ParseResult:
    if fmt in {"auto", "yaml"}:
        try:
            records, warnings = parse_yaml_subscription(body, source=source)
            return _finalize(records, warnings, parse_error=parse_error)
        except Exception:
            if fmt == "yaml":
                raise

    if fmt in {"auto", "share-links"}:
        records, warnings = parse_share_links_text(_decode_text(body), source=source)
        if records or fmt == "share-links":
            return _finalize(records, warnings, parse_error=parse_error)

    if fmt == "auto":
        records, warnings = parse_share_links_text(_try_base64_text(body), source=source)
        return _finalize(records, warnings, parse_error=parse_error)

    raise ParseError("unsupported subscription format")
```

- [ ] **Step 5: Run parser tests**

Run:

```bash
uv run pytest tests/test_parsers.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit parsers**

```bash
git add src/mihomo_proxy_manager/parsers tests/test_parsers.py
git commit -m "feat: parse mihomo yaml and share links"
```

---

### Task 6: JSON Source Cache Store

**Files:**
- Create: `src/mihomo_proxy_manager/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write cache tests**

Create `tests/test_cache.py`:

```python
from datetime import UTC, datetime

import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.models import CacheConfig, ProxyRecord, SourceCache


@pytest.mark.asyncio
async def test_cache_roundtrip_and_permissions(tmp_path) -> None:
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))
    cache = SourceCache(
        source="airport_a",
        schema_version=1,
        last_attempt_at=datetime(2026, 6, 17, tzinfo=UTC),
        last_success_at=datetime(2026, 6, 17, tzinfo=UTC),
        etag='"abc"',
        last_modified=None,
        node_count=1,
        warnings=("warn",),
        last_error=None,
        proxies=(ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
    )

    await store.set("airport_a", cache)
    loaded = await store.get("airport_a")

    assert loaded == cache
    assert (tmp_path / "airport_a.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_unknown_schema_version_is_rejected(tmp_path) -> None:
    path = tmp_path / "airport_a.json"
    path.write_text('{"schema_version": 99, "source": "airport_a"}', encoding="utf-8")
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, max_stale=__import__("datetime").timedelta(days=7)))

    with pytest.raises(ValueError):
        await store.get("airport_a")
```

- [ ] **Step 2: Run cache tests and verify they fail**

Run:

```bash
uv run pytest tests/test_cache.py -v
```

Expected: FAIL because `cache.py` does not exist.

- [ ] **Step 3: Implement JSON cache store**

Create `src/mihomo_proxy_manager/cache.py`:

```python
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from filelock import FileLock

from .models import CacheConfig, ProxyRecord, SourceCache, SourceStatus

CURRENT_SCHEMA_VERSION = 1


class SourceCacheStore(Protocol):
    async def get(self, source_name: str) -> SourceCache | None: ...
    async def set(self, source_name: str, cache: SourceCache) -> None: ...
    async def status(self, source_name: str) -> SourceStatus: ...


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _dt_s(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class JsonSourceCacheStore:
    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self.config.dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, SourceCache] = {}
        self._refreshing: set[str] = set()

    def _path(self, source_name: str) -> Path:
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.json"

    def _lock_path(self, source_name: str) -> Path:
        safe_name = quote(source_name, safe="")
        return self.config.dir / f"{safe_name}.lock"

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        if refreshing:
            self._refreshing.add(source_name)
        else:
            self._refreshing.discard(source_name)

    async def get(self, source_name: str) -> SourceCache | None:
        if source_name in self._memory:
            return self._memory[source_name]
        path = self._path(source_name)
        if not path.exists():
            return None
        cache = await asyncio.to_thread(self._read_file, path)
        self._memory[source_name] = cache
        return cache

    async def set(self, source_name: str, cache: SourceCache) -> None:
        await asyncio.to_thread(self._write_file, source_name, cache)
        self._memory[source_name] = cache

    async def status(self, source_name: str) -> SourceStatus:
        cache = await self.get(source_name)
        if cache is None:
            return SourceStatus(source_name, None, None, 0, "no cache", source_name in self._refreshing)
        return SourceStatus(
            source=source_name,
            last_attempt_at=cache.last_attempt_at,
            last_success_at=cache.last_success_at,
            node_count=cache.node_count,
            last_error=cache.last_error,
            refreshing=source_name in self._refreshing,
        )

    def _read_file(self, path: Path) -> SourceCache:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported cache schema version: {data.get('schema_version')}")
        proxies = tuple(ProxyRecord(item["source"], item["data"]) for item in data.get("proxies", ()))
        return SourceCache(
            source=data["source"],
            schema_version=data["schema_version"],
            last_attempt_at=_dt(data.get("last_attempt_at")),
            last_success_at=_dt(data.get("last_success_at")),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            node_count=int(data.get("node_count", len(proxies))),
            warnings=tuple(data.get("warnings", ())),
            last_error=data.get("last_error"),
            proxies=proxies,
        )

    def _to_json(self, cache: SourceCache) -> dict[str, Any]:
        return {
            "source": cache.source,
            "schema_version": cache.schema_version,
            "last_attempt_at": _dt_s(cache.last_attempt_at),
            "last_success_at": _dt_s(cache.last_success_at),
            "etag": cache.etag,
            "last_modified": cache.last_modified,
            "node_count": cache.node_count,
            "warnings": list(cache.warnings),
            "last_error": cache.last_error,
            "proxies": [{"source": record.source, "data": record.data} for record in cache.proxies],
        }

    def _write_file(self, source_name: str, cache: SourceCache) -> None:
        path = self._path(source_name)
        tmp = path.with_suffix(".json.tmp")
        lock = FileLock(str(self._lock_path(source_name)))
        with lock:
            tmp.write_text(json.dumps(self._to_json(cache), ensure_ascii=False, indent=self.config.write_indent), encoding="utf-8")
            os.chmod(tmp, self.config.file_mode)
            os.replace(tmp, path)
            os.chmod(path, self.config.file_mode)
```

- [ ] **Step 4: Run cache tests**

Run:

```bash
uv run pytest tests/test_cache.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit cache store**

```bash
git add src/mihomo_proxy_manager/cache.py tests/test_cache.py
git commit -m "feat: add json source cache store"
```

---

### Task 7: Async Fetcher and HTTP Action Plugin

**Files:**
- Create: `src/mihomo_proxy_manager/fetcher.py`
- Create: `src/mihomo_proxy_manager/plugins/__init__.py`
- Create: `src/mihomo_proxy_manager/plugins/http_action.py`
- Test: `tests/test_fetcher.py`
- Test: `tests/test_plugins_refresher.py`

- [ ] **Step 1: Write fetcher tests with a fake transport**

Create `tests/test_fetcher.py`:

```python
import httpx
import pytest

from mihomo_proxy_manager.fetcher import FetchResult, SubscriptionFetcher
from mihomo_proxy_manager.models import FetchConfig, HttpConfig


@pytest.mark.asyncio
async def test_fetch_sends_conditional_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"abc"'
        assert request.headers["If-Modified-Since"] == "Wed, 17 Jun 2026 04:00:00 GMT"
        return httpx.Response(304)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))
    result = await fetcher.fetch(
        "https://93.184.216.34/sub",
        FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False),
        etag='"abc"',
        last_modified="Wed, 17 Jun 2026 04:00:00 GMT",
    )

    assert result.not_modified is True


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1025)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))

    with pytest.raises(ValueError):
        await fetcher.fetch("https://93.184.216.34/sub", FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False))


@pytest.mark.asyncio
async def test_fetch_revalidates_redirect_target() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/sub"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = SubscriptionFetcher(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3))

    with pytest.raises(ValueError):
        await fetcher.fetch("https://93.184.216.34/sub", FetchConfig(__import__("datetime").timedelta(seconds=30), "ua", {}, False))
```

- [ ] **Step 2: Write plugin tests**

Add to `tests/test_plugins_refresher.py`:

```python
import httpx
import pytest

from mihomo_proxy_manager.models import HttpConfig, PluginConfig
from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.plugins.http_action import HttpActionPlugin, PluginContext


@pytest.mark.asyncio
async def test_http_action_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plugin = HttpActionPlugin(SafeHttpClient(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)))
    config = PluginConfig(
        name="turn_on",
        type="http_action",
        method="POST",
        url="https://93.184.216.34/switch",
        headers={},
        success_status=(204,),
        timeout=__import__("datetime").timedelta(seconds=10),
        allow_private_network=False,
    )

    result = await plugin.run(PluginContext(source_name="airport_a", plugin=config))

    assert result.ok
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_fetcher.py tests/test_plugins_refresher.py -v
```

Expected: FAIL because fetcher and plugin modules do not exist.

- [ ] **Step 4: Implement fetcher**

Create `src/mihomo_proxy_manager/fetcher.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from .models import FetchConfig, HttpConfig
from .security import assert_safe_url


@dataclass(frozen=True)
class FetchResult:
    body: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class SafeHttpClient:
    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
        self.client = client
        self.http_config = http_config

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        allow_private_network: bool,
        body: bytes | str | None = None,
    ) -> httpx.Response:
        current = url
        for _ in range(self.http_config.max_redirects + 1):
            assert_safe_url(current, allow_private_network=allow_private_network, resolve_dns=True)
            async with self.client.stream(
                method,
                current,
                headers=headers,
                content=body,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("redirect response missing Location")
                    current = urljoin(current, location)
                    continue
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.http_config.max_response_size:
                        raise ValueError("upstream response exceeds max_response_size")
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=bytes(content),
                    request=response.request,
                )
        raise ValueError("too many redirects")


class SubscriptionFetcher:
    def __init__(self, client: httpx.AsyncClient, http_config: HttpConfig) -> None:
        self.safe_http = SafeHttpClient(client, http_config)

    async def fetch(
        self,
        url: str,
        fetch_config: FetchConfig,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        headers = dict(fetch_config.headers)
        headers.setdefault("User-Agent", fetch_config.user_agent)
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        response = await self.safe_http.request(
            "GET",
            url,
            headers=headers,
            timeout=fetch_config.timeout.total_seconds(),
            allow_private_network=fetch_config.allow_private_network,
        )
        if response.status_code == 304:
            return FetchResult(None, etag, last_modified, True)
        response.raise_for_status()
        return FetchResult(response.content, response.headers.get("ETag"), response.headers.get("Last-Modified"))
```

- [ ] **Step 5: Implement HTTP action plugin**

Create `src/mihomo_proxy_manager/plugins/http_action.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.models import PluginConfig


@dataclass(frozen=True)
class PluginContext:
    source_name: str
    plugin: PluginConfig


@dataclass(frozen=True)
class PluginResult:
    ok: bool
    message: str | None = None


class HttpActionPlugin:
    def __init__(self, safe_http: SafeHttpClient) -> None:
        self.safe_http = safe_http

    async def run(self, context: PluginContext) -> PluginResult:
        plugin = context.plugin
        try:
            response = await self.safe_http.request(
                plugin.method,
                plugin.url,
                headers=plugin.headers,
                timeout=plugin.timeout.total_seconds(),
                allow_private_network=plugin.allow_private_network,
                body=plugin.body,
            )
            if response.status_code not in plugin.success_status:
                return PluginResult(False, f"unexpected status {response.status_code}")
            return PluginResult(True)
        except Exception as exc:
            return PluginResult(False, str(exc))
```

Create `src/mihomo_proxy_manager/plugins/__init__.py`:

```python
from .http_action import HttpActionPlugin, PluginContext, PluginResult

__all__ = ["HttpActionPlugin", "PluginContext", "PluginResult"]
```

- [ ] **Step 6: Run fetcher and plugin tests**

Run:

```bash
uv run pytest tests/test_fetcher.py tests/test_plugins_refresher.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit fetcher and plugin**

```bash
git add src/mihomo_proxy_manager/fetcher.py src/mihomo_proxy_manager/plugins tests/test_fetcher.py tests/test_plugins_refresher.py
git commit -m "feat: add async fetcher and http action plugin"
```

---

### Task 8: Source Refresher

**Files:**
- Create: `src/mihomo_proxy_manager/refresher.py`
- Modify: `tests/test_plugins_refresher.py`

- [ ] **Step 1: Add refresher tests**

Append to `tests/test_plugins_refresher.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.models import (
    AppConfig,
    CacheConfig,
    FetchConfig,
    FilterConfig,
    HttpConfig,
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
    ValidationReport,
)
from mihomo_proxy_manager.refresher import SourceRefresher


class StaticFetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    async def fetch(self, *args, **kwargs):
        from mihomo_proxy_manager.fetcher import FetchResult

        self.calls += 1
        return FetchResult(self.body, '"etag"', "Wed, 17 Jun 2026 04:00:00 GMT")


def source_config() -> SourceConfig:
    return SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="yaml",
        parse_error="fail",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
        refresh=RefreshConfig(),
        rename=RenameConfig(prefix="[{source}] "),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
    )


@pytest.mark.asyncio
async def test_refresher_writes_cache(tmp_path) -> None:
    body = b'''
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
'''
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=StaticFetcher(body),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    result = await refresher.refresh("airport_a")
    cache = await store.get("airport_a")

    assert result.ok
    assert cache is not None
    assert cache.proxies[0].data["name"] == "[airport_a] HK"


@pytest.mark.asyncio
async def test_refresher_shares_inflight_refresh(tmp_path) -> None:
    body = b'''
proxies:
  - name: HK
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    cipher: auto
'''
    store = JsonSourceCacheStore(CacheConfig(tmp_path, 2, 0o600, timedelta(days=7)))
    fetcher = StaticFetcher(body)
    refresher = SourceRefresher(
        sources={"airport_a": source_config()},
        plugins={},
        cache_store=store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=1),
    )

    first, second = await asyncio.gather(refresher.refresh("airport_a"), refresher.refresh("airport_a"))

    assert first.ok
    assert second.ok
    assert fetcher.calls == 1
```

- [ ] **Step 2: Run refresher test and verify it fails**

Run:

```bash
uv run pytest tests/test_plugins_refresher.py::test_refresher_writes_cache -v
```

Expected: FAIL because `refresher.py` does not exist.

- [ ] **Step 3: Implement source refresher**

Create `src/mihomo_proxy_manager/refresher.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .cache import CURRENT_SCHEMA_VERSION, SourceCacheStore
from .models import PluginConfig, SourceCache, SourceConfig
from .parsers import ParseError, parse_subscription
from .plugins.http_action import HttpActionPlugin, PluginContext
from .security import redact_secret
from .transform import apply_transform


@dataclass(frozen=True)
class RefreshResult:
    ok: bool
    source: str
    node_count: int = 0
    warning_count: int = 0
    cache_path: str | None = None
    error: str | None = None


class SourceRefresher:
    def __init__(
        self,
        *,
        sources: dict[str, SourceConfig],
        plugins: dict[str, PluginConfig],
        cache_store: SourceCacheStore,
        fetcher: Any,
        http_plugin: HttpActionPlugin | None,
        refresh_lock_timeout: timedelta,
    ) -> None:
        self.sources = sources
        self.plugins = plugins
        self.cache_store = cache_store
        self.fetcher = fetcher
        self.http_plugin = http_plugin
        self.refresh_lock_timeout = refresh_lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Task[RefreshResult]] = {}

    def _lock(self, source_name: str) -> asyncio.Lock:
        self._locks.setdefault(source_name, asyncio.Lock())
        return self._locks[source_name]

    async def refresh(self, source_name: str) -> RefreshResult:
        existing = self._inflight.get(source_name)
        if existing is not None and not existing.done():
            return await asyncio.wait_for(asyncio.shield(existing), timeout=self.refresh_lock_timeout.total_seconds())
        task = asyncio.create_task(self._refresh_with_lock(source_name))
        self._inflight[source_name] = task
        try:
            return await task
        finally:
            if self._inflight.get(source_name) is task:
                self._inflight.pop(source_name, None)

    async def _refresh_with_lock(self, source_name: str) -> RefreshResult:
        lock = self._lock(source_name)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self.refresh_lock_timeout.total_seconds())
        except TimeoutError:
            return RefreshResult(False, source_name, error="refresh lock timeout")
        try:
            return await self._refresh_locked(source_name)
        finally:
            lock.release()

    async def _refresh_locked(self, source_name: str) -> RefreshResult:
        source = self.sources[source_name]
        now = datetime.now(UTC)
        if hasattr(self.cache_store, "set_refreshing"):
            self.cache_store.set_refreshing(source_name, True)
        old_cache = await self.cache_store.get(source_name)
        try:
            for plugin_name, ref in source.plugins.before_fetch.items():
                plugin_config = self.plugins[plugin_name]
                if self.http_plugin is None:
                    raise RuntimeError("http plugin runner is not configured")
                result = await self.http_plugin.run(PluginContext(source_name, plugin_config))
                if not result.ok and ref.on_failure == "abort":
                    raise RuntimeError(result.message or f"plugin {plugin_name} failed")

            fetched = await self.fetcher.fetch(
                source.url,
                source.fetch,
                etag=old_cache.etag if old_cache else None,
                last_modified=old_cache.last_modified if old_cache else None,
            )
            if fetched.not_modified and old_cache:
                cache = SourceCache(
                    source=source_name,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    last_attempt_at=now,
                    last_success_at=now,
                    etag=old_cache.etag,
                    last_modified=old_cache.last_modified,
                    node_count=old_cache.node_count,
                    warnings=old_cache.warnings,
                    last_error=None,
                    proxies=old_cache.proxies,
                )
                await self.cache_store.set(source_name, cache)
                return RefreshResult(True, source_name, old_cache.node_count, len(old_cache.warnings), self._cache_path(source_name))

            parsed = parse_subscription(
                fetched.body or b"",
                source=source_name,
                fmt=source.format,
                parse_error=source.parse_error,
            )
            transformed = apply_transform(parsed.records, filter_config=source.filter, rename_config=source.rename)
            if not transformed:
                raise ParseError("no usable proxies after source transform")
            cache = SourceCache(
                source=source_name,
                schema_version=CURRENT_SCHEMA_VERSION,
                last_attempt_at=now,
                last_success_at=now,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                node_count=len(transformed),
                warnings=tuple(parsed.warnings),
                last_error=None,
                proxies=tuple(transformed),
            )
            await self.cache_store.set(source_name, cache)
            return RefreshResult(True, source_name, len(transformed), len(parsed.warnings), self._cache_path(source_name))
        except Exception as exc:
            redacted_error = redact_secret(str(exc))
            if old_cache:
                failed = SourceCache(
                    source=source_name,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    last_attempt_at=now,
                    last_success_at=old_cache.last_success_at,
                    etag=old_cache.etag,
                    last_modified=old_cache.last_modified,
                    node_count=old_cache.node_count,
                    warnings=old_cache.warnings,
                    last_error=redacted_error,
                    proxies=old_cache.proxies,
                )
                await self.cache_store.set(source_name, failed)
            return RefreshResult(False, source_name, cache_path=self._cache_path(source_name), error=redacted_error)
        finally:
            if hasattr(self.cache_store, "set_refreshing"):
                self.cache_store.set_refreshing(source_name, False)

    def _cache_path(self, source_name: str) -> str | None:
        if hasattr(self.cache_store, "_path"):
            return str(self.cache_store._path(source_name))
        return None
```

- [ ] **Step 4: Run refresher tests**

Run:

```bash
uv run pytest tests/test_plugins_refresher.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit refresher**

```bash
git add src/mihomo_proxy_manager/refresher.py tests/test_plugins_refresher.py
git commit -m "feat: add source refresh pipeline"
```

---

### Task 9: Provider Renderer

**Files:**
- Create: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write renderer tests**

Create `tests/test_render.py`:

```python
import yaml

from mihomo_proxy_manager.models import ProxyRecord, RenameConfig, FilterConfig, RouteConfig, RouteOutputConfig
from mihomo_proxy_manager.render import ProviderRenderer


def route(include_meta_comments: bool = False) -> RouteConfig:
    return RouteConfig(
        name="phone",
        path="/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
        sources=("airport_a",),
        require_all_sources=False,
        output=RouteOutputConfig("provider", include_meta_comments),
        rename=RenameConfig(prefix="[phone] "),
        filter=FilterConfig(),
    )


def test_provider_renderer_preserves_fields_and_strips_internal_metadata() -> None:
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [ProxyRecord("airport_a", {"name": "HK:01", "type": "vmess", "server": "example.com", "port": 443, "uuid": "id", "cipher": "auto"})],
    )

    loaded = yaml.safe_load(body)
    proxy = loaded["proxies"][0]
    assert proxy["name"] == "[phone] HK:01"
    assert proxy["server"] == "example.com"
    assert "source" not in proxy


def test_provider_renderer_repairs_duplicate_names() -> None:
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [
            ProxyRecord("a", {"name": "HK", "type": "vmess"}),
            ProxyRecord("b", {"name": "HK", "type": "vmess"}),
            ProxyRecord("c", {"name": "HK #2", "type": "vmess"}),
        ],
    )

    names = [item["name"] for item in yaml.safe_load(body)["proxies"]]
    assert names == ["[phone] HK", "[phone] HK #2", "[phone] HK #2 #2"]
```

- [ ] **Step 2: Run renderer tests and verify they fail**

Run:

```bash
uv run pytest tests/test_render.py -v
```

Expected: FAIL because `render.py` does not exist.

- [ ] **Step 3: Implement renderer**

Create `src/mihomo_proxy_manager/render.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import yaml

from .models import ProxyRecord, RouteConfig
from .transform import apply_transform, repair_duplicate_names


class ProviderRenderer:
    def __init__(self, *, yaml_sort_keys: bool) -> None:
        self.yaml_sort_keys = yaml_sort_keys

    def render_sync(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        transformed = apply_transform(records, filter_config=route.filter, rename_config=route.rename)
        repaired = repair_duplicate_names(transformed)
        proxies = [dict(record.data) for record in repaired]
        payload = {"proxies": proxies}
        body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=self.yaml_sort_keys).encode("utf-8")
        if route.output.include_meta_comments:
            prefix = (
                f"# generated_at: {datetime.now(UTC).isoformat()}\\n"
                f"# route: {route.name}\\n"
                f"# nodes: {len(proxies)}\\n"
            ).encode("utf-8")
            return prefix + body
        return body

    async def render(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        return self.render_sync(route, records)
```

- [ ] **Step 4: Run renderer tests**

Run:

```bash
uv run pytest tests/test_render.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit renderer**

```bash
git add src/mihomo_proxy_manager/render.py tests/test_render.py
git commit -m "feat: render provider payloads"
```

---

### Task 10: Starlette App and Status Endpoint

**Files:**
- Create: `src/mihomo_proxy_manager/status.py`
- Create: `src/mihomo_proxy_manager/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write app tests**

Create `tests/test_app.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from mihomo_proxy_manager.app import create_app
from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.config import load_config
from mihomo_proxy_manager.models import ProxyRecord, SourceCache


def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'''
[server]
health_path = "/healthz"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"
route_refresh_wait = "1s"

[cache]
dir = "{tmp_path / "cache"}"
max_stale = "7d"

[sources.airport_a]
url = "https://example.com/sub"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
''', encoding="utf-8")
    return path


class FakeRefresher:
    def __init__(self) -> None:
        self.called: list[str] = []

    async def refresh(self, source_name: str):
        self.called.append(source_name)


@pytest.mark.asyncio
async def test_provider_route_returns_yaml(tmp_path) -> None:
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
    assert "proxies:" in response.text


def test_health_and_unknown_path(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    app = create_app(config, cache_store=JsonSourceCacheStore(config.cache), refresher=None, scheduler=None)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/missing").status_code == 404


@pytest.mark.asyncio
async def test_provider_serves_stale_valid_cache_and_triggers_refresh(tmp_path) -> None:
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
```

- [ ] **Step 2: Run app tests and verify they fail**

Run:

```bash
uv run pytest tests/test_app.py -v
```

Expected: FAIL because `app.py` does not exist.

- [ ] **Step 3: Implement status helpers**

Create `src/mihomo_proxy_manager/status.py`:

```python
from __future__ import annotations

from .cache import SourceCacheStore
from .security import redact_secret


async def build_status(cache_store: SourceCacheStore, source_names: list[str]) -> dict[str, object]:
    sources = []
    for name in source_names:
        status = await cache_store.status(name)
        sources.append(
            {
                "source": status.source,
                "last_attempt_at": status.last_attempt_at.isoformat() if status.last_attempt_at else None,
                "last_success_at": status.last_success_at.isoformat() if status.last_success_at else None,
                "node_count": status.node_count,
                "last_error": redact_secret(status.last_error) if status.last_error else None,
                "refreshing": status.refreshing,
            }
        )
    return {"sources": sources}
```

- [ ] **Step 4: Implement Starlette app**

Create `src/mihomo_proxy_manager/app.py`:

```python
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .cache import SourceCacheStore
from .models import AppConfig, ProxyRecord, SourceConfig
from .render import ProviderRenderer
from .status import build_status


def _is_still_valid(cache, max_stale) -> bool:
    return bool(cache and cache.last_success_at and datetime.now(UTC) - cache.last_success_at <= max_stale)


def _is_due(cache, source: SourceConfig, timezone: str) -> bool:
    if not cache or not cache.last_success_at:
        return True
    now = datetime.now(UTC)
    if source.refresh.interval and now - cache.last_success_at >= source.refresh.interval:
        return True
    if source.refresh.cron:
        tz = ZoneInfo(timezone)
        last_success = cache.last_success_at.astimezone(tz)
        now_tz = now.astimezone(tz)
        for expr in source.refresh.cron:
            previous = croniter(expr, now_tz).get_prev(datetime)
            if previous > last_success:
                return True
    return False


def create_app(config: AppConfig, *, cache_store: SourceCacheStore, refresher, scheduler) -> Starlette:
    renderer = ProviderRenderer(yaml_sort_keys=config.output.yaml_sort_keys)
    route_by_path = {route.path: route for route in config.routes.values()}

    async def health(request):
        return JSONResponse({"ok": True})

    async def status(request):
        return JSONResponse(await build_status(cache_store, list(config.sources)))

    async def provider(request):
        route = route_by_path.get(request.url.path)
        if route is None:
            return PlainTextResponse("not found", status_code=404)

        records: list[ProxyRecord] = []
        missing: list[str] = []
        due: list[str] = []
        for source_name in route.sources:
            cache = await cache_store.get(source_name)
            if _is_still_valid(cache, config.cache.max_stale):
                records.extend(cache.proxies)
                if _is_due(cache, config.sources[source_name], config.server.timezone):
                    due.append(source_name)
            else:
                missing.append(source_name)

        for source_name in due:
            if refresher is not None:
                asyncio.create_task(refresher.refresh(source_name))
        if due:
            await asyncio.sleep(0)

        if missing and refresher is not None:
            tasks = [asyncio.create_task(refresher.refresh(name)) for name in missing]
            if route.require_all_sources or not records:
                await asyncio.wait(tasks, timeout=config.server.route_refresh_wait.total_seconds())
                records.clear()
                for source_name in route.sources:
                    cache = await cache_store.get(source_name)
                    if _is_still_valid(cache, config.cache.max_stale):
                        records.extend(cache.proxies)
                    elif route.require_all_sources:
                        return PlainTextResponse("route unavailable", status_code=503)
            else:
                for task in tasks:
                    task.add_done_callback(lambda _: None)

        if not records:
            return PlainTextResponse("route unavailable", status_code=503)

        body = await renderer.render(route, records)
        return Response(body, media_type="application/yaml; charset=utf-8")

    routes = [Route(config.server.health_path, health)]
    if config.server.status_path:
        routes.append(Route(config.server.status_path, status))
    routes.append(Route("/{path:path}", provider))

    async def lifespan(app):
        if scheduler:
            await scheduler.start()
        yield
        if scheduler:
            await scheduler.stop()

    return Starlette(routes=routes, lifespan=lifespan)
```

- [ ] **Step 5: Run app tests**

Run:

```bash
uv run pytest tests/test_app.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Starlette app**

```bash
git add src/mihomo_proxy_manager/app.py src/mihomo_proxy_manager/status.py tests/test_app.py
git commit -m "feat: expose provider routes"
```

---

### Task 11: Scheduler, Logging, and Complete CLI

**Files:**
- Create: `src/mihomo_proxy_manager/scheduler.py`
- Create: `src/mihomo_proxy_manager/logging.py`
- Create: `tests/test_scheduler.py`
- Modify: `src/mihomo_proxy_manager/cli.py`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Extend CLI tests**

Append to `tests/test_cli_smoke.py`:

```python
from pathlib import Path

from mihomo_proxy_manager.cli import main


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
```

- [ ] **Step 2: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli_smoke.py -v
```

Expected: PASS for CLI parser tests and PASS for `check` if earlier CLI wiring is correct.

- [ ] **Step 3: Write scheduler tests**

Create `tests/test_scheduler.py`:

```python
from datetime import timedelta

import pytest

from mihomo_proxy_manager.models import (
    AppConfig,
    CacheConfig,
    HttpConfig,
    LoggingSinkConfig,
    OutputConfig,
    ParserConfig,
    RefreshConfig,
    RenameConfig,
    FilterConfig,
    RouteConfig,
    RouteOutputConfig,
    SchedulerConfig,
    SecurityConfig,
    ServerConfig,
    SourceConfig,
    SourcePluginConfig,
    FetchConfig,
)
from mihomo_proxy_manager.scheduler import RefreshScheduler


class FakeRefresher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def refresh(self, source_name: str):
        self.calls.append(source_name)


def scheduler_config(tmp_path, *, startup_refresh=True, startup_refresh_mode="blocking") -> AppConfig:
    source = SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="auto",
        parse_error="skip",
        fetch=FetchConfig(timedelta(seconds=30), "ua", {}, False),
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
        scheduler=SchedulerConfig(startup_refresh, startup_refresh_mode, timedelta(seconds=0), timedelta(seconds=1)),
        security=SecurityConfig(128, False),
        parser=ParserConfig("auto", "skip"),
        output=OutputConfig(False, False),
        sources={"airport_a": source},
        routes={"phone": RouteConfig("phone", "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", ("airport_a",), False, RouteOutputConfig(), RenameConfig(), FilterConfig())},
        plugins={},
    )


@pytest.mark.asyncio
async def test_scheduler_blocking_startup_refreshes_sources(tmp_path) -> None:
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path), refresher)

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == ["airport_a"]


@pytest.mark.asyncio
async def test_scheduler_startup_refresh_can_be_disabled(tmp_path) -> None:
    refresher = FakeRefresher()
    scheduler = RefreshScheduler(scheduler_config(tmp_path, startup_refresh=False), refresher)

    await scheduler.start()
    await scheduler.stop()

    assert refresher.calls == []
```

- [ ] **Step 4: Implement scheduler**

Create `src/mihomo_proxy_manager/scheduler.py`:

```python
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from .models import AppConfig


class RefreshScheduler:
    def __init__(self, config: AppConfig, refresher) -> None:
        self.config = config
        self.refresher = refresher
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self.config.scheduler.startup_refresh:
            if self.config.scheduler.startup_refresh_mode == "blocking":
                await asyncio.gather(*(self.refresher.refresh(name) for name in self.config.sources))
            else:
                for name in self.config.sources:
                    self._tasks.append(asyncio.create_task(self.refresher.refresh(name)))
        for name, source in self.config.sources.items():
            if source.refresh.interval:
                self._tasks.append(asyncio.create_task(self._interval_loop(name, source.refresh.interval.total_seconds())))
            for expr in source.refresh.cron:
                self._tasks.append(asyncio.create_task(self._cron_loop(name, expr)))

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _jitter(self) -> None:
        seconds = self.config.scheduler.jitter.total_seconds()
        if seconds > 0:
            await asyncio.sleep(random.uniform(0, seconds))

    async def _interval_loop(self, source_name: str, interval_seconds: float) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(interval_seconds)
            await self._jitter()
            await self.refresher.refresh(source_name)

    async def _cron_loop(self, source_name: str, expr: str) -> None:
        tz = ZoneInfo(self.config.server.timezone)
        iterator = croniter(expr, datetime.now(tz))
        while not self._stopping.is_set():
            next_at = iterator.get_next(datetime)
            delay = max(0.0, (next_at - datetime.now(tz)).total_seconds())
            await asyncio.sleep(delay)
            await self._jitter()
            await self.refresher.refresh(source_name)
```

- [ ] **Step 5: Implement loguru setup**

Create `src/mihomo_proxy_manager/logging.py`:

```python
from __future__ import annotations

import sys

from loguru import logger

from .models import AppConfig


def configure_logging(config: AppConfig) -> None:
    logger.remove()
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
```

- [ ] **Step 6: Complete CLI serve and refresh**

Replace `src/mihomo_proxy_manager/cli.py` with:

```python
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
import uvicorn

from .app import create_app
from .cache import JsonSourceCacheStore
from .config import load_config
from .fetcher import SafeHttpClient, SubscriptionFetcher
from .logging import configure_logging
from .plugins.http_action import HttpActionPlugin
from .refresher import SourceRefresher
from .scheduler import RefreshScheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")

    return parser


def _cmd_check(config_path: str) -> int:
    config_file = Path(config_path)
    config = load_config(config_file, validate=False)
    report = config.validate(config_path=config_file)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.ok:
        print("OK: configuration is valid")
        return 0
    return 1


async def _build_runtime(config_path: str):
    config_file = Path(config_path)
    config = load_config(config_file)
    configure_logging(config)
    cache_store = JsonSourceCacheStore(config.cache)
    client = httpx.AsyncClient()
    fetcher = SubscriptionFetcher(client, config.http)
    plugin = HttpActionPlugin(SafeHttpClient(client, config.http))
    refresher = SourceRefresher(
        sources=config.sources,
        plugins=config.plugins,
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=plugin,
        refresh_lock_timeout=config.scheduler.refresh_lock_timeout,
    )
    return config, cache_store, client, refresher


def _cmd_serve(config_path: str) -> int:
    async def run() -> int:
        config, cache_store, client, refresher = await _build_runtime(config_path)
        scheduler = RefreshScheduler(config, refresher)
        app = create_app(config, cache_store=cache_store, refresher=refresher, scheduler=scheduler)
        try:
            server_config = uvicorn.Config(app, host=config.server.host, port=config.server.port, access_log=False)
            server = uvicorn.Server(server_config)
            await server.serve()
            return 0
        finally:
            await client.aclose()

    return asyncio.run(run())


def _cmd_refresh(config_path: str, source_name: str) -> int:
    async def run() -> int:
        config, _cache_store, client, refresher = await _build_runtime(config_path)
        try:
            if source_name not in config.sources:
                print(f"ERROR: unknown source {source_name!r}")
                return 1
            result = await refresher.refresh(source_name)
            if result.ok:
                print(f"OK: refreshed {result.source}: nodes={result.node_count} warnings={result.warning_count} cache={result.cache_path}")
                return 0
            print(f"ERROR: refresh failed for {result.source}: cache={result.cache_path} error={result.error}")
            return 1
        finally:
            await client.aclose()

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.config)
    if args.command == "serve":
        return _cmd_serve(args.config)
    if args.command == "refresh":
        return _cmd_refresh(args.config, args.source)
    parser.error("unreachable command")
    return 2
```

- [ ] **Step 7: Run CLI, scheduler tests, and config check**

Run:

```bash
uv run pytest tests/test_cli_smoke.py -v
uv run pytest tests/test_scheduler.py -v
uv run mpm check -c examples/config.toml
```

Expected: tests PASS; `mpm check` prints `OK: configuration is valid`.

- [ ] **Step 8: Commit scheduler and CLI**

```bash
git add src/mihomo_proxy_manager/scheduler.py src/mihomo_proxy_manager/logging.py src/mihomo_proxy_manager/cli.py tests/test_cli_smoke.py tests/test_scheduler.py
git commit -m "feat: add scheduler and service cli"
```

---

### Task 12: Full Verification and Documentation Polish

**Files:**
- Modify: `README.md`
- Modify: `examples/config.toml`
- Modify: test assertions only when verification exposes a mismatch between the written tests and the implemented public behavior

- [ ] **Step 1: Update README with MVP behavior**

Replace `README.md` with:

````markdown
# mihomo-proxy-manager

Async upstream provider service for aggregating Clash/Mihomo subscriptions.

## What it does

- Downloads configured upstream subscriptions.
- Parses Clash/Mihomo YAML and common share-link subscriptions.
- Applies source-level and route-level filtering and renaming.
- Caches source-level parsed proxies to JSON files.
- Exposes hidden provider payload URLs for Mihomo `proxy-providers`.

## Commands

```bash
mpm check -c examples/config.toml
mpm serve -c examples/config.toml
mpm refresh -c examples/config.toml airport_a
```

## Provider output

Configured provider routes return:

```yaml
proxies:
  - name: "[airport_a] HK 01"
    type: vmess
    server: example.com
    port: 443
```

## Security notes

Provider paths are bearer secrets. Use high-entropy route paths, serve over TLS in production, and rotate paths if leaked.
````

- [ ] **Step 2: Run all tests**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run type checker**

Run:

```bash
uv run ty check
```

Expected: PASS or no diagnostics.

- [ ] **Step 4: Run CLI config check**

Run:

```bash
uv run mpm check -c examples/config.toml
```

Expected:

```text
OK: configuration is valid
```

- [ ] **Step 5: Start the service briefly and verify health endpoint**

Run:

```bash
uv run mpm serve -c examples/config.toml
```

In another terminal, run:

```bash
curl -i http://127.0.0.1:8080/healthz
```

Expected:

```text
HTTP/1.1 200 OK
{"ok":true}
```

Stop the service with `Ctrl-C`.

- [ ] **Step 6: Commit final docs and verification fixes**

```bash
git add README.md examples/config.toml tests src
git commit -m "docs: document provider service usage"
```

## Final Verification Checklist

- [ ] `uv run pytest -v` passes.
- [ ] `uv run ty check` passes or reports no diagnostics.
- [ ] `uv run mpm check -c examples/config.toml` returns exit code 0.
- [ ] `uv run mpm serve -c examples/config.toml` starts.
- [ ] `curl -i http://127.0.0.1:8080/healthz` returns 200.
- [ ] `git status --short` is clean after final commit.

## Spec Coverage Review

- Config model and validation: Tasks 2 and 3.
- Hidden path entropy and URL safety: Task 3.
- Source-level cache and file locks: Task 6.
- YAML/share-link parsing: Task 5.
- Source and route transforms: Task 4 and Task 9.
- Async fetching and plugins: Task 7.
- Refresh pipeline and stale cache behavior foundations: Task 8 and Task 10.
- Provider renderer: Task 9.
- Starlette endpoints: Task 10.
- Scheduler and startup refresh: Task 11.
- CLI commands: Tasks 1, 2, and 11.
- Logging: Task 11.
- Verification: Task 12.
