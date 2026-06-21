# Access Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task on the current branch. Implementation tasks must run serially in order, Task 1 -> Task 6. Do not dispatch parallel implementation workers because `app.py`, `cli.py`, and tests overlap across tasks. Reviews may run in parallel after a task's diff exists. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SQLite-backed access auditing for subscription routes, with trusted real-IP resolution, redacted header capture, separate human-readable access logs, and masked aggregate stats on the status API/page.

**Architecture:** Configuration owns all access audit policy. A new `access_audit.py` module owns event dataclasses, real-IP/header sanitization helpers, SQLite storage, stats aggregation, and access log line formatting. `app.py` records only matched route requests through a shared finalizer, while `status.py` consumes store stats through an optional dependency.

**Tech Stack:** Python 3.11+, Starlette, Loguru, SQLAlchemy Core 2.x, SQLite WAL, pytest, ty, ruff.

---

## Spec Source

Primary spec: `docs/superpowers/specs/2026-06-21-access-audit-design.md`

Fixed decisions to preserve:

- Parent `access_log.enabled = false` disables DB and access file logging.
- Retention default is `30d`.
- Aggregation is by route/path, not TCP port.
- Header values are stored only after sensitive-name redaction, `redact_secret()`, and truncation.
- Status API/HTML never expose full `headers_json`.
- Proxy headers are trusted only when direct peer is in `trusted_proxies`.
- Query/status/health/unknown 404 accesses are excluded; matched route outcomes `403`, `400`, `422`, `503`, and success are recorded.
- Access log sink is separate from normal Loguru sinks and human-readable.
- Access audit stores personal data: IP addresses, User-Agents, route paths, timestamps, selected sanitized headers, response status, response size, and duration. Disable all audit storage and access-file logging with `[access_log] enabled = false`.
- Access stats are exposed on `status_path`; keep `status_path` high entropy and non-public, and keep high-entropy header values out of `access_log.headers.stats_allowlist`.

## File Structure

- Create `src/mihomo_proxy_manager/access_audit.py`
  - Config-independent dataclasses and protocols: `AccessEvent`, `AccessStats`, `AccessAuditStore`.
  - Real IP helpers: `parse_trusted_networks()`, `resolve_real_ip()`, `mask_ip_for_status()`.
  - Header helpers: `sanitize_headers()`, `display_header_value()`, `format_access_log_line()`.
  - SQLAlchemy Core schema and `SQLiteAccessAuditStore`.
- Modify `src/mihomo_proxy_manager/models.py`
  - Add `AccessLogFileConfig`, `AccessLogHeadersConfig`, `AccessLogStatusConfig`, `AccessLogConfig`.
  - Add `access_log: AccessLogConfig` to `AppConfig`.
- Modify `src/mihomo_proxy_manager/config.py`
  - Add `[access_log]` parsing, nested key validation, known real-IP header validation, duration/path/network validation, and filesystem checks.
- Modify `src/mihomo_proxy_manager/logging.py`
  - Add access log sink support using `logger.bind(access_log=True)`.
  - Filter normal sinks to exclude access records.
- Modify `src/mihomo_proxy_manager/app.py`
  - Accept `access_audit_store`.
  - Wrap matched provider route processing with event finalization.
  - Use `asyncio.to_thread()` for store calls.
  - Dispose store during lifespan shutdown.
- Modify `src/mihomo_proxy_manager/status.py`
  - Accept optional `access_audit_store`.
  - Add `access` JSON and HTML section.
- Modify `src/mihomo_proxy_manager/cli.py`
  - Initialize access logging and SQLite store for `serve`.
  - Keep `check` validation filesystem-only, no DB open.
- Modify dependency files:
  - `pyproject.toml`
  - `requirements.txt`
  - `uv.lock`
- Modify docs/examples:
  - `README.md`
  - `README_EN.md`
  - `examples/config.toml`
- Add tests:
  - `tests/test_access_audit.py`
  - Extend `tests/test_config.py`
  - Extend `tests/test_logging.py`
  - Extend `tests/test_app.py`
  - Extend `tests/test_status.py` if created, otherwise status tests may live in `tests/test_app.py` or `tests/test_coverage_gaps.py` following existing style.

## Task 1: Dependencies And Config Model

**Files:**

- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `uv.lock`
- Modify: `src/mihomo_proxy_manager/models.py`
- Modify: `src/mihomo_proxy_manager/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config/default tests**

Add tests covering defaults, parsing, invalid values, and dependency packaging.

```python
from datetime import timedelta
from pathlib import Path

import pytest

from mihomo_proxy_manager.config import load_config


def test_access_log_defaults(temp_config_path: Path) -> None:
    config = load_config(write_config(temp_config_path, minimal_config()))
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
        +
        """
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
        """
    )
    config = load_config(path)
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
        ("[access_log.headers]\nunknown = true\n", "access_log.headers key is unsupported"),
        ("[access_log.status]\nunknown = true\n", "access_log.status key is unsupported"),
        ('[access_log]\nretention = "0s"\n', "access_log.retention must be positive"),
        ('[access_log]\ntrusted_proxies = ["not-a-network"]\n', "trusted proxy is invalid"),
        ('[access_log]\nreal_ip_headers = ["forwarded"]\n', "real_ip_headers value is unsupported"),
        ("[access_log.headers]\nmax_value_length = 0\n", "max_value_length must be positive"),
        ("[access_log.headers]\nstats_max_rows = 0\n", "stats_max_rows must be positive"),
        ("[access_log.status]\nrecent_limit = 0\n", "recent_limit must be positive"),
        ("[access_log.status]\ntop_limit = 0\n", "top_limit must be positive"),
    ],
)
def test_access_log_rejects_invalid_config(
    temp_config_path: Path, snippet: str, message: str
) -> None:
    path = write_config(temp_config_path, minimal_config() + "\n" + snippet)
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_access_log_filesystem_checks_create_dirs(temp_config_path: Path, tmp_path: Path) -> None:
    config_path = write_config(
        temp_config_path,
        minimal_config()
        +
        f"""
        [access_log]
        enabled = true
        db_path = "{tmp_path / "data" / "access.sqlite3"}"

        [access_log.file]
        enabled = true
        path = "{tmp_path / "logs" / "access.log"}"
        """
    )
    config = load_config(config_path)
    assert config.check_filesystem() == []
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert not (tmp_path / "data" / "access.sqlite3").exists()
    assert not (tmp_path / "logs" / "access.log").exists()


def test_access_log_disabled_skips_access_dirs(temp_config_path: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "disabled" / "access.sqlite3"
    log_path = tmp_path / "disabled-logs" / "access.log"
    config_path = write_config(
        temp_config_path,
        minimal_config()
        +
        f"""
        [access_log]
        enabled = false
        db_path = "{db_path}"

        [access_log.file]
        enabled = true
        path = "{log_path}"
        """
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
    assert 'name = "sqlalchemy"' in lock
```

Use the existing `tests/test_config.py` helpers exactly: `load_config(write_config(temp_config_path, minimal_config()))`, and every `write_config(...)` call takes `(path, body)`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: failures mention missing `AppConfig.access_log`, unsupported `[access_log]`, and missing SQLAlchemy dependency.

- [ ] **Step 3: Add config dataclasses**

Add to `models.py` near logging config definitions:

```python
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Literal

IPNetwork = IPv4Network | IPv6Network
RealIPHeader = Literal["cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"]
DEFAULT_TRUSTED_PROXY_NETWORKS = (
    "127.0.0.1/32",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


def _default_trusted_proxies() -> tuple[IPNetwork, ...]:
    return tuple(ip_network(value, strict=False) for value in DEFAULT_TRUSTED_PROXY_NETWORKS)


@dataclass(frozen=True)
class AccessLogFileConfig:
    enabled: bool = True
    path: Path = Path("logs/access.log")
    rotation: str = "10 MB"
    retention: str = "30 days"
    compression: str = "gz"


@dataclass(frozen=True)
class AccessLogHeadersConfig:
    max_value_length: int = 512
    stats_allowlist: tuple[str, ...] = (
        "user-agent",
        "host",
        "cf-ipcountry",
        "cf-ray",
    )
    stats_max_rows: int = 5000


@dataclass(frozen=True)
class AccessLogStatusConfig:
    enabled: bool = True
    mask_ips: bool = True
    include_recent: bool = False
    recent_limit: int = 20
    top_limit: int = 20


@dataclass(frozen=True)
class AccessLogConfig:
    enabled: bool = True
    db_path: Path = Path("data/access/access.sqlite3")
    retention: timedelta = field(default_factory=lambda: timedelta(days=30))
    trusted_proxies: tuple[IPNetwork, ...] = field(default_factory=_default_trusted_proxies)
    real_ip_headers: tuple[RealIPHeader, ...] = (
        "cf-connecting-ip",
        "true-client-ip",
        "x-forwarded-for",
        "x-real-ip",
    )
    file: AccessLogFileConfig = field(default_factory=AccessLogFileConfig)
    headers: AccessLogHeadersConfig = field(default_factory=AccessLogHeadersConfig)
    status: AccessLogStatusConfig = field(default_factory=AccessLogStatusConfig)
```

Then add `access_log: AccessLogConfig = field(default_factory=AccessLogConfig)` to `AppConfig`.

- [ ] **Step 4: Implement parser helpers and whitelist**

In `config.py`, import `ip_network`, `cast`, and the `AccessLog*`, `IPNetwork`, and `RealIPHeader` model types. Do not import from `access_audit.py` in Task 1 because that file is created in Task 2. Add helper functions:

```python
from ipaddress import ip_network
from typing import cast

SUPPORTED_REAL_IP_HEADERS = {
    "cf-connecting-ip",
    "true-client-ip",
    "x-forwarded-for",
    "x-real-ip",
}


def _positive_duration(value: str, *, key: str) -> timedelta:
    duration = parse_duration(value)
    if duration.total_seconds() <= 0:
        raise ValueError(f"{key} must be positive")
    return duration


def _positive_int(value: object, *, key: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{key} must be positive")
    return parsed


def _string_tuple(value: object, default: tuple[str, ...], *, key: str) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{key} must contain scalar string values")
        result.append(str(item))
    return tuple(result)


def _trusted_proxies(values: object) -> tuple[IPNetwork, ...]:
    raw_values = _string_tuple(
        values,
        (
            "127.0.0.1/32",
            "::1/128",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ),
        key="access_log.trusted_proxies",
    )
    networks: list[IPNetwork] = []
    try:
        for value in raw_values:
            networks.append(ip_network(value, strict=False))
    except ValueError as exc:
        raise ValueError("access_log trusted proxy is invalid") from exc
    return tuple(networks)


def _real_ip_headers(values: object) -> tuple[RealIPHeader, ...]:
    raw_values = _string_tuple(
        values,
        ("cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"),
        key="access_log.real_ip_headers",
    )
    headers = tuple(value.lower() for value in raw_values)
    unsupported = sorted(set(headers) - SUPPORTED_REAL_IP_HEADERS)
    if unsupported:
        raise ValueError(
            "access_log.real_ip_headers value is unsupported: "
            + ", ".join(repr(item) for item in unsupported)
        )
    return cast(tuple[RealIPHeader, ...], headers)


def _reject_unknown_keys(data: dict[str, object], allowed: set[str], prefix: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            "\n".join(f"{prefix} key is unsupported: {key!r}" for key in unknown)
        )


def _access_log(raw: dict[str, object]) -> AccessLogConfig:
    allowed = {"enabled", "db_path", "retention", "trusted_proxies", "real_ip_headers", "file", "headers", "status"}
    file_allowed = {"enabled", "path", "rotation", "retention", "compression"}
    headers_allowed = {"max_value_length", "stats_allowlist", "stats_max_rows"}
    status_allowed = {"enabled", "mask_ips", "include_recent", "recent_limit", "top_limit"}
    _reject_unknown_keys(raw, allowed, "access_log")
    file_raw = _table(raw, "file")
    headers_raw = _table(raw, "headers")
    status_raw = _table(raw, "status")
    _reject_unknown_keys(file_raw, file_allowed, "access_log.file")
    _reject_unknown_keys(headers_raw, headers_allowed, "access_log.headers")
    _reject_unknown_keys(status_raw, status_allowed, "access_log.status")
    return AccessLogConfig(
        enabled=bool(raw.get("enabled", True)),
        db_path=Path(raw.get("db_path", "data/access/access.sqlite3")),
        retention=_positive_duration(str(raw.get("retention", "30d")), key="access_log.retention"),
        trusted_proxies=_trusted_proxies(raw.get("trusted_proxies")),
        real_ip_headers=_real_ip_headers(raw.get("real_ip_headers")),
        file=AccessLogFileConfig(
            enabled=bool(file_raw.get("enabled", True)),
            path=Path(file_raw.get("path", "logs/access.log")),
            rotation=str(file_raw.get("rotation", "10 MB")),
            retention=str(file_raw.get("retention", "30 days")),
            compression=str(file_raw.get("compression", "gz")),
        ),
        headers=AccessLogHeadersConfig(
            max_value_length=_positive_int(headers_raw.get("max_value_length", 512), key="access_log.headers.max_value_length"),
            stats_allowlist=tuple(
                item.lower()
                for item in _string_tuple(
                    headers_raw.get("stats_allowlist"),
                    ("user-agent", "host", "cf-ipcountry", "cf-ray"),
                    key="access_log.headers.stats_allowlist",
                )
            ),
            stats_max_rows=_positive_int(headers_raw.get("stats_max_rows", 5000), key="access_log.headers.stats_max_rows"),
        ),
        status=AccessLogStatusConfig(
            enabled=bool(status_raw.get("enabled", True)),
            mask_ips=bool(status_raw.get("mask_ips", True)),
            include_recent=bool(status_raw.get("include_recent", False)),
            recent_limit=_positive_int(status_raw.get("recent_limit", 20), key="access_log.status.recent_limit"),
            top_limit=_positive_int(status_raw.get("top_limit", 20), key="access_log.status.top_limit"),
        ),
    )
```

Add `"access_log"` to `allowed_top_level`, parse `access_log_raw = _table(raw, "access_log")`, pass `access_log=_access_log(access_log_raw)` into `LoadedConfig` construction.

- [ ] **Step 5: Extend filesystem checks**

In `LoadedConfig.check_filesystem()`:

```python
if self.access_log.enabled:
    self.access_log.db_path.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(self.access_log.db_path.parent, os.W_OK):
        errors.append(
            f"access log database directory is not writable: {self.access_log.db_path.parent}"
        )
    if self.access_log.file.enabled:
        self.access_log.file.path.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(self.access_log.file.path.parent, os.W_OK):
            errors.append(
                f"access log directory is not writable: {self.access_log.file.path.parent}"
            )
```

This must not create the DB file or access log file.

- [ ] **Step 6: Update dependency files**

Run:

```bash
rtk uv add "sqlalchemy>=2.0"
rtk uv pip compile pyproject.toml --all-extras -o requirements.txt
```

If this project uses a different requirements generation command in docs or Makefile, use that exact command. Verify `pyproject.toml`, `uv.lock`, and `requirements.txt` all changed.

- [ ] **Step 7: Run config tests**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
rtk git add pyproject.toml requirements.txt uv.lock src/mihomo_proxy_manager/models.py src/mihomo_proxy_manager/config.py tests/test_config.py
rtk git commit -m "feat(access): add audit config"
```

## Task 2: Access Audit Helpers And SQLite Store

**Files:**

- Create: `src/mihomo_proxy_manager/access_audit.py`
- Test: `tests/test_access_audit.py`

- [ ] **Step 1: Write failing helper and store tests**

Create `tests/test_access_audit.py` with tests for real-IP resolution, header sanitization, stats, retention, and failure-safe behavior.

```python
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from ipaddress import ip_network
from pathlib import Path

from sqlalchemy import text

from mihomo_proxy_manager.access_audit import (
    AccessEvent,
    SQLiteAccessAuditStore,
    format_access_log_line,
    mask_ip_for_status,
    resolve_real_ip,
    sanitize_headers,
)
from mihomo_proxy_manager.models import (
    AccessLogConfig,
    AccessLogFileConfig,
    AccessLogHeadersConfig,
    AccessLogStatusConfig,
)


def access_config(tmp_path: Path, **overrides) -> AccessLogConfig:
    config = AccessLogConfig(
        enabled=True,
        db_path=tmp_path / "access.sqlite3",
        retention=timedelta(days=30),
        trusted_proxies=(
            ip_network("127.0.0.1/32"),
            ip_network("10.0.0.0/8"),
        ),
        real_ip_headers=(
            "cf-connecting-ip",
            "true-client-ip",
            "x-forwarded-for",
            "x-real-ip",
        ),
        file=AccessLogFileConfig(),
        headers=AccessLogHeadersConfig(),
        status=AccessLogStatusConfig(),
    )
    return replace(config, **overrides)


def event(**overrides) -> AccessEvent:
    base = AccessEvent(
        visited_at=1_790_000_000_000,
        route_name="phone",
        path="/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
        companion=None,
        method="GET",
        status_code=200,
        real_ip="203.0.113.10",
        ip_source="cf-connecting-ip",
        user_agent="Surfboard/2.24",
        headers={"user-agent": "Surfboard/2.24", "host": "mpm.example.com"},
        target_format="surfboard",
        response_bytes=1234,
        duration_ms=18,
    )
    return replace(base, **overrides)


def test_resolve_real_ip_priority_from_trusted_peer() -> None:
    headers = {
        "cf-connecting-ip": "203.0.113.10",
        "true-client-ip": "198.51.100.3",
        "x-forwarded-for": "8.8.8.8, 10.0.0.2",
        "x-real-ip": "192.0.2.8",
    }
    result = resolve_real_ip(
        client_host="127.0.0.1",
        headers=headers,
        trusted_proxies=(ip_network("127.0.0.1/32"),),
        header_order=("cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"),
    )
    assert result.real_ip == "203.0.113.10"
    assert result.ip_source == "cf-connecting-ip"


def test_resolve_real_ip_ignores_proxy_headers_from_untrusted_peer() -> None:
    result = resolve_real_ip(
        client_host="198.51.100.9",
        headers={"cf-connecting-ip": "203.0.113.10"},
        trusted_proxies=(ip_network("127.0.0.1/32"),),
        header_order=("cf-connecting-ip", "x-real-ip"),
    )
    assert result.real_ip == "198.51.100.9"
    assert result.ip_source == "client-host"


def test_resolve_real_ip_xff_skips_private_and_invalid_entries() -> None:
    result = resolve_real_ip(
        client_host="127.0.0.1",
        headers={"x-forwarded-for": "bad, 10.1.1.1, 8.8.8.8"},
        trusted_proxies=(ip_network("127.0.0.1/32"),),
        header_order=("x-forwarded-for", "x-real-ip"),
    )
    assert result.real_ip == "8.8.8.8"
    assert result.ip_source == "x-forwarded-for"


def test_sanitize_headers_redacts_secrets_and_truncates() -> None:
    headers = {
        "Authorization": "Bearer abc123",
        "Cookie": "session=secret",
        "X-Trace": "token=abc123",
        "User-Agent": "A" * 20,
    }
    sanitized = sanitize_headers(headers, max_value_length=8, extra_secrets=["abc123"])
    assert sanitized["authorization"] == "***"
    assert sanitized["cookie"] == "***"
    assert sanitized["x-trace"] == "token=***"
    assert sanitized["user-agent"] == "AAAAA..."


def test_mask_ip_for_status_masks_ipv4_and_ipv6() -> None:
    assert mask_ip_for_status("203.0.113.10") == "203.0.113.0/24"
    assert mask_ip_for_status("2001:db8:abcd:1234:5678::1") == "2001:db8:abcd:1234::/64"
    assert mask_ip_for_status(None) is None


def test_store_records_event_and_returns_stats(tmp_path: Path) -> None:
    config = access_config(
        tmp_path,
        headers=AccessLogHeadersConfig(
            stats_allowlist=("user-agent", "host", "x-forwarded-for"),
            stats_max_rows=100,
        ),
        status=AccessLogStatusConfig(include_recent=True, recent_limit=2, top_limit=5),
    )
    store = SQLiteAccessAuditStore(config)
    try:
        store.record(event(headers={"user-agent": "Surfboard/2.24", "host": "mpm.example.com", "referer": "https://example.com/path?token=secret", "x-custom-token": "***", "x-forwarded-for": "8.8.8.8, 10.0.0.1"}))
        store.record(event(visited_at=1_790_000_001_000, real_ip="2001:db8:abcd:1234::1", user_agent="Quantumult X", path="/p/token-import", companion="import", target_format="quantumult-x"))
        stats = store.stats(now_ms=1_790_000_002_000)
    finally:
        store.dispose()
    assert stats.enabled is True
    assert stats.stats_enabled is True
    assert stats.retention_seconds == 2_592_000
    assert stats.privacy == {"mask_ips": True, "include_recent": True}
    assert stats.total_events == 2
    assert stats.since == 1_790_000_000_000
    assert stats.top_ips[0]["real_ip"] == "2001:db8:abcd:1234::/64"
    assert any(item["user_agent"] == "Quantumult X" for item in stats.top_user_agents)
    assert any(item["header"] == "host" and item["value"] == "mpm.example.com" for item in stats.top_headers)
    assert any(item["header"] == "x-forwarded-for" and item["value"] == "8.8.8.0/24, 10.0.0.0/24" for item in stats.top_headers)
    assert any(
        item["path"] == "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
        and item["route_name"] == "phone"
        and item["count"] == 1
        for item in stats.top_paths
    )
    assert any(
        item["path"] == "/p/token-import"
        and item["route_name"] == "phone"
        and item["count"] == 1
        for item in stats.top_paths
    )
    assert all(item["header"] != "referer" for item in stats.top_headers)
    assert all(item["header"] != "x-custom-token" for item in stats.top_headers)
    assert stats.recent is not None
    assert stats.recent[0]["path"] == "/p/token-import"
    assert "headers_json" not in stats.recent[0]


def test_store_cleanup_uses_epoch_ms_cutoff(tmp_path: Path) -> None:
    config = access_config(tmp_path, retention=timedelta(days=1))
    store = SQLiteAccessAuditStore(config)
    try:
        store.record(event(visited_at=1_000))
        store.record(event(visited_at=90_000_000))
        store.cleanup(now_ms=90_000_000)
        stats = store.stats(now_ms=90_000_000)
    finally:
        store.dispose()
    assert stats.total_events == 1


def test_store_stats_filters_retained_window_without_cleanup(tmp_path: Path) -> None:
    config = access_config(
        tmp_path,
        retention=timedelta(seconds=30),
        headers=AccessLogHeadersConfig(
            stats_allowlist=("user-agent", "host"),
            stats_max_rows=100,
        ),
        status=AccessLogStatusConfig(include_recent=True, recent_limit=10, top_limit=10),
    )
    store = SQLiteAccessAuditStore(config)
    try:
        store.record(
            event(
                visited_at=1_000,
                path="/p/old",
                route_name="phone",
                real_ip="198.51.100.1",
                user_agent="Old UA",
                headers={"user-agent": "Old UA", "host": "old.example"},
            )
        )
        store.record(
            event(
                visited_at=72_000,
                path="/p/recent-a",
                route_name="phone",
                real_ip="203.0.113.10",
                user_agent="Recent UA",
                headers={"user-agent": "Recent UA", "host": "mpm.example.com"},
            )
        )
        store.record(
            event(
                visited_at=80_000,
                path="/p/recent-a",
                route_name="tablet",
                real_ip="203.0.113.11",
                user_agent="Recent UA",
                headers={"user-agent": "Recent UA", "host": "mpm.example.com"},
            )
        )
        stats = store.stats(now_ms=100_000)
    finally:
        store.dispose()
    assert stats.total_events == 2
    assert stats.since == 72_000
    assert all(item["user_agent"] != "Old UA" for item in stats.top_user_agents)
    assert all(item["value"] != "old.example" for item in stats.top_headers)
    assert all(item["path"] != "/p/old" for item in stats.top_paths)
    assert any(
        item["path"] == "/p/recent-a"
        and item["route_name"] == "phone"
        and item["count"] == 1
        for item in stats.top_paths
    )
    assert any(
        item["path"] == "/p/recent-a"
        and item["route_name"] == "tablet"
        and item["count"] == 1
        for item in stats.top_paths
    )
    assert stats.recent is not None
    assert [item["path"] for item in stats.recent] == ["/p/recent-a", "/p/recent-a"]


def test_store_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    store = SQLiteAccessAuditStore(access_config(tmp_path))
    try:
        with store.engine.connect() as connection:
            journal = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
    finally:
        store.dispose()
    assert str(journal).lower() == "wal"
    assert int(timeout) == 5000


def test_format_access_log_line_is_single_line_and_redacted() -> None:
    line = format_access_log_line(event(headers={"authorization": "***", "host": "mpm.example.com"}))
    assert "\n" not in line
    assert "ip=203.0.113.10" in line
    assert "headers=\"authorization=***; host=mpm.example.com\"" in line
    assert "route_name=phone" in line
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
rtk pytest tests/test_access_audit.py -q
```

Expected: import failure for `mihomo_proxy_manager.access_audit`.

- [ ] **Step 3: Implement dataclasses and helpers**

Create `access_audit.py` with these public types and functions:

```python
from __future__ import annotations

import ipaddress
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from loguru import logger
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    desc,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine

from .models import AccessLogConfig, IPNetwork
from .security import redact_secret

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-access-token",
    "x-real-token",
    "cf-access-client-secret",
}

IP_BEARING_HEADERS = {
    "x-forwarded-for",
    "x-real-ip",
    "true-client-ip",
    "cf-connecting-ip",
}


@dataclass(frozen=True)
class ResolvedIP:
    real_ip: str | None
    ip_source: str


@dataclass(frozen=True)
class AccessEvent:
    visited_at: int
    route_name: str | None
    path: str
    companion: str | None
    method: str
    status_code: int
    real_ip: str | None
    ip_source: str
    user_agent: str | None
    headers: dict[str, str]
    target_format: str | None
    response_bytes: int
    duration_ms: int


@dataclass(frozen=True)
class AccessStats:
    enabled: bool
    stats_enabled: bool
    retention_seconds: int | None = None
    privacy: dict[str, Any] | None = None
    total_events: int = 0
    since: int | None = None
    top_ips: list[dict[str, Any]] = field(default_factory=list)
    top_user_agents: list[dict[str, Any]] = field(default_factory=list)
    top_headers: list[dict[str, Any]] = field(default_factory=list)
    top_paths: list[dict[str, Any]] = field(default_factory=list)
    recent: list[dict[str, Any]] | None = None


class AccessAuditStore(Protocol):
    def record(self, event: AccessEvent) -> None: ...
    def cleanup(self, now_ms: int | None = None) -> None: ...
    def stats(self, now_ms: int | None = None) -> AccessStats: ...
    def dispose(self) -> None: ...
```

Implement helpers:

```python
def parse_trusted_networks(values: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"trusted proxy is invalid: {value!r}") from exc
    return tuple(networks)


def now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted_peer(client_host: str | None, trusted_proxies: tuple[IPNetwork, ...]) -> bool:
    if not client_host:
        return False
    peer = _parse_ip(client_host)
    return peer is not None and any(peer in network for network in trusted_proxies)


def resolve_real_ip(
    *,
    client_host: str | None,
    headers: dict[str, str],
    trusted_proxies: tuple[IPNetwork, ...],
    header_order: tuple[str, ...],
) -> ResolvedIP:
    normalized = {name.lower(): value for name, value in headers.items()}
    client_ip = _parse_ip(client_host or "")
    if not _is_trusted_peer(client_host, trusted_proxies):
        return ResolvedIP(str(client_ip), "client-host") if client_ip else ResolvedIP(None, "unknown")
    for header in header_order:
        value = normalized.get(header)
        if not value:
            continue
        if header == "x-forwarded-for":
            for part in value.split(","):
                ip = _parse_ip(part)
                if ip is not None and ip.is_global:
                    return ResolvedIP(str(ip), "x-forwarded-for")
            continue
        ip = _parse_ip(value)
        if ip is not None:
            return ResolvedIP(str(ip), header)
    return ResolvedIP(str(client_ip), "client-host") if client_ip else ResolvedIP(None, "unknown")


def sanitize_headers(
    headers: dict[str, str],
    *,
    max_value_length: int,
    extra_secrets: list[str] | None = None,
) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = raw_name.lower()
        if name in SENSITIVE_HEADER_NAMES:
            sanitized[name] = "***"
            continue
        value = redact_secret(str(raw_value), extra_secrets=extra_secrets)
        if len(value) > max_value_length:
            value = value[: max(0, max_value_length - 3)] + "..."
        sanitized[name] = value
    return sanitized


def mask_ip_for_status(value: str | None) -> str | None:
    if value is None:
        return None
    ip = _parse_ip(value)
    if ip is None:
        return value
    if isinstance(ip, ipaddress.IPv4Address):
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{ip}/64", strict=False)
    return str(network)


def display_header_value(header: str, value: str, *, mask_ips: bool, max_value_length: int) -> str:
    header = header.lower()
    if header == "referer":
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc:
            value = f"{parsed.scheme}://{parsed.netloc}"
    if mask_ips and header in IP_BEARING_HEADERS:
        parts = [part.strip() for part in value.split(",")]
        value = ", ".join(mask_ip_for_status(part) or part for part in parts)
    if len(value) > max_value_length:
        value = value[: max(0, max_value_length - 3)] + "..."
    return value
```

- [ ] **Step 4: Implement SQLAlchemy schema and store**

Use SQLAlchemy Core table:

```python
metadata = MetaData()
access_events = Table(
    "access_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("visited_at", BigInteger, nullable=False, index=True),
    Column("route_name", String, nullable=True, index=True),
    Column("path", String, nullable=False, index=True),
    Column("companion", String, nullable=True),
    Column("method", String, nullable=False),
    Column("status_code", Integer, nullable=False),
    Column("real_ip", String, nullable=True, index=True),
    Column("ip_source", String, nullable=False),
    Column("user_agent", String, nullable=True, index=True),
    Column("headers_json", Text, nullable=False),
    Column("target_format", String, nullable=True),
    Column("response_bytes", Integer, nullable=False),
    Column("duration_ms", Integer, nullable=False),
)
```

Implement `SQLiteAccessAuditStore.__init__()`:

```python
class SQLiteAccessAuditStore:
    def __init__(self, config: AccessLogConfig):
        self.config = config
        self.db_path = config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"timeout": 5},
            future=True,
        )
        self._last_cleanup_ms = 0
        with self.engine.begin() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
            connection.execute(text("PRAGMA busy_timeout=5000"))
            metadata.create_all(connection)
        self.cleanup()
```

Implement `record()`:

```python
def record(self, event: AccessEvent) -> None:
    try:
        with self.engine.begin() as connection:
            connection.execute(
                insert(access_events).values(
                    visited_at=event.visited_at,
                    route_name=event.route_name,
                    path=event.path,
                    companion=event.companion,
                    method=event.method,
                    status_code=event.status_code,
                    real_ip=event.real_ip,
                    ip_source=event.ip_source,
                    user_agent=event.user_agent,
                    headers_json=json.dumps(event.headers, ensure_ascii=False, sort_keys=True),
                    target_format=event.target_format,
                    response_bytes=event.response_bytes,
                    duration_ms=event.duration_ms,
                )
            )
        now_ms = now_epoch_ms()
        if now_ms - self._last_cleanup_ms >= 3_600_000:
            self.cleanup(now_ms=now_ms)
    except Exception as exc:
        logger.warning("access audit record failed: {error}", error=exc)
```

Implement cleanup/stats/dispose. Stats must:

- In `SQLiteAccessAuditStore.stats(now_ms)`, compute `cutoff_ms = now_ms - int(config.retention.total_seconds() * 1000)` and apply `access_events.c.visited_at >= cutoff_ms` to `total_events`, `since`, `top_ips`, `top_user_agents`, `top_headers` source rows, `top_paths`, and `recent`.
- Populate `retention_seconds`, `privacy`, `since`, `top_paths`, and all top lists from the retained window only.
- Set `retention_seconds=int(config.retention.total_seconds())`.
- Set `privacy={"mask_ips": config.status.mask_ips, "include_recent": config.status.include_recent}`.
- Set `since` to the minimum retained `visited_at`, or `None` when no retained rows exist.
- Group `top_paths` by both `path` and `route_name`; include `path`, `route_name`, `count`, and `last_seen`.
- Cleanup deletes old rows, but stats must not depend on cleanup having run.
- Use `config.status.top_limit`.
- Use `config.headers.stats_max_rows` for Python header aggregation query ordered by `visited_at DESC`.
- Aggregate `top_headers` only for exact lowercase names in `config.headers.stats_allowlist`; do not include non-allowlisted headers such as `referer` or `x-custom-token`.
- Apply `mask_ip_for_status()` when `config.status.mask_ips`.
- Apply `display_header_value()` for header aggregates.
- Include recent rows only when `config.status.include_recent`.
- Never include `headers_json` in output rows.

- [ ] **Step 5: Implement access log formatter**

```python
def _quote(value: object) -> str:
    text_value = "" if value is None else str(value)
    return '"' + text_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def format_access_log_line(event: AccessEvent) -> str:
    visited = datetime.fromtimestamp(event.visited_at / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = "; ".join(f"{name}={value}" for name, value in sorted(event.headers.items()))
    return " ".join(
        [
            visited,
            f"ip={event.real_ip or '-'}",
            f"ip_source={event.ip_source}",
            f"method={event.method}",
            f"path={event.path}",
            f"route_name={event.route_name or '-'}",
            f"companion={event.companion or 'null'}",
            f"target={event.target_format or '-'}",
            f"status={event.status_code}",
            f"bytes={event.response_bytes}",
            f"duration_ms={event.duration_ms}",
            f"ua={_quote(event.user_agent)}",
            f"headers={_quote(headers)}",
        ]
    )
```

- [ ] **Step 6: Run tests**

Run:

```bash
rtk pytest tests/test_access_audit.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/access_audit.py tests/test_access_audit.py
rtk git commit -m "feat(access): add sqlite audit store"
```

## Task 3: Separate Access Log Sink

**Files:**

- Modify: `src/mihomo_proxy_manager/logging.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write failing sink-isolation tests**

Add tests that configure normal file logging and access file logging, then assert access records do not enter normal log and normal records do not enter access log.

```python
from dataclasses import replace
from pathlib import Path

from loguru import logger

from mihomo_proxy_manager.access_audit import AccessEvent, format_access_log_line
from mihomo_proxy_manager.logging import configure_logging
from mihomo_proxy_manager.models import AccessLogFileConfig


def test_access_log_sink_is_separate(tmp_path: Path) -> None:
    normal_path = tmp_path / "normal.log"
    access_path = tmp_path / "access.log"
    app_config = _minimal_config(tmp_path)
    config = replace(
        app_config,
        logging_file=replace(
            app_config.logging_file,
            enabled=True,
            path=normal_path,
        ),
        access_log=replace(
            app_config.access_log,
            enabled=True,
            file=AccessLogFileConfig(enabled=True, path=access_path),
        ),
    )
    configure_logging(config)
    logger.info("normal message")
    logger.bind(access_log=True).info("access message")
    logger.complete()
    assert "normal message" in normal_path.read_text(encoding="utf-8")
    assert "access message" not in normal_path.read_text(encoding="utf-8")
    assert "access message" in access_path.read_text(encoding="utf-8")
    assert "normal message" not in access_path.read_text(encoding="utf-8")


def test_access_log_disabled_creates_no_access_file(tmp_path: Path) -> None:
    access_path = tmp_path / "access.log"
    app_config = _minimal_config(tmp_path)
    config = replace(
        app_config,
        access_log=replace(
            app_config.access_log,
            enabled=False,
            file=AccessLogFileConfig(enabled=True, path=access_path),
        ),
    )
    configure_logging(config)
    logger.bind(access_log=True).info("access message")
    logger.complete()
    assert not access_path.exists()


def test_access_log_record_keeps_route_path(tmp_path: Path) -> None:
    access_path = tmp_path / "access.log"
    app_config = _minimal_config(tmp_path)
    config = replace(
        app_config,
        access_log=replace(
            app_config.access_log,
            enabled=True,
            file=AccessLogFileConfig(enabled=True, path=access_path),
        ),
    )
    route = config.routes["phone"]
    configure_logging(config)
    logger.bind(access_log=True).info(
        format_access_log_line(
            AccessEvent(
                visited_at=1_790_000_000_000,
                route_name=route.name,
                path=route.path,
                companion=None,
                method="GET",
                status_code=200,
                real_ip="203.0.113.10",
                ip_source="client-host",
                user_agent="Surfboard/2.24",
                headers={"host": "mpm.example.com", "user-agent": "Surfboard/2.24"},
                target_format="surfboard",
                response_bytes=1234,
                duration_ms=18,
            )
        )
    )
    logger.complete()
    contents = access_path.read_text(encoding="utf-8")
    assert f"path={route.path}" in contents
    assert "path=***" not in contents
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
rtk pytest tests/test_logging.py -q
```

Expected: access log sink is missing or mixed into normal sink.

- [ ] **Step 3: Implement filters and access sink**

In `configure_logging()`:

```python
def _normal_log_filter(record: "Record") -> bool:
    return record["extra"].get("access_log") is not True


def _access_log_filter(record: "Record") -> bool:
    return record["extra"].get("access_log") is True
```

Apply `filter=_normal_log_filter` to console and normal file sinks. Add access sink after normal sinks:

```python
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
```

Keep `logger.remove()` and `logger.configure(patcher=...)` at start.

Access log redaction rule:

- Existing `_collect_secret_values()` includes configured route paths for normal log protection. Do not let those route-path secrets redact `logger.bind(access_log=True)` records, or `format_access_log_line()` will become `path=***`.
- Implement this by splitting collected secret values into route-path secrets and global secrets, or by adding an `include_route_paths`/`redact_route_paths` flag used by `_redact_record()`.
- For access records, still redact source URLs, `server.status_path`, configured headers/secrets, and sensitive values. Only configured subscription route paths are allowed to remain visible in access records.

- [ ] **Step 4: Run logging tests**

Run:

```bash
rtk pytest tests/test_logging.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/mihomo_proxy_manager/logging.py tests/test_logging.py
rtk git commit -m "feat(access): separate access log sink"
```

## Task 4: App Route Recording And Store Lifecycle

**Files:**

- Modify: `src/mihomo_proxy_manager/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing app integration tests**

Add a fake store to `tests/test_app.py`.

```python
class FakeAccessAuditStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events = []
        self.disposed = False

    def record(self, event) -> None:
        if self.fail:
            raise RuntimeError("audit failed")
        self.events.append(event)

    def cleanup(self, now_ms=None) -> None:
        return None

    def stats(self, now_ms=None):
        raise AssertionError("not used")

    def dispose(self) -> None:
        self.disposed = True
```

Add tests:

```python
@pytest.mark.asyncio
async def test_access_audit_records_success_route(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path, headers={"cf-connecting-ip": "203.0.113.10", "user-agent": "Surfboard/2.24"})

    assert response.status_code == 200
    assert len(store.events) == 1
    event = store.events[0]
    assert event.route_name == "phone"
    assert event.path == path
    assert event.companion is None
    assert event.status_code == 200
    assert event.target_format in {"provider", "surfboard", "quantumult-x", "xray-uri"}
    assert event.response_bytes == len(response.content)
    assert event.duration_ms >= 0
    assert event.headers["user-agent"] == "Surfboard/2.24"


@pytest.mark.asyncio
async def test_access_audit_records_forbidden_and_bad_target(tmp_path) -> None:
    config = auto_app_config(tmp_path, allowed_user_agents=("allowed",))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        forbidden = client.get(path, headers={"user-agent": "blocked"})
        bad = client.get(f"{path}?target=unknown", headers={"user-agent": "allowed"})

    assert forbidden.status_code == 403
    assert bad.status_code == 400
    assert [event.status_code for event in store.events] == [403, 400]
    assert store.events[1].target_format is None


@pytest.mark.asyncio
async def test_access_audit_records_422_for_unsupported_nodes(tmp_path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="xray-uri", encoding="plain"),
    )
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set(
        "airport_a",
        source_cache_with_nodes(
            ProxyRecord(
                "airport_a",
                {"name": "bad", "type": "tuic", "server": "example.com", "port": 443},
            )
        ),
    )
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 422
    assert store.events[-1].route_name == "phone"
    assert store.events[-1].path == path
    assert store.events[-1].status_code == 422


@pytest.mark.asyncio
async def test_access_audit_records_503(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    empty_store = JsonSourceCacheStore(config.cache)
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=empty_store, refresher=None, scheduler=None, access_audit_store=store)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 503
    assert store.events[-1].route_name == "phone"
    assert store.events[-1].status_code == 503


@pytest.mark.asyncio
async def test_access_audit_excludes_health_status_and_unknown(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)

    with TestClient(app) as client:
        client.get(config.server.health_path)
        client.get(config.server.status_path)
        client.get(f"{config.server.status_path}/api")
        client.get("/unknown")

    assert store.events == []


@pytest.mark.asyncio
async def test_access_audit_failure_does_not_change_response(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    await cache_store.set("airport_a", source_cache_with_nodes(ss_node()))
    store = FakeAccessAuditStore(fail=True)
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_access_audit_store_disposed_on_lifespan_shutdown(tmp_path) -> None:
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    store = FakeAccessAuditStore()
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None, access_audit_store=store)

    with TestClient(app):
        pass

    assert store.disposed is True
```

Use existing `tests/test_app.py` helpers: `load_config(config_file(tmp_path))`, `JsonSourceCacheStore`, `source_cache_with_nodes()`, `ss_node()`, `app_config_with_route_output()`, and `TestClient`. Do not use a global `client` fixture, `app_config_with_route`, `cache_store`, `empty_cache_store`, `/p/token`, route name `main`, or `LifespanManager`.

- [ ] **Step 2: Run app tests to verify failure**

Run:

```bash
rtk pytest tests/test_app.py -q
```

Expected: `create_app()` rejects `access_audit_store`.

- [ ] **Step 3: Add optional app dependency and finalizer**

In `create_app()` signature:

```python
from .access_audit import AccessAuditStore, AccessEvent, format_access_log_line, now_epoch_ms, resolve_real_ip, sanitize_headers

def create_app(..., access_audit_store: AccessAuditStore | None = None) -> Starlette:
```

Add helpers inside `create_app()`:

```python
async def _record_access_event(event: AccessEvent) -> None:
    if access_audit_store is None or not config.access_log.enabled:
        return
    try:
        await asyncio.to_thread(access_audit_store.record, event)
        if config.access_log.file.enabled:
            logger.bind(access_log=True).info(format_access_log_line(event))
    except Exception as exc:
        logger.warning("access audit write failed: {error}", error=exc)


def _response_bytes(response: Response) -> int:
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return len(body)
    header_value = response.headers.get("content-length")
    if header_value and header_value.isdigit():
        return int(header_value)
    return 0


def _access_event(
    *,
    request: Request,
    route: RouteConfig,
    companion: str | None,
    start_ms: int,
    response: Response,
    target_format: str | None,
) -> AccessEvent:
    resolved = resolve_real_ip(
        client_host=request.client.host if request.client else None,
        headers=dict(request.headers),
        trusted_proxies=config.access_log.trusted_proxies,
        header_order=config.access_log.real_ip_headers,
    )
    headers = sanitize_headers(
        dict(request.headers),
        max_value_length=config.access_log.headers.max_value_length,
        extra_secrets=secrets,
    )
    return AccessEvent(
        visited_at=start_ms,
        route_name=route.name,
        path=request.url.path,
        companion=companion,
        method=request.method,
        status_code=response.status_code,
        real_ip=resolved.real_ip,
        ip_source=resolved.ip_source,
        user_agent=headers.get("user-agent"),
        headers=headers,
        target_format=target_format,
        response_bytes=_response_bytes(response),
        duration_ms=max(0, now_epoch_ms() - start_ms),
    )
```

- [ ] **Step 4: Refactor provider to finalize matched routes**

Inside `provider(request)`, keep unknown 404 before auditing:

```python
route_match = route_by_path.get(request.url.path)
if route_match is None:
    ...
    return PlainTextResponse("not found", status_code=404)
route, companion = route_match
start_ms = now_epoch_ms()
target_format_for_audit: str | None = None
response: Response | None = None
try:
    ...
    output_format, target_error = _effective_output_format(...)
    if target_error is not None or output_format is None:
        response = PlainTextResponse(target_error or "unsupported target", status_code=400)
        return response
    target_format_for_audit = output_format
    ...
    response = Response(...)
    return response
finally:
    if response is not None:
        await _record_access_event(
            _access_event(
                request=request,
                route=route,
                companion=companion,
                start_ms=start_ms,
                response=response,
                target_format=target_format_for_audit,
            )
        )
```

Every existing early `return PlainTextResponse(...)` inside matched route handling must assign `response` first, then return it.

- [ ] **Step 5: Dispose store on shutdown**

In lifespan `finally`:

```python
if access_audit_store is not None:
    try:
        await asyncio.to_thread(access_audit_store.dispose)
    except Exception as exc:
        logger.warning("access audit store dispose failed: {error}", error=exc)
```

Run after scheduler/background cleanup is acceptable; it must run before returning from lifespan shutdown.

- [ ] **Step 6: Run app tests**

Run:

```bash
rtk pytest tests/test_app.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/app.py tests/test_app.py
rtk git commit -m "feat(access): record route access events"
```

## Task 5: Status API And HTML Access Stats

**Files:**

- Modify: `src/mihomo_proxy_manager/status.py`
- Modify: `src/mihomo_proxy_manager/app.py`
- Test: `tests/test_app.py` or `tests/test_status.py`

- [ ] **Step 1: Write failing status tests**

Add a stats fake:

```python
from mihomo_proxy_manager.access_audit import AccessStats


class FakeStatsStore:
    def stats(self, now_ms=None) -> AccessStats:
        return AccessStats(
            enabled=True,
            stats_enabled=True,
            retention_seconds=2_592_000,
            privacy={"mask_ips": True, "include_recent": False},
            total_events=2,
            since=1_789_000_000_000,
            top_ips=[{"real_ip": "203.0.113.0/24", "count": 2, "last_seen": 1_790_000_000_000}],
            top_user_agents=[{"user_agent": "Surfboard/2.24", "count": 2, "last_seen": 1_790_000_000_000}],
            top_headers=[{"header": "cf-ipcountry", "value": "US", "count": 2, "last_seen": 1_790_000_000_000}],
            top_paths=[{"path": "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", "route_name": "phone", "count": 2, "last_seen": 1_790_000_000_000}],
            recent=None,
        )
```

Tests:

```python
def _status_config_and_store(tmp_path):
    config = load_config(config_file(tmp_path))
    cache_store = JsonSourceCacheStore(config.cache)
    return config, cache_store


@pytest.mark.asyncio
async def test_status_api_includes_access_stats(tmp_path) -> None:
    config, cache_store = _status_config_and_store(tmp_path)
    data = await build_status(cache_store, config, access_audit_store=FakeStatsStore())
    assert data["access"]["enabled"] is True
    assert data["access"]["stats_enabled"] is True
    assert data["access"]["top_ips"][0]["real_ip"] == "203.0.113.0/24"
    assert data["access"]["top_paths"][0]["route_name"] == "phone"
    assert "route" not in data["access"]["top_paths"][0]
    assert "recent" not in data["access"]


@pytest.mark.asyncio
async def test_status_api_access_disabled(tmp_path) -> None:
    app_config, cache_store = _status_config_and_store(tmp_path)
    config = replace(app_config, access_log=replace(app_config.access_log, enabled=False))
    data = await build_status(cache_store, config)
    assert data["access"] == {"enabled": False}


@pytest.mark.asyncio
async def test_status_api_stats_disabled_when_no_store(tmp_path) -> None:
    app_config, cache_store = _status_config_and_store(tmp_path)
    data = await build_status(cache_store, app_config, access_audit_store=None)
    assert data["access"] == {"enabled": True, "stats_enabled": False}


@pytest.mark.asyncio
async def test_status_api_stats_disabled_by_config(tmp_path) -> None:
    app_config, cache_store = _status_config_and_store(tmp_path)
    config = replace(app_config, access_log=replace(app_config.access_log, status=replace(app_config.access_log.status, enabled=False)))
    data = await build_status(cache_store, config, access_audit_store=FakeStatsStore())
    assert data["access"] == {"enabled": True, "stats_enabled": False}


def test_status_html_renders_access_stats() -> None:
    html = render_status_html({"generated_at": "...", "summary": {}, "sources": [], "routes": [], "access": FakeStatsStore().stats().__dict__})
    assert "Access" in html
    assert "203.0.113.0/24" in html
    assert "headers_json" not in html
```

If these tests live in `tests/test_app.py`, reuse existing imports and helpers. If they live in a new `tests/test_status.py`, copy the minimal `config_file()` helper or build an equivalent temp config with route name `phone`; do not rely on nonexistent `app_config` or `cache_store` fixtures.

- [ ] **Step 2: Run status tests to verify failure**

Run:

```bash
rtk pytest tests/test_app.py -q
```

or, if using a new file:

```bash
rtk pytest tests/test_status.py -q
```

Expected: `build_status()` rejects `access_audit_store` or no access key exists.

- [ ] **Step 3: Extend `build_status()`**

Change signature:

```python
from typing import Protocol

from .access_audit import AccessStats


class AccessStatsStore(Protocol):
    def stats(self, now_ms: int | None = None) -> AccessStats: ...


async def build_status(
    cache_store: SourceCacheStore,
    config: AppConfig,
    *,
    extra_secrets: list[str] | None = None,
    access_audit_store: AccessStatsStore | None = None,
) -> dict[str, Any]:
```

Add helper:

```python
async def _access_status(config: AppConfig, access_audit_store: AccessStatsStore | None) -> dict[str, Any]:
    if not config.access_log.enabled:
        return {"enabled": False}
    if not config.access_log.status.enabled or access_audit_store is None:
        return {"enabled": True, "stats_enabled": False}
    try:
        stats = await asyncio.to_thread(access_audit_store.stats)
    except Exception as exc:
        logger.warning("access audit stats failed: {error}", error=exc)
        return {"enabled": True, "stats_enabled": False}
    data = {
        "enabled": stats.enabled,
        "stats_enabled": stats.stats_enabled,
        "retention_seconds": stats.retention_seconds,
        "privacy": stats.privacy,
        "total_events": stats.total_events,
        "since": stats.since,
        "top_ips": stats.top_ips or [],
        "top_user_agents": stats.top_user_agents or [],
        "top_headers": stats.top_headers or [],
        "top_paths": stats.top_paths or [],
    }
    if stats.recent is not None:
        data["recent"] = stats.recent
    return data
```

Import `asyncio` and `logger` if not present. Add `"access": await _access_status(...)` to returned dict.

- [ ] **Step 4: Pass store from app status route**

In `app.py` status handler:

```python
data = await build_status(
    cache_store,
    config,
    extra_secrets=secrets,
    access_audit_store=access_audit_store,
)
```

- [ ] **Step 5: Render HTML section**

In `status.py`, add `_access_section(data)` and include it in rendered body.

```python
def _access_section(data: dict[str, Any]) -> str:
    access = data.get("access", {})
    if not access.get("enabled"):
        return "<section><h2>Access</h2><p class=\"muted\">disabled</p></section>"
    if not access.get("stats_enabled"):
        return "<section><h2>Access</h2><p class=\"muted\">stats disabled</p></section>"
    total = html.escape(str(access.get("total_events", 0)))

    def rows(items: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
        if not items:
            return '<tr><td colspan="4" class="muted">-</td></tr>'
        rendered = []
        for item in items:
            cells = "".join(f"<td>{html.escape(str(item.get(column, '-')))}</td>" for column in columns)
            rendered.append(f"<tr>{cells}</tr>")
        return "".join(rendered)

    recent = ""
    if access.get("recent") is not None:
        recent = f"""
        <h3>Recent</h3>
        <table><tbody>{rows(access.get("recent", []), ("visited_at", "path", "status_code", "real_ip"))}</tbody></table>
        """
    return f"""
    <section>
      <h2>Access</h2>
      <div class="metric"><div class="metric-value">{total}</div><div class="metric-label">events</div></div>
      <h3>Top IPs</h3>
      <table><tbody>{rows(access.get("top_ips", []), ("real_ip", "count", "last_seen"))}</tbody></table>
      <h3>User-Agents</h3>
      <table><tbody>{rows(access.get("top_user_agents", []), ("user_agent", "count", "last_seen"))}</tbody></table>
      <h3>Headers</h3>
      <table><tbody>{rows(access.get("top_headers", []), ("header", "value", "count", "last_seen"))}</tbody></table>
      <h3>Paths</h3>
      <table><tbody>{rows(access.get("top_paths", []), ("path", "route_name", "count", "last_seen"))}</tbody></table>
      {recent}
    </section>
    """
```

Adapt classes to existing HTML style but preserve content and escaping.

- [ ] **Step 6: Run status/app tests**

Run:

```bash
rtk pytest tests/test_app.py -q
```

and, if created:

```bash
rtk pytest tests/test_status.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/status.py src/mihomo_proxy_manager/app.py tests/test_app.py tests/test_status.py
rtk git commit -m "feat(access): expose access stats"
```

If `tests/test_status.py` does not exist, omit it from `git add`.

## Task 6: CLI Integration, Docs, Examples, Full Verification

**Files:**

- Modify: `src/mihomo_proxy_manager/cli.py`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `examples/config.toml`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing CLI smoke tests**

Add tests:

```python
import asyncio

from mihomo_proxy_manager.cli import _build_runtime


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


def test_serve_runtime_initializes_access_store_when_enabled(monkeypatch, tmp_path: Path) -> None:
    created = {}

    class FakeStore:
        def __init__(self, config):
            created["db_path"] = config.db_path
        def cleanup(self, now_ms=None): pass
        def record(self, event): pass
        def stats(self, now_ms=None): raise AssertionError("not used")
        def dispose(self): pass

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


def test_serve_runtime_does_not_initialize_access_store_when_disabled(monkeypatch, tmp_path: Path) -> None:
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


def test_build_runtime_does_not_initialize_access_store_by_default(monkeypatch, tmp_path: Path) -> None:
    def fail_store(config):
        raise AssertionError("store should not be created")

    monkeypatch.setattr("mihomo_proxy_manager.cli.SQLiteAccessAuditStore", fail_store)
    runtime = asyncio.run(_build_runtime(str(_write_cli_config(tmp_path)), debug=False))
    try:
        assert runtime.access_audit_store is None
    finally:
        asyncio.run(runtime.client.aclose())
```

Do not use Typer `runner`, `cli_app`, or `--dry-run`; this repo uses argparse and has no serve dry-run path.

- [ ] **Step 2: Run CLI tests to verify failure**

Run:

```bash
rtk pytest tests/test_cli_smoke.py -q
```

Expected: CLI does not initialize or pass store.

- [ ] **Step 3: Integrate store in runtime**

In `cli.py`, import:

```python
from dataclasses import dataclass

from .access_audit import AccessAuditStore, SQLiteAccessAuditStore
from .models import AppConfig, HttpConfig
```

Replace the returned tuple with a small runtime dataclass so tests and commands can access named fields:

```python
@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    cache_store: JsonSourceCacheStore
    client: httpx.AsyncClient
    refresher: SourceRefresher
    access_audit_store: AccessAuditStore | None = None
```

Add an opt-in access-audit parameter to `_build_runtime()`:

```python
async def _build_runtime(
    config_path: str, *, debug: bool = False, access_audit: bool = False
) -> Runtime:
    ...
configure_logging(config, debug=debug)
access_audit_store = None
if access_audit and config.access_log.enabled:
    access_audit_store = SQLiteAccessAuditStore(config.access_log)
...
return Runtime(..., access_audit_store=access_audit_store)
```

`_build_runtime()` must not initialize `SQLiteAccessAuditStore` by default. Pass `access_audit=True` only from `_cmd_serve()`. Leave `_cmd_refresh()` on the default `False` path. `Runtime` must have only `config`, `cache_store`, `client`, `refresher`, and `access_audit_store`; do not add `scheduler` to `Runtime`.

In `_cmd_serve()`, create the serving-only scheduler after `_build_runtime()` and pass that local variable into `create_app()`:

```python
scheduler = RefreshScheduler(runtime.config, runtime.refresher)
app = create_app(
    runtime.config,
    cache_store=runtime.cache_store,
    refresher=runtime.refresher,
    scheduler=scheduler,
    access_audit_store=runtime.access_audit_store,
)
```

Do not open SQLite in `mpm check` or `mpm refresh`; `load_config()` and `check_filesystem()` are enough for `check`, and refresh only needs cache/refresher dependencies.

- [ ] **Step 4: Update docs and example**

In `README.md`, `README_EN.md`, and `examples/config.toml`, add a section/config block covering:

```toml
[access_log]
enabled = true
db_path = "data/access/access.sqlite3"
retention = "30d"
trusted_proxies = ["127.0.0.1/32", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
real_ip_headers = ["cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"]

[access_log.file]
enabled = true
path = "logs/access.log"
rotation = "10 MB"
retention = "30 days"
compression = "gz"

[access_log.headers]
max_value_length = 512
stats_allowlist = ["user-agent", "host", "cf-ipcountry", "cf-ray"]
stats_max_rows = 5000

[access_log.status]
enabled = true
mask_ips = true
include_recent = false
recent_limit = 20
top_limit = 20
```

Docs must state:

- SQLite stores route access events for 30 days by default.
- Access audit stores personal data, including IP addresses, User-Agents, route paths, timestamps, selected sanitized headers, response status, response size, and duration.
- `logs/access.log` is human-readable and separate from normal logs.
- `trusted_proxies` gates `CF-Connecting-IP`, `True-Client-IP`, `X-Forwarded-For`, and `X-Real-IP`.
- Default `trusted_proxies` trusts loopback plus RFC1918 Docker/LAN ranges for zero-config reverse proxy deployments. If the app is reachable directly from private/LAN clients, set exact reverse proxy IPs/CIDRs; otherwise clients can spoof `CF-Connecting-IP`, `True-Client-IP`, `X-Forwarded-For`, or `X-Real-IP`.
- Reverse proxy must overwrite/sanitize `X-Forwarded-For`; otherwise remove it from `real_ip_headers`.
- Header values are redacted/truncated before storage.
- Status page shows aggregate stats only; full `headers_json` is never shown.
- Access stats are exposed on `status_path`; keep `status_path` high entropy and non-public, and do not allowlist high-entropy private headers.
- IPs are masked in status by default; recent rows are hidden by default.
- Disable with:

```toml
[access_log]
enabled = false
```

- `mpm check` may create directories through existing filesystem validation, but does not create DB/log files.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
rtk pytest tests/test_cli_smoke.py tests/test_config.py tests/test_logging.py tests/test_access_audit.py tests/test_app.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full verification**

Run:

```bash
rtk make lint
rtk make typecheck
rtk pytest -q
rtk make check
```

Expected: all pass. If `make check` already includes some earlier commands, still run it because this repo treats it as release-quality verification.

- [ ] **Step 7: Verify CI-style dependency path**

Run in a temporary venv if practical:

```bash
rtk python -m venv /tmp/mpm-access-audit-venv
rtk /tmp/mpm-access-audit-venv/bin/pip install -r requirements.txt
rtk /tmp/mpm-access-audit-venv/bin/pip install -e . --no-deps
rtk /tmp/mpm-access-audit-venv/bin/python -c "import sqlalchemy; import mihomo_proxy_manager.access_audit"
```

Expected: imports succeed.

- [ ] **Step 8: Commit**

```bash
rtk git add src/mihomo_proxy_manager/cli.py tests/test_cli_smoke.py README.md README_EN.md examples/config.toml
rtk git commit -m "docs(access): document audit logging"
```

If CLI changed in Step 3, use:

```bash
rtk git commit -m "feat(access): wire audit store into cli"
```

Then commit docs separately:

```bash
rtk git add README.md README_EN.md examples/config.toml
rtk git commit -m "docs(access): document audit logging"
```

## Final Integration Checklist

- [ ] Run `rtk git status --short`; only intended files may be changed.
- [ ] Run `rtk git log --oneline -6`; confirm each task has a focused commit.
- [ ] Run `rtk make lint`.
- [ ] Run `rtk make typecheck`.
- [ ] Run `rtk pytest -q`.
- [ ] Run `rtk make check`.
- [ ] Inspect `README.md`, `README_EN.md`, and `examples/config.toml` for the full `[access_log]` block.
- [ ] Confirm status JSON uses `route_name`, never `route`, inside access stats.
- [ ] Confirm `headers_json` appears only in SQLite code/tests and never in status HTML/API fixtures.
- [ ] Confirm `access_log.enabled = false` creates no SQLite store and no access log sink.

## Self-Review

Spec coverage:

- Config/defaults/validation/dependencies: Task 1.
- SQLAlchemy Core SQLite schema, WAL, busy timeout, cleanup, stats: Task 2.
- Real IP resolution and header sanitization: Task 2.
- Human-readable separate access file log: Task 3.
- Matched route lifecycle, all response outcomes, non-invasive failure handling: Task 4.
- Store disposal: Task 4.
- Status API and HTML privacy behavior: Task 5.
- CLI startup/check behavior: Task 6.
- README/README_EN/examples updates: Task 6.

Placeholder scan:

- No unresolved placeholder markers remain.
- Every task includes concrete tests, implementation targets, commands, and expected outcomes.

Type consistency:

- `access_audit_store` is the optional dependency name in `create_app()` and `build_status()`.
- Access stats use `route_name` consistently.
- Store API is `record()`, `cleanup()`, `stats()`, `dispose()`.
