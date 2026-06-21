"""Access audit helpers, SQLite storage, and human-readable formatting."""

from __future__ import annotations

import ipaddress
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    insert,
    select,
    text,
)

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


def _is_trusted_peer(
    client_host: str | None, trusted_proxies: tuple[IPNetwork, ...]
) -> bool:
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
        if client_ip is None:
            return ResolvedIP(None, "unknown")
        return ResolvedIP(str(client_ip), "client-host")

    for header in header_order:
        value = normalized.get(header.lower())
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
    if client_ip is None:
        return ResolvedIP(None, "unknown")
    return ResolvedIP(str(client_ip), "client-host")


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
        if "***" not in value and len(value) > max_value_length:
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


def display_header_value(
    header: str, value: str, *, mask_ips: bool, max_value_length: int
) -> str:
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


def _retention_ms(config: AccessLogConfig) -> int:
    return int(config.retention.total_seconds() * 1000)


def _row_mapping(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def _sort_counted(items: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items.values(),
        key=lambda item: (-int(item["count"]), -int(item["last_seen"]), str(item)),
    )


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
                        headers_json=json.dumps(
                            event.headers, ensure_ascii=False, sort_keys=True
                        ),
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

    def cleanup(self, now_ms: int | None = None) -> None:
        now_ms = now_epoch_ms() if now_ms is None else now_ms
        cutoff_ms = now_ms - _retention_ms(self.config)
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(access_events).where(
                        access_events.c.visited_at < cutoff_ms
                    )
                )
            self._last_cleanup_ms = now_ms
        except Exception as exc:
            logger.warning("access audit cleanup failed: {error}", error=exc)

    def stats(self, now_ms: int | None = None) -> AccessStats:
        if not self.config.enabled:
            return AccessStats(enabled=False, stats_enabled=False)
        if not self.config.status.enabled:
            return AccessStats(enabled=True, stats_enabled=False)

        now_ms = now_epoch_ms() if now_ms is None else now_ms
        cutoff_ms = now_ms - _retention_ms(self.config)
        retained = access_events.c.visited_at >= cutoff_ms
        limit = self.config.status.top_limit
        retention_seconds = int(self.config.retention.total_seconds())
        privacy = {
            "mask_ips": self.config.status.mask_ips,
            "include_recent": self.config.status.include_recent,
        }

        with self.engine.connect() as connection:
            rows = [
                _row_mapping(row)
                for row in connection.execute(
                    select(access_events)
                    .where(retained)
                    .order_by(access_events.c.visited_at.desc(), access_events.c.id.desc())
                )
            ]

        total_events = len(rows)
        since = min((int(row["visited_at"]) for row in rows), default=None)
        return AccessStats(
            enabled=True,
            stats_enabled=True,
            retention_seconds=retention_seconds,
            privacy=privacy,
            total_events=total_events,
            since=since,
            top_ips=self._top_ips(rows, limit),
            top_user_agents=self._top_user_agents(rows, limit),
            top_headers=self._top_headers(rows, limit),
            top_paths=self._top_paths(rows, limit),
            recent=self._recent(rows) if self.config.status.include_recent else None,
        )

    def dispose(self) -> None:
        self.engine.dispose()

    def _top_ips(self, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        items: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            raw_ip = row["real_ip"]
            if raw_ip is None:
                continue
            real_ip = (
                mask_ip_for_status(raw_ip) if self.config.status.mask_ips else raw_ip
            )
            key = (real_ip,)
            item = items.setdefault(
                key,
                {"real_ip": real_ip, "count": 0, "last_seen": int(row["visited_at"])},
            )
            item["count"] += 1
            item["last_seen"] = max(item["last_seen"], int(row["visited_at"]))
        return _sort_counted(items)[:limit]

    def _top_user_agents(
        self, rows: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        items: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            user_agent = row["user_agent"]
            if user_agent is None:
                continue
            key = (user_agent,)
            item = items.setdefault(
                key,
                {
                    "user_agent": user_agent,
                    "count": 0,
                    "last_seen": int(row["visited_at"]),
                },
            )
            item["count"] += 1
            item["last_seen"] = max(item["last_seen"], int(row["visited_at"]))
        return _sort_counted(items)[:limit]

    def _top_headers(
        self, rows: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        allowlist = set(self.config.headers.stats_allowlist)
        items: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows[: self.config.headers.stats_max_rows]:
            try:
                headers = json.loads(str(row["headers_json"]))
            except json.JSONDecodeError:
                continue
            if not isinstance(headers, dict):
                continue
            for header, raw_value in headers.items():
                if not isinstance(header, str) or header not in allowlist:
                    continue
                value = display_header_value(
                    header,
                    str(raw_value),
                    mask_ips=self.config.status.mask_ips,
                    max_value_length=self.config.headers.max_value_length,
                )
                key = (header, value)
                item = items.setdefault(
                    key,
                    {
                        "header": header,
                        "value": value,
                        "count": 0,
                        "last_seen": int(row["visited_at"]),
                    },
                )
                item["count"] += 1
                item["last_seen"] = max(item["last_seen"], int(row["visited_at"]))
        return _sort_counted(items)[:limit]

    def _top_paths(
        self, rows: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        items: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(dict)
        for row in rows:
            key = (row["path"], row["route_name"])
            if not items[key]:
                items[key] = {
                    "path": row["path"],
                    "route_name": row["route_name"],
                    "count": 0,
                    "last_seen": int(row["visited_at"]),
                }
            items[key]["count"] += 1
            items[key]["last_seen"] = max(
                items[key]["last_seen"], int(row["visited_at"])
            )
        return _sort_counted(items)[:limit]

    def _recent(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        recent_rows = rows[: self.config.status.recent_limit]
        recent: list[dict[str, Any]] = []
        for row in recent_rows:
            real_ip = row["real_ip"]
            if self.config.status.mask_ips:
                real_ip = mask_ip_for_status(real_ip)
            recent.append(
                {
                    "visited_at": int(row["visited_at"]),
                    "route_name": row["route_name"],
                    "path": row["path"],
                    "companion": row["companion"],
                    "method": row["method"],
                    "status_code": int(row["status_code"]),
                    "real_ip": real_ip,
                    "ip_source": row["ip_source"],
                    "user_agent": row["user_agent"],
                    "target_format": row["target_format"],
                    "response_bytes": int(row["response_bytes"]),
                    "duration_ms": int(row["duration_ms"]),
                }
            )
        return recent


def _quote(value: object) -> str:
    text_value = "" if value is None else str(value)
    escaped = text_value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def format_access_log_line(event: AccessEvent) -> str:
    visited = datetime.fromtimestamp(event.visited_at / 1000, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    headers = "; ".join(
        f"{name}={value}" for name, value in sorted(event.headers.items())
    )
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
