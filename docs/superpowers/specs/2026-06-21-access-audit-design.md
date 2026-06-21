# Access Audit Log Design

Date: 2026-06-21

## Goal

Record every subscription route access with the real client IP, request headers, route/path, response metadata, and access time. Persist events in SQLite with a 30-day default retention window, write a separate human-readable access log, and expose aggregated access statistics on the existing status page and status API.

The feature is for deployments behind Docker, reverse proxies, Cloudflare, and similar networks where `request.client.host` is not enough to identify the real caller.

## Non-Goals

- Do not record health or status endpoint access.
- Do not implement user authentication for the status page.
- Do not build a general analytics dashboard.
- Do not store request or response bodies.
- Do not store raw sensitive header values.
- Do not change route access-control decisions.

## User-Facing Config

Add top-level access audit config:

```toml
[access_log]
enabled = true
db_path = "data/access/access.sqlite3"
retention = "30d"

[access_log.file]
enabled = true
path = "logs/access.log"
rotation = "10 MB"
retention = "30 days"
compression = "gz"
```

Defaults:

- `access_log.enabled = true`
- `access_log.db_path = "data/access/access.sqlite3"`
- `access_log.retention = "30d"`
- `access_log.file.enabled = true`
- `access_log.file.path = "logs/access.log"`
- `access_log.file.rotation = "10 MB"`
- `access_log.file.retention = "30 days"`
- `access_log.file.compression = "gz"`

Validation:

- `retention` uses existing duration parsing and must be positive.
- `db_path` parent directory must be creatable and writable.
- Access log file parent directory must be creatable and writable when file logging is enabled.
- This config is independent from `[logging.file]`; disabling normal file logs must not disable access logs.

## Dependencies

Add SQLAlchemy 2.x as a runtime dependency:

```toml
sqlalchemy>=2.0
```

Use SQLAlchemy Core, not ORM, for a small explicit schema and simple insert/select/delete queries. SQLite is the only supported backend in this feature.

## Data Model

Create module:

```text
src/mihomo_proxy_manager/access_audit.py
```

SQLite table:

```text
access_events
```

Columns:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | autoincrement |
| `visited_at` | datetime/text | UTC ISO timestamp |
| `route_name` | text nullable | matched route name |
| `path` | text not null | requested path, including companion suffix but not query string |
| `companion` | text nullable | `nodes`, `import`, or null |
| `method` | text not null | HTTP method |
| `status_code` | integer not null | response status |
| `real_ip` | text nullable | resolved client IP |
| `ip_source` | text not null | which source supplied `real_ip` |
| `user_agent` | text nullable | sanitized `User-Agent` |
| `headers_json` | text not null | JSON object of sanitized request headers |
| `target_format` | text nullable | effective output format, if route rendered |
| `response_bytes` | integer not null | response body byte length, or 0 if unknown |
| `duration_ms` | integer not null | request duration |

Indexes:

- `idx_access_events_visited_at`
- `idx_access_events_route_name`
- `idx_access_events_path`
- `idx_access_events_real_ip`
- `idx_access_events_user_agent`

## Real IP Resolution

Resolve real IP in this order:

1. `CF-Connecting-IP`
2. `True-Client-IP`
3. first public-looking entry in `X-Forwarded-For`
4. `X-Real-IP`
5. `request.client.host`

Rules:

- Header names are case-insensitive.
- `X-Forwarded-For` may contain comma-separated values; trim whitespace and pick the first syntactically valid IP string.
- IPv4 and IPv6 are accepted.
- If a selected header is present but invalid, skip it and continue to the next source.
- `ip_source` is one of `cf-connecting-ip`, `true-client-ip`, `x-forwarded-for`, `x-real-ip`, `client-host`, or `unknown`.

This feature records what the proxy chain reports. It does not validate trusted proxy ranges yet. A future hardening step may add a trusted proxy allowlist.

## Header Recording

Store all request headers after sanitization.

Sensitive header names are redacted case-insensitively:

```text
authorization
proxy-authorization
cookie
set-cookie
x-api-key
x-auth-token
x-access-token
x-real-token
cf-access-client-secret
```

Redacted values are stored as:

```text
***
```

Other header values are stored exactly as Starlette exposes them, except secrets already known to the application may also pass through existing `redact_secret()` where practical.

Header statistics should be computed only for useful headers by default:

- `user-agent`
- `host`
- `referer`
- headers beginning with `cf-`
- headers beginning with `x-forwarded-`
- `x-real-ip`
- `true-client-ip`

This avoids the status page being dominated by low-value transport headers.

## Access Event Lifecycle

Only matched subscription route paths create access events:

- main route path
- companion route path such as `-nodes`
- companion route path such as `-import`

Excluded:

- `health_path`
- `status_path`
- `{status_path}/api`
- unknown paths that return 404 before matching a route

For a matched route:

1. Capture start time before route access check.
2. Resolve real IP and sanitize headers.
3. Execute existing request flow.
4. Capture response status, effective output format if known, response body byte length, and duration.
5. Write the event to SQLite if `access_log.enabled = true`.
6. Write a human-readable access log line if `access_log.file.enabled = true`.

Access logging must not change the client response. If writing the event or access log fails, log a normal warning with redaction and continue returning the original response.

## Access Log File

The access log is separate from the normal app log.

It uses its own Loguru sink and writes human-readable one-line entries:

```text
2026-06-21T10:22:33Z ip=203.0.113.10 ip_source=cf-connecting-ip method=GET path=/p/token route=main companion=- target=surfboard status=200 bytes=1234 duration_ms=18 ua="Surfboard/2.24" headers="host=mpm.example.com; cf-ray=..."
```

Rules:

- One request per line.
- No stack traces.
- No multiline header dumps.
- Sensitive header values are `***`.
- Access log file rotation/retention/compression uses `[access_log.file]`, not `[logging.file]`.

Normal app log may keep summary lines, but detailed access data belongs to the access log and SQLite.

## Retention

Default retention is 30 days.

Cleanup behavior:

- Run once at startup after opening the SQLite store.
- Run opportunistically after writes, but throttle cleanup to at most once per hour.
- Delete rows where `visited_at < now - retention`.
- Cleanup failures log a warning and do not fail route responses.

## Status API

Extend `build_status()` output with:

```json
{
  "access": {
    "enabled": true,
    "retention_seconds": 2592000,
    "total_events": 1234,
    "since": "2026-05-22T00:00:00Z",
    "top_ips": [
      {"real_ip": "203.0.113.10", "count": 42, "last_seen": "..."}
    ],
    "top_user_agents": [
      {"user_agent": "Surfboard/2.24", "count": 20, "last_seen": "..."}
    ],
    "top_headers": [
      {"header": "cf-ipcountry", "value": "US", "count": 12, "last_seen": "..."}
    ],
    "top_paths": [
      {"path": "/p/token", "route": "main", "count": 99, "last_seen": "..."}
    ],
    "recent": [
      {
        "visited_at": "...",
        "real_ip": "203.0.113.10",
        "ip_source": "cf-connecting-ip",
        "path": "/p/token",
        "route": "main",
        "companion": null,
        "target_format": "surfboard",
        "status_code": 200,
        "duration_ms": 18,
        "user_agent": "Surfboard/2.24"
      }
    ]
  }
}
```

When disabled:

```json
{
  "access": {
    "enabled": false
  }
}
```

Default limits:

- `top_ips`: 20
- `top_user_agents`: 20
- `top_headers`: 30
- `top_paths`: 20
- `recent`: 50

## Status HTML

Add a compact access section to the existing status page:

- Access logging enabled/disabled.
- Total events in retention window.
- Top IPs table.
- Top User-Agents table.
- Top headers table.
- Top paths table.
- Recent requests table.

The section must not show full `headers_json` for every event by default. It may show selected header aggregates and recent row summaries.

## App Integration

`create_app()` receives an optional access audit store, or builds one during CLI runtime before app creation.

Recommended shape:

```python
class AccessAuditStore(Protocol):
    def record(self, event: AccessEvent) -> None: ...
    def cleanup(self, now: datetime | None = None) -> None: ...
    def stats(self, now: datetime | None = None) -> AccessStats: ...
```

The route handler should create an `AccessEvent` after response rendering so it can include:

- status code
- response body size
- effective target format
- duration

Because route handlers are async and SQLite writes are synchronous, keep writes short and local. If a write becomes slow in practice, a future change can move event writes to a background queue.

## CLI Integration

`mpm serve`:

- configure normal logging
- configure access log sink
- initialize SQLite schema if access audit is enabled
- run retention cleanup
- pass the access audit store into `create_app()`

`mpm check`:

- validate access log config and writable directories
- do not create database files unless existing config checks already create directories

## Security And Privacy

- Redact sensitive header values before SQLite and access file logging.
- Do not record request bodies.
- Do not record response bodies.
- Do not log raw source subscription URLs.
- Do not expose status access stats unless `status_path` is configured; existing high-entropy status path guidance still applies.
- The feature records personal data such as IP addresses and User-Agents. Docs must mention retention and how to disable it.

## Testing Strategy

Add tests for:

- Config defaults and parsing for `[access_log]` and `[access_log.file]`.
- Invalid retention is rejected.
- Invalid or unwritable paths are rejected.
- SQLAlchemy store creates schema.
- Store records an event and returns top IP, User-Agent, headers, paths, and recent rows.
- Retention cleanup deletes old rows and keeps recent rows.
- Real IP resolution priority:
  - `CF-Connecting-IP`
  - `True-Client-IP`
  - first `X-Forwarded-For`
  - `X-Real-IP`
  - client host fallback
- Invalid IP header falls through to the next source.
- Header sanitizer redacts sensitive headers case-insensitively.
- Matched subscription route writes SQLite event.
- Health/status/unknown route do not write access event.
- Access logging failure does not change response.
- Access log file receives a human-readable line separate from normal app log.
- Status API includes `access` stats when enabled.
- Status API returns `{"enabled": false}` when disabled.
- Status HTML includes access statistics section.
- Existing tests pass when access logging is disabled.

## Documentation Updates

Update:

- `README.md`
- `README_EN.md`
- `examples/config.toml`

Docs should explain:

- SQLite access audit log.
- Human-readable access log file.
- Real IP header priority.
- Header redaction.
- Default 30-day retention.
- How to disable access logging:

```toml
[access_log]
enabled = false
```

## Open Decisions

All current decisions are fixed for this design:

- Storage: SQLite through SQLAlchemy Core.
- Retention default: 30 days.
- Header storage: all request headers, with sensitive values redacted.
- Aggregation unit: route/path, not TCP port.
- Access log file: human-readable, separate from normal app log.
