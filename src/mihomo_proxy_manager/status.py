"""构建状态响应，包含订阅源、路由统计和协议分布。

Build status responses with per-source refresh state, per-route stats, and
protocol distributions.
"""

from __future__ import annotations

import asyncio
import html
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from loguru import logger

from .access_audit import AccessStats
from .cache import SourceCacheStore
from .models import AppConfig, ProxyRecord, SourceCache
from .security import redact_secret
from .transform import apply_transform


class AccessStatsStore(Protocol):
    def stats(self, now_ms: int | None = None) -> AccessStats: ...


def _is_still_valid(cache: SourceCache | None, max_stale: timedelta) -> bool:
    """检查缓存是否仍在最大过期时间内有效。

    Check whether the cache is still valid within the maximum staleness duration.
    """
    if cache is None or cache.last_success_at is None:
        return False
    return datetime.now(UTC) - cache.last_success_at <= max_stale


def _protocol_counts(records: Iterable[ProxyRecord]) -> dict[str, int]:
    """统计代理记录中的协议分布。

    Count proxies by their ``type`` field.
    """
    counts: Counter[str] = Counter()
    for record in records:
        proxy_type = str(record.data.get("type", "unknown")).lower()
        counts[proxy_type or "unknown"] += 1
    return dict(counts)


def _sort_counts(counts: dict[str, int]) -> dict[str, int]:
    """按数量降序、名称升序返回协议分布字典。"""
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _select_fields(
    items: Iterable[dict[str, Any]], fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        {field: item.get(field) for field in fields if field in item} for item in items
    ]


async def _access_status(
    config: AppConfig, access_audit_store: AccessStatsStore | None
) -> dict[str, Any]:
    if not config.access_log.enabled:
        return {"enabled": False}
    if not config.access_log.status.enabled or access_audit_store is None:
        return {"enabled": True, "stats_enabled": False}
    try:
        stats = await asyncio.to_thread(access_audit_store.stats)
    except Exception as exc:
        logger.warning("access audit stats failed: {error}", error=exc)
        return {"enabled": True, "stats_enabled": False}

    data: dict[str, Any] = {
        "enabled": stats.enabled,
        "stats_enabled": stats.stats_enabled,
        "retention_seconds": stats.retention_seconds,
        "privacy": stats.privacy,
        "total_events": stats.total_events,
        "since": stats.since,
        "top_ips": _select_fields(
            stats.top_ips or (), ("real_ip", "count", "last_seen")
        ),
        "top_user_agents": _select_fields(
            stats.top_user_agents or (), ("user_agent", "count", "last_seen")
        ),
        "top_headers": _select_fields(
            stats.top_headers or (), ("header", "value", "count", "last_seen")
        ),
        "top_paths": _select_fields(
            stats.top_paths or (), ("path", "route_name", "count", "last_seen")
        ),
    }
    if stats.recent is not None:
        data["recent"] = _select_fields(
            stats.recent,
            (
                "visited_at",
                "route_name",
                "path",
                "companion",
                "method",
                "status_code",
                "real_ip",
                "ip_source",
                "target_format",
                "response_bytes",
                "duration_ms",
            ),
        )
    return data


async def build_status(
    cache_store: SourceCacheStore,
    config: AppConfig,
    *,
    extra_secrets: list[str] | None = None,
    access_audit_store: AccessStatsStore | None = None,
) -> dict[str, Any]:
    """构建完整的状态字典。

    Build a full status dictionary including sources, routes, and protocols.

    Args:
        cache_store: 缓存存储实例 / Cache store instance.
        config: 应用配置 / Application configuration.
        extra_secrets: 额外的敏感字符串列表，用于脱敏错误信息 /
            Additional sensitive strings for error redaction.

    Returns:
        包含 sources、routes、summary 的状态字典 / Status dict.
    """
    source_names = list(config.sources)
    caches: dict[str, SourceCache | None] = {}
    for name in source_names:
        caches[name] = await cache_store.get(name)

    sources: list[dict[str, Any]] = []
    for name in source_names:
        cache = caches[name]
        status = await cache_store.status(name)
        if cache is None:
            sources.append(
                {
                    "source": name,
                    "last_attempt_at": None,
                    "last_success_at": None,
                    "node_count": 0,
                    "protocols": {},
                    "last_error": redact_secret(
                        status.last_error, extra_secrets=extra_secrets
                    )
                    if status.last_error
                    else None,
                    "refreshing": status.refreshing,
                    "healthy": False,
                }
            )
            continue
        sources.append(
            {
                "source": status.source,
                "last_attempt_at": status.last_attempt_at.isoformat()
                if status.last_attempt_at
                else None,
                "last_success_at": status.last_success_at.isoformat()
                if status.last_success_at
                else None,
                "node_count": status.node_count,
                "protocols": _sort_counts(_protocol_counts(cache.proxies)),
                "last_error": redact_secret(
                    status.last_error, extra_secrets=extra_secrets
                )
                if status.last_error
                else None,
                "refreshing": status.refreshing,
                "healthy": _is_still_valid(cache, config.cache.max_stale),
            }
        )

    routes: list[dict[str, Any]] = []
    overall_protocols: Counter[str] = Counter()
    for route in config.routes.values():
        route_records: list[ProxyRecord] = []
        available_sources = 0
        for source_name in route.sources:
            cache = caches.get(source_name)
            if cache is not None:
                route_records.extend(cache.proxies)
                if _is_still_valid(cache, config.cache.max_stale):
                    available_sources += 1
        transformed = apply_transform(
            route_records,
            filter_config=route.filter,
            rename_config=route.rename,
        )
        protocols = _sort_counts(_protocol_counts(transformed))
        overall_protocols.update(protocols)
        routes.append(
            {
                "name": route.name,
                "sources": list(route.sources),
                "available_sources": available_sources,
                "total_sources": len(route.sources),
                "node_count": len(transformed),
                "protocols": protocols,
            }
        )

    total_route_nodes = sum(route["node_count"] for route in routes)
    summary = {
        "sources": {
            "total": len(sources),
            "healthy": sum(1 for source in sources if source.get("healthy")),
            "refreshing": sum(1 for source in sources if source.get("refreshing")),
        },
        "routes": {
            "total": len(routes),
            "nodes": total_route_nodes,
        },
        "protocols": _sort_counts(dict(overall_protocols)),
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "sources": sources,
        "routes": routes,
        "access": await _access_status(config, access_audit_store),
    }


def _render_html(data: dict[str, Any]) -> str:
    """将状态字典渲染为美观的 HTML 页面。"""
    generated = html.escape(str(data.get("generated_at", "")))
    summary = data.get("summary", {})
    source_summary = summary.get("sources", {})
    route_summary = summary.get("routes", {})
    overall_protocols = summary.get("protocols", {})

    def _pills(counts: dict[str, int]) -> str:
        if not counts:
            return '<span class="muted">-</span>'
        return "".join(
            f'<span class="pill">{html.escape(name)} <span class="pill-count">{count}</span></span>'
            for name, count in counts.items()
        )

    def _source_state(source: dict[str, Any]) -> str:
        if source.get("healthy"):
            label = "healthy"
            if source.get("refreshing"):
                label += " · refreshing"
            return f'<span class="badge badge-ok">{label}</span>'
        if source.get("refreshing"):
            return '<span class="badge badge-warn">refreshing</span>'
        return '<span class="badge badge-err">unhealthy</span>'

    def _source_cards() -> str:
        cards: list[str] = []
        for source in data.get("sources", []):
            name = html.escape(source["source"])
            node_count = source.get("node_count", 0)
            protocols = _pills(source.get("protocols", {}))
            last_success = html.escape(str(source.get("last_success_at") or "-"))
            last_error = source.get("last_error")
            error_block = ""
            if last_error:
                error_text = html.escape(str(last_error))
                error_block = f'<div class="detail error"><span class="detail-label">Error</span>{error_text}</div>'
            cards.append(
                f"""
                <div class="card">
                  <div class="card-header">
                    <div class="card-title">{name}</div>
                    {_source_state(source)}
                  </div>
                  <div class="metric">
                    <div class="metric-value">{node_count}</div>
                    <div class="metric-label">nodes</div>
                  </div>
                  <div class="detail"><span class="detail-label">Protocols</span>{protocols}</div>
                  <div class="detail"><span class="detail-label">Last Success</span>{last_success}</div>
                  {error_block}
                </div>
                """
            )
        return (
            "\n".join(cards) if cards else '<p class="muted">No sources configured.</p>'
        )

    def _route_cards() -> str:
        cards: list[str] = []
        for route in data.get("routes", []):
            name = html.escape(route["name"])
            node_count = route.get("node_count", 0)
            available = route.get("available_sources", 0)
            total = route.get("total_sources", 0)
            protocols = _pills(route.get("protocols", {}))
            availability_class = (
                "bar-fill-ok" if available == total else "bar-fill-warn"
            )
            pct = (available / total * 100) if total else 0
            cards.append(
                f"""
                <div class="card">
                  <div class="card-header">
                    <div class="card-title">{name}</div>
                    <span class="badge badge-neutral">{available}/{total} sources</span>
                  </div>
                  <div class="metric">
                    <div class="metric-value">{node_count}</div>
                    <div class="metric-label">nodes</div>
                  </div>
                  <div class="detail"><span class="detail-label">Protocols</span>{protocols}</div>
                  <div class="detail"><span class="detail-label">Availability</span>
                    <div class="bar-bg">
                      <div class="bar-fill {availability_class}" style="width: {pct:.1f}%;"></div>
                    </div>
                  </div>
                </div>
                """
            )
        return (
            "\n".join(cards) if cards else '<p class="muted">No routes configured.</p>'
        )

    def _summary_cards() -> str:
        items = [
            ("Sources", source_summary.get("total", 0), "#818cf8"),
            ("Healthy", source_summary.get("healthy", 0), "#34d399"),
            ("Refreshing", source_summary.get("refreshing", 0), "#fbbf24"),
            ("Routes", route_summary.get("total", 0), "#818cf8"),
            ("Route Nodes", route_summary.get("nodes", 0), "#38bdf8"),
        ]
        return "".join(
            f"""
            <div class="summary-card">
              <div class="summary-value" style="color: {color};">{value}</div>
              <div class="summary-label">{html.escape(label)}</div>
            </div>
            """
            for label, value, color in items
        )

    def _access_section() -> str:
        access = data.get("access", {})
        if not access.get("enabled"):
            return '<section><h2>Access</h2><p class="muted">disabled</p></section>'
        if not access.get("stats_enabled"):
            return (
                '<section><h2>Access</h2><p class="muted">stats disabled</p></section>'
            )

        def _cell(value: object) -> str:
            return html.escape(str(value if value is not None else "-"))

        def _table(items: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
            headers = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
            if not items:
                body = f'<tr><td colspan="{len(columns)}" class="muted">-</td></tr>'
            else:
                body = "".join(
                    "<tr>"
                    + "".join(
                        f"<td>{_cell(item.get(column))}</td>" for column in columns
                    )
                    + "</tr>"
                    for item in items
                )
            return (
                f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"
            )

        total = _cell(access.get("total_events", 0))
        since = _cell(access.get("since", "-"))
        retention = _cell(access.get("retention_seconds", "-"))
        recent = ""
        if access.get("recent") is not None:
            recent = f"""
              <h3>Recent</h3>
              {
                _table(
                    access.get("recent", []),
                    ("visited_at", "path", "route_name", "status_code", "real_ip"),
                )
            }
            """

        return f"""
        <section>
          <h2>Access</h2>
          <div class="access-summary">
            <div class="metric">
              <div class="metric-value">{total}</div>
              <div class="metric-label">events</div>
            </div>
            <div class="detail"><span class="detail-label">Since</span>{since}</div>
            <div class="detail"><span class="detail-label">Retention Seconds</span>{retention}</div>
          </div>
          <h3>Top IPs</h3>
          {_table(access.get("top_ips", []), ("real_ip", "count", "last_seen"))}
          <h3>User-Agents</h3>
          {_table(access.get("top_user_agents", []), ("user_agent", "count", "last_seen"))}
          <h3>Headers</h3>
          {_table(access.get("top_headers", []), ("header", "value", "count", "last_seen"))}
          <h3>Paths</h3>
          {_table(access.get("top_paths", []), ("path", "route_name", "count", "last_seen"))}
          {recent}
        </section>
        """

    overall_pills = _pills(overall_protocols)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Proxy Manager Status</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --surface: #111827;
      --surface-elevated: #1f2937;
      --border: #374151;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #6366f1;
      --accent-soft: rgba(99, 102, 241, 0.15);
      --ok: #10b981;
      --ok-soft: rgba(16, 185, 129, 0.15);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.15);
      --err: #ef4444;
      --err-soft: rgba(239, 68, 68, 0.15);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 2rem 1.25rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: radial-gradient(ellipse at top, #111827 0%, var(--bg) 60%);
      background-attachment: fixed;
      color: var(--text);
      line-height: 1.6;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 2rem;
      flex-wrap: wrap;
    }}
    h1 {{ font-size: 1.75rem; margin: 0; letter-spacing: -0.025em; }}
    h1 span {{ color: var(--accent); }}
    .subtitle {{ color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem; }}
    .api-link {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.5rem 0.875rem;
      border-radius: 0.5rem;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      text-decoration: none;
      font-size: 0.875rem;
      transition: background 0.15s, border-color 0.15s;
    }}
    .api-link:hover {{ background: var(--surface-elevated); border-color: var(--accent); }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .summary-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1.25rem;
      text-align: center;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }}
    .summary-value {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
    .summary-label {{ color: var(--muted); font-size: 0.875rem; margin-top: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .overall {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1rem 1.25rem;
      margin-bottom: 2.5rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem;
    }}
    .overall-label {{ color: var(--muted); font-size: 0.875rem; }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{
      font-size: 1.25rem;
      margin: 0 0 1rem 0;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1rem;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1.25rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
      transition: transform 0.15s, border-color 0.15s;
    }}
    .card:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
    .card-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }}
    .card-title {{ font-weight: 600; font-size: 1rem; word-break: break-all; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      white-space: nowrap;
    }}
    .badge-ok {{ background: var(--ok-soft); color: var(--ok); border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge-warn {{ background: var(--warn-soft); color: var(--warn); border: 1px solid rgba(245, 158, 11, 0.3); }}
    .badge-err {{ background: var(--err-soft); color: var(--err); border: 1px solid rgba(239, 68, 68, 0.3); }}
    .badge-neutral {{ background: var(--surface-elevated); color: var(--muted); border: 1px solid var(--border); }}
    .metric {{ margin-bottom: 1rem; }}
    .metric-value {{ font-size: 2.5rem; font-weight: 700; line-height: 1; }}
    .metric-label {{ color: var(--muted); font-size: 0.875rem; }}
    .detail {{ margin-top: 0.75rem; font-size: 0.875rem; }}
    .detail-label {{ display: block; color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.35rem; }}
    .detail.error {{ color: var(--err); }}
    h3 {{ margin: 1rem 0 0.5rem; font-size: 1rem; }}
    .access-summary {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      gap: 1rem 2rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 0.75rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      overflow: hidden;
      font-size: 0.875rem;
    }}
    th, td {{
      padding: 0.55rem 0.7rem;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      word-break: break-all;
    }}
    th {{ color: var(--muted); background: var(--surface-elevated); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      background: var(--accent-soft);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.25);
      border-radius: 999px;
      padding: 0.25rem 0.65rem;
      font-size: 0.8rem;
      margin: 0.15rem 0.15rem 0 0;
    }}
    .pill-count {{ color: #c7d2fe; font-weight: 600; }}
    .bar-bg {{
      height: 0.5rem;
      background: var(--surface-elevated);
      border-radius: 999px;
      overflow: hidden;
      margin-top: 0.35rem;
    }}
    .bar-fill {{ height: 100%; border-radius: 999px; transition: width 0.3s ease; }}
    .bar-fill-ok {{ background: var(--ok); }}
    .bar-fill-warn {{ background: var(--warn); }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 640px) {{
      body {{ padding: 1rem; }}
      .grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 1.4rem; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Proxy Manager <span>Status</span></h1>
        <div class="subtitle">Generated at {generated}</div>
      </div>
      <a class="api-link" href="./api">JSON API ↗</a>
    </header>

    <div class="summary-grid">
      {_summary_cards()}
    </div>

    <div class="overall">
      <span class="overall-label">Overall protocols:</span>
      {overall_pills}
    </div>

    {_access_section()}

    <section>
      <h2>Sources</h2>
      <div class="grid">
        {_source_cards()}
      </div>
    </section>

    <section>
      <h2>Routes</h2>
      <div class="grid">
        {_route_cards()}
      </div>
    </section>
  </div>
</body>
</html>"""


def render_status_html(data: dict[str, Any]) -> str:
    """渲染美观的状态 HTML 页面。

    Render the status data as a polished HTML dashboard.

    Args:
        data: build_status 返回的字典 / Status dict from build_status.

    Returns:
        HTML 字符串 / HTML string.
    """
    return _render_html(data)
