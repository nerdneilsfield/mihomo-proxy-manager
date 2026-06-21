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
        header_order=(
            "cf-connecting-ip",
            "true-client-ip",
            "x-forwarded-for",
            "x-real-ip",
        ),
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
    assert (
        mask_ip_for_status("2001:db8:abcd:1234:5678::1")
        == "2001:db8:abcd:1234::/64"
    )
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
        store.record(
            event(
                headers={
                    "user-agent": "Surfboard/2.24",
                    "host": "mpm.example.com",
                    "referer": "https://example.com/path?token=secret",
                    "x-custom-token": "***",
                    "x-forwarded-for": "8.8.8.8, 10.0.0.1",
                }
            )
        )
        store.record(
            event(
                visited_at=1_790_000_001_000,
                real_ip="2001:db8:abcd:1234::1",
                user_agent="Quantumult X",
                path="/p/token-import",
                companion="import",
                target_format="quantumult-x",
            )
        )
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
    assert any(
        item["header"] == "host" and item["value"] == "mpm.example.com"
        for item in stats.top_headers
    )
    assert any(
        item["header"] == "x-forwarded-for"
        and item["value"] == "8.8.8.0/24, 10.0.0.0/24"
        for item in stats.top_headers
    )
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


def test_record_and_cleanup_log_failures_without_raising(tmp_path: Path) -> None:
    store = SQLiteAccessAuditStore(access_config(tmp_path))
    try:
        store.dispose()
        store.record(event())
        store.cleanup(now_ms=1_790_000_000_000)
    finally:
        store.dispose()


def test_referer_allowlist_displays_origin_only(tmp_path: Path) -> None:
    config = access_config(
        tmp_path,
        headers=AccessLogHeadersConfig(
            stats_allowlist=("referer",),
            stats_max_rows=100,
        ),
        status=AccessLogStatusConfig(include_recent=True, recent_limit=2, top_limit=5),
    )
    store = SQLiteAccessAuditStore(config)
    try:
        store.record(
            event(headers={"referer": "https://example.com/path?token=secret"})
        )
        stats = store.stats(now_ms=1_790_000_002_000)
    finally:
        store.dispose()
    assert stats.top_headers == [
        {
            "header": "referer",
            "value": "https://example.com",
            "count": 1,
            "last_seen": 1_790_000_000_000,
        }
    ]


def test_format_access_log_line_is_single_line_and_redacted() -> None:
    line = format_access_log_line(
        event(headers={"authorization": "***", "host": "mpm.example.com"})
    )
    assert "\n" not in line
    assert "ip=203.0.113.10" in line
    assert 'headers="authorization=***; host=mpm.example.com"' in line
    assert "route_name=phone" in line
