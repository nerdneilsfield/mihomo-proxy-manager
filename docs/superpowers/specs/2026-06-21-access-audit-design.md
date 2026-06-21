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
trusted_proxies = [
  "127.0.0.1/32",
  "::1/128",
  "10.0.0.0/8",
  "172.16.0.0/12",
  "192.168.0.0/16",
]
real_ip_headers = [
  "cf-connecting-ip",
  "true-client-ip",
  "x-forwarded-for",
  "x-real-ip",
]

[access_log.file]
enabled = true
path = "logs/access.log"
rotation = "10 MB"
retention = "30 days"
compression = "gz"

[access_log.headers]
max_value_length = 512
stats_allowlist = [
  "user-agent",
  "host",
  "cf-ipcountry",
  "cf-ray",
]
stats_max_rows = 5000

[access_log.status]
enabled = true
mask_ips = true
include_recent = false
recent_limit = 20
top_limit = 20
```

Defaults:

- `access_log.enabled = true`
- `access_log.db_path = "data/access/access.sqlite3"`
- `access_log.retention = "30d"`
- `access_log.trusted_proxies = ["127.0.0.1/32", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]`
- `access_log.real_ip_headers = ["cf-connecting-ip", "true-client-ip", "x-forwarded-for", "x-real-ip"]`
- `access_log.file.enabled = true`
- `access_log.file.path = "logs/access.log"`
- `access_log.file.rotation = "10 MB"`
- `access_log.file.retention = "30 days"`
- `access_log.file.compression = "gz"`
- `access_log.headers.max_value_length = 512`
- `access_log.headers.stats_allowlist = ["user-agent", "host", "cf-ipcountry", "cf-ray"]`
- `access_log.headers.stats_max_rows = 5000`
- `access_log.status.enabled = true`
- `access_log.status.mask_ips = true`
- `access_log.status.include_recent = false`
- `access_log.status.recent_limit = 20`
- `access_log.status.top_limit = 20`

Validation:

- `retention` uses existing duration parsing and must be positive.
- `db_path` parent directory must be creatable and writable.
- Access log file parent directory must be creatable and writable when file logging is enabled.
- This config is independent from `[logging.file]`; disabling normal file logs must not disable access logs.
- Unknown top-level keys under `[access_log]`, `[access_log.file]`, `[access_log.headers]`, or `[access_log.status]` are rejected by the same top-level whitelist strategy used for other config sections.
- Config model adds dataclasses under `AppConfig.access_log`, including nested `file`, `headers`, and `status` config objects.
- `trusted_proxies` entries must parse as IP networks or exact IP addresses; exact IPs are normalized to single-host networks.
- `real_ip_headers` values must be known supported headers: `cf-connecting-ip`, `true-client-ip`, `x-forwarded-for`, `x-real-ip`.
- Header value length and status limits must be positive integers. `headers.stats_max_rows` bounds Python aggregation work for header statistics.

## Dependencies

Add SQLAlchemy 2.x as a runtime dependency:

```toml
sqlalchemy>=2.0
```

Use SQLAlchemy Core, not ORM, for a small explicit schema and simple insert/select/delete queries. SQLite is the only supported backend in this feature.

Dependency files must stay in sync:

- Add `sqlalchemy>=2.0` to `pyproject.toml`.
- Regenerate and commit `uv.lock`.
- Regenerate and commit `requirements.txt` because CI and README installation use `pip install -r requirements.txt` followed by `pip install -e . --no-deps`.
- Verify CI-style install path still has SQLAlchemy available.

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
| `visited_at` | integer | UTC epoch milliseconds |
| `route_name` | text nullable | matched route name |
| `path` | text not null | requested path, including companion suffix but not query string |
| `companion` | text nullable | `nodes`, `import`, or null |
| `method` | text not null | HTTP method |
| `status_code` | integer not null | response status |
| `real_ip` | text nullable | resolved client IP |
| `ip_source` | text not null | which source supplied `real_ip` |
| `user_agent` | text nullable | sanitized `User-Agent` |
| `headers_json` | text not null | JSON object of redacted and truncated request headers |
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

Only trust proxy-supplied real IP headers when the direct peer `request.client.host` is in `access_log.trusted_proxies`. This default trusts loopback and RFC1918 Docker/LAN proxy ranges because common Docker and reverse proxy deployments need zero extra setup. It is conservative about the public Internet but not perfect for hostile same-LAN clients; deployments that expose the app directly on private networks should set `trusted_proxies` to the exact reverse proxy IPs.

If the direct peer is trusted, resolve real IP using `access_log.real_ip_headers` order. Default order:

1. `CF-Connecting-IP`
2. `True-Client-IP`
3. first public-looking entry in `X-Forwarded-For`
4. `X-Real-IP`
5. `request.client.host`

Rules:

- Header names are case-insensitive.
- If the direct peer is not trusted, ignore all proxy headers and use `request.client.host` with `ip_source = "client-host"`.
- `X-Forwarded-For` is accepted only when the trusted reverse proxy is configured to overwrite or sanitize this header before forwarding to the app. The application cannot prove that from the HTTP request, so docs and examples must warn operators not to pass through client-supplied XFF chains.
- For accepted `X-Forwarded-For`, trim comma-separated values and pick the leftmost syntactically valid global IP address after the trusted proxy has sanitized/overwritten the header.
- Private, loopback, link-local, multicast, unspecified, documentation, and otherwise non-global/reserved `X-Forwarded-For` entries are skipped. If every `X-Forwarded-For` entry is non-global or invalid, continue to the next configured source.
- Single-IP headers (`CF-Connecting-IP`, `True-Client-IP`, `X-Real-IP`) must parse as IP addresses. They may be private when the trusted proxy supplies them because private clients behind LAN/VPN are legitimate; invalid values are skipped.
- IPv4 and IPv6 are accepted.
- If a selected header is present but invalid, skip it and continue to the next source.
- `ip_source` is one of `cf-connecting-ip`, `true-client-ip`, `x-forwarded-for`, `x-real-ip`, `client-host`, or `unknown`.
- If `request.client.host` is missing or invalid and no trusted header gives an IP, set `real_ip = null` and `ip_source = "unknown"`.

## Header Recording

Store all request headers only after a redaction pipeline:

1. Normalize header names to lowercase for storage and matching.
2. Redact values for sensitive header names.
3. Apply the existing application secret redaction function to every header value, even when the header name is not sensitive.
4. Truncate remaining values longer than `access_log.headers.max_value_length`.
5. Store the resulting object in `headers_json`.

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

Other header values are not stored exactly as received; they always pass through existing secret redaction and truncation first. Truncated values end with `...` after redaction, never before redaction.

Status page and status API must not expose full `headers_json` by default. Full header JSON may remain available only inside SQLite for operators with filesystem access.

Header statistics are computed only for `access_log.headers.stats_allowlist`. Displayed values are masked/truncated with the same max length, and the HTML should use a shorter visual cap if needed.

IP-bearing headers such as `x-forwarded-for`, `x-real-ip`, `true-client-ip`, and `cf-connecting-ip` are excluded from the default stats allowlist because `top_ips` already exposes masked IP aggregates. If an operator explicitly adds an IP-bearing header to `stats_allowlist`, status API and HTML must mask IP values with the same IPv4 `/24` and IPv6 `/64` rules used by `top_ips`; XFF chains must be parsed and each IP-like value masked before display.

Default allowlist excludes `referer` because full URLs often carry tokens or query parameters. If operators add `referer`, aggregation stores and displays origin only (`scheme://host[:port]`), never path or query.

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
3. Execute existing request flow through a shared wrapper/finalizer.
4. Record every matched-route outcome, including early `403`, malformed request `400`, validation `422`, upstream/render `503`, and successful render responses.
5. Capture response status, effective output format if known, response body byte length, and duration after the response object is available.
6. Write the event to SQLite if `access_log.enabled = true`.
7. Write a human-readable access log line if `access_log.file.enabled = true`.

Implementation requirement:

- Build the event in a `try`/`finally` style shared finalizer or route wrapper so early returns and exceptions converted to HTTP responses are recorded.
- Capture start time before any route access check or route-specific validation.
- If an exception escapes and Starlette converts it outside the wrapper, record only if the wrapper can observe the final response; do not invent status codes.
- Exclusions remain strict: health, status, status API, and unknown unmatched paths produce no access events.

Access logging must not change the client response. If writing the event or access log fails, log a normal warning with redaction and continue returning the original response.

## Access Log File

The access log is separate from the normal app log.

It uses its own Loguru sink and writes human-readable one-line entries:

```text
2026-06-21T10:22:33Z ip=203.0.113.10 ip_source=cf-connecting-ip method=GET path=/p/token route_name=main companion=null target=surfboard status=200 bytes=1234 duration_ms=18 ua="Surfboard/2.24" headers="host=mpm.example.com; cf-ray=..."
```

Rules:

- One request per line.
- No stack traces.
- No multiline header dumps.
- Sensitive header values are `***`.
- Access log file rotation/retention/compression uses `[access_log.file]`, not `[logging.file]`.
- Use `logger.bind(access_log=True)` for access log records.
- `configure_logging()` removes all existing Loguru sinks first, then adds normal sinks and access sink in deterministic order.
- Normal sinks filter out records where `record["extra"].get("access_log") is True`.
- Access sink filter includes only records where `record["extra"].get("access_log") is True`.

Normal app log may keep summary lines, but detailed access data belongs to the access log and SQLite.

## Retention

Default DB retention is 30 days. Access log file retention is separate and uses `[access_log.file].retention`.

If `access_log.enabled = false`, do not initialize SQLite, do not create the DB file, do not create access log sinks, and do not write file access logs even when `[access_log.file].enabled = true`.

Cleanup behavior:

- Run once at startup after opening the SQLite store.
- Run opportunistically after writes, but throttle cleanup to at most once per hour.
- Delete rows where `visited_at < cutoff_epoch_ms`.
- Cleanup failures log a warning and do not fail route responses.
- DB insert/stat/cleanup failures, including disk-full and locked-database errors, log a normal warning and must not alter the HTTP response.
- Consider occasional WAL checkpoint or SQLite `VACUUM` documentation for operators, but do not run expensive vacuum work in request path.

## Status API

Extend `build_status()` output with:

```json
{
  "access": {
    "enabled": true,
    "stats_enabled": true,
    "retention_seconds": 2592000,
    "privacy": {
      "mask_ips": true,
      "include_recent": false
    },
    "total_events": 1234,
    "since": 1789950153000,
    "top_ips": [
      {"real_ip": "203.0.113.0/24", "count": 42, "last_seen": 1792542153000}
    ],
    "top_user_agents": [
      {"user_agent": "Surfboard/2.24", "count": 20, "last_seen": 1792542153000}
    ],
    "top_headers": [
      {"header": "cf-ipcountry", "value": "US", "count": 12, "last_seen": 1792542153000}
    ],
    "top_paths": [
      {"path": "/p/token", "route_name": "main", "count": 99, "last_seen": 1792542153000}
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

When access audit is enabled but status stats are disabled:

```json
{
  "access": {
    "enabled": true,
    "stats_enabled": false
  }
}
```

Default limits:

- All top lists use `access_log.status.top_limit` by default: 20.
- `recent` is omitted when `access_log.status.include_recent = false`.
- If recent is enabled, cap by `access_log.status.recent_limit` and include only redacted fields.
- JSON timestamps for access stats use UTC epoch milliseconds consistently (`visited_at`, `last_seen`, and `since`).
- Mask IPv4 addresses to `/24` and IPv6 addresses to `/64` when `access_log.status.mask_ips = true`.
- Never include full `headers_json` in status JSON by default.
- Use `route_name` consistently in all status JSON objects. Do not emit a `route` alias.

## Status HTML

Add a compact access section to the existing status page:

- Access logging enabled/disabled.
- Total events in retention window.
- Top IPs table.
- Top User-Agents table.
- Top headers table.
- Top paths table.
- Recent requests table only when `access_log.status.include_recent = true`.

The section must not show full `headers_json` for every event by default. It may show selected header aggregates and recent row summaries only after masking, redaction, and truncation.

## App Integration

`create_app()` receives an optional access audit store. Existing callers and tests keep passing because the default is `None`:

```python
def create_app(
    ...,
    access_audit_store: AccessAuditStore | None = None,
) -> Starlette: ...
```

`build_status()` also receives the store as an optional dependency:

```python
def build_status(
    ...,
    access_audit_store: AccessAuditStore | None = None,
) -> dict[str, Any]: ...
```

When `access_audit_store is None`, route handling skips DB/file access audit writes. If access audit config is disabled, `build_status()` returns `{"access": {"enabled": false}}`; if config is enabled but no store exists, it returns `{"access": {"enabled": true, "stats_enabled": false}}` without querying stats.

Recommended shape:

```python
class AccessAuditStore(Protocol):
    def record(self, event: AccessEvent) -> None: ...
    def cleanup(self, now_ms: int | None = None) -> None: ...
    def stats(self, now_ms: int | None = None) -> AccessStats: ...
    def dispose(self) -> None: ...
```

The route handler should finalize an `AccessEvent` after it has a response object, including early-return responses, so it can include:

- status code
- response body size
- effective target format
- duration

Because route handlers are async and SQLite writes are synchronous, call store `record()`, `stats()`, and `cleanup()` through `asyncio.to_thread()` from the app. Keep writes short and local. If a write becomes slow in practice, a future change can move event writes to a background queue.

## SQLite Store Lifecycle

Use SQLAlchemy Core:

```python
engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"timeout": 5},
)
```

Initialization:

- Create the parent directory during `mpm serve` only when `access_log.enabled = true`.
- Open engine and set SQLite PRAGMAs at init: `journal_mode=WAL` and `busy_timeout=5000`.
- Create schema through SQLAlchemy metadata.
- Run startup cleanup after schema creation.

Runtime:

- Insert, stats, and cleanup methods are synchronous store methods invoked through `asyncio.to_thread()` by async app code.
- Store timestamps as UTC epoch milliseconds. Cleanup compares integer milliseconds, not formatted strings.
- Top header aggregation must not require SQLite JSON1. Implement it in Python over a bounded recent row window: select at most `access_log.headers.stats_max_rows` retained rows ordered by `visited_at DESC`, parse each `headers_json`, aggregate only `stats_allowlist`, and apply display masking/truncation. This is efficient enough for default limits and avoids SQLite extension assumptions.

Shutdown:

- Attach store disposal to Starlette lifespan shutdown.
- Call `engine.dispose()` during shutdown.
- Disposal failures log a warning and do not block shutdown.

## CLI Integration

`mpm serve`:

- configure normal logging
- configure access log sink
- initialize SQLite schema if access audit is enabled
- run retention cleanup
- pass the access audit store into `create_app()`

`mpm check`:

- validate access log config and writable directories
- use config loading through `load_config(validate=True)` and existing `check_filesystem` behavior for directory validation.
- directory creation is acceptable if it is already how config validation works for other paths.
- do not create or open the SQLite database file during check.
- do not create access log files during check.
- if pure validation becomes available in a future change, prefer it, but do not change global filesystem validation semantics only for this feature.

## Security And Privacy

- Redact sensitive header values before SQLite and access file logging.
- Treat all header values as potentially sensitive: apply existing secret redaction to every value and truncate long values before storage.
- Do not record request bodies.
- Do not record response bodies.
- Do not log raw source subscription URLs.
- Do not expose status access stats unless `status_path` is configured; existing high-entropy status path guidance still applies.
- Allow status access stats to be disabled independently with `access_log.status.enabled = false`.
- Mask IPs in status output by default; full IPs remain in SQLite and access log for operators who enabled audit logging.
- Hide recent request rows from status output by default because they are more identifying than aggregate stats.
- The feature records personal data such as IP addresses and User-Agents. Docs must mention retention and how to disable it.

## Testing Strategy

Add tests for:

- Config defaults and parsing for `[access_log]`, `[access_log.file]`, `[access_log.headers]`, and `[access_log.status]`.
- Config whitelist rejects unknown access log keys, and parsed model is available at `AppConfig.access_log`.
- Dependency packaging: `pyproject.toml`, `uv.lock`, and `requirements.txt` all include SQLAlchemy; a CI-style `pip install -r requirements.txt && pip install -e . --no-deps` can import `sqlalchemy`.
- Invalid retention is rejected.
- Invalid or unwritable paths are rejected.
- Invalid `trusted_proxies`, `real_ip_headers`, header limits, and status limits are rejected.
- SQLAlchemy store creates schema.
- Store initializes SQLite with WAL and busy timeout where observable.
- Store disposes engine on lifespan shutdown.
- Store records an event and returns top IP, User-Agent, headers, paths, and recent rows.
- Store records `visited_at` as UTC epoch milliseconds and cleanup compares integer cutoffs.
- Retention cleanup deletes old rows and keeps recent rows.
- DB locked, disk-full, insert failure, stats failure, and cleanup failure log warnings and do not alter route responses.
- `access_log.enabled = false` creates no store, writes no DB events, and writes no access log file lines.
- Real IP resolution priority:
  - `CF-Connecting-IP`
  - `True-Client-IP`
  - first valid global `X-Forwarded-For`
  - `X-Real-IP`
  - client host fallback
- Proxy headers are ignored when `request.client.host` is not in `trusted_proxies`.
- Trusted proxy spoof/fallback cases cover invalid headers, private/reserved `X-Forwarded-For` entries, and missing client host.
- Invalid IP header falls through to the next source.
- Header sanitizer redacts sensitive headers case-insensitively, applies existing secret redaction to all values, and truncates long values.
- Header stats aggregate in Python without requiring SQLite JSON1.
- Header stats honor allowlist and never expose full `referer` URL; referer aggregation uses origin only when explicitly allowlisted.
- Header stats exclude IP-bearing headers by default; if configured, IP-bearing header values are masked before status display.
- Matched subscription route writes SQLite event for all outcomes: `403`, `400`, `422`, `503`, and successful render.
- Health/status/unknown route do not write access event.
- Access logging failure does not change response.
- Access log file receives a human-readable line separate from normal app log.
- Loguru sink isolation: access records go only to access sink, normal records go only to normal sinks, and `configure_logging()` resets sinks deterministically.
- Status API includes `access` stats when enabled and `access_log.status.enabled = true`.
- Status API returns `{"enabled": false}` when disabled.
- Status API returns `{"enabled": true, "stats_enabled": false}` when audit is enabled but status stats are disabled.
- Status API masks IPs by default, hides `recent` by default, caps recent rows when enabled, and never exposes full `headers_json`.
- Status JSON uses `route_name` consistently, not `route`; `companion` is `null` or a string.
- Status HTML includes access statistics section.
- Status HTML follows the same masking, recent-row, and header exposure rules as status JSON.
- Existing tests pass when access logging is disabled.

## Documentation Updates

Update:

- `README.md`
- `README_EN.md`
- `examples/config.toml`

Docs should explain:

- SQLite access audit log.
- Human-readable access log file.
- Real IP trusted proxy behavior, default trusted proxy ranges, and spoofing tradeoff.
- `X-Forwarded-For` trust requirement: upstream proxy must overwrite or sanitize XFF; otherwise use `CF-Connecting-IP`, `True-Client-IP`, `X-Real-IP`, or disable XFF in `real_ip_headers`.
- Header redaction pipeline, value truncation, and status header aggregation allowlist.
- Status access stats privacy controls: masked IPs by default and recent rows hidden by default.
- Default 30-day retention.
- `mpm check` validates config and may create directories via existing filesystem checks, but does not create DB or log files.
- How to disable access logging:

```toml
[access_log]
enabled = false
```

## Open Decisions

All current decisions are fixed for this design:

- Storage: SQLite through SQLAlchemy Core.
- Retention default: 30 days.
- Header storage: all request headers after sensitive-name redaction, existing secret redaction on every value, and truncation.
- Status output: no full `headers_json`; masked IPs and no recent rows by default.
- Real IP: trust proxy headers only from configured trusted direct peers; default trusted peers cover loopback and RFC1918 Docker/LAN ranges.
- Aggregation unit: route/path, not TCP port.
- Access log file: human-readable, separate from normal app log.
- Timestamp storage and status JSON: UTC epoch milliseconds.
