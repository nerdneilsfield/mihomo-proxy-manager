# Provider Upstream Service Design

Date: 2026-06-17

## Goal

Build a Python service that acts as an upstream provider server for Clash/Mihomo clients.
The service downloads multiple upstream subscriptions, parses proxy nodes, applies configurable filtering and renaming, caches source-level results, and exposes hidden HTTP paths that return Mihomo-compatible proxy provider payloads.

The first implementation is an asynchronous single-process service with clear module boundaries, so cache and scheduling internals can later move to Redis or a separate worker without changing parser, transform, or renderer behavior.

## References

- Mihomo proxy providers: https://wiki.metacubex.one/en/config/proxy-providers/
- Mihomo proxy groups: https://wiki.metacubex.one/en/config/proxy-groups/
- Clash outbound and proxy providers: https://en.clash.wiki/configuration/outbound.html
- Clash configuration reference: https://en.clash.wiki/configuration/configuration-reference.html

## Non-Goals

- No user account system.
- No management API for creating or deleting routes.
- No configuration hot reload. TOML changes require service restart.
- No Redis cache in the MVP.
- No route output persistence in the MVP.
- No full Clash/Mihomo config renderer in the MVP, but renderer interfaces should allow it later.
- No download proxy support in the MVP.

## Technology

- Python project managed by `uv`.
- Python 3.11 or newer.
- HTTP server: `starlette` served by `uvicorn`.
- Async HTTP client: `httpx`.
- Configuration: TOML loaded with Python `tomllib`.
- Logging: `loguru`.
- Type checking: Astral `ty`.
- Tests: `pytest`.

The service should be async-first. Starlette endpoints, source refreshes, plugin execution, and upstream downloads use `async`. JSON file cache can start with direct small-file operations behind a cache interface; the interface should remain replaceable.

## Configuration Model

Configuration uses named TOML tables so sources, routes, and plugins can reference each other by stable names.

```toml
[server]
host = "0.0.0.0"
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
enabled = true
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
startup_refresh_mode = "background" # background | blocking
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
```

Example source, plugin, and route configuration:

```toml
[sources.airport_a]
url = "https://example.com/sub"
format = "auto"
parse_error = "skip" # skip | fail

[sources.airport_a.fetch]
timeout = "30s"
user_agent = "custom-UA"
allow_private_network = false

[sources.airport_a.fetch.headers]
Authorization = "Bearer xxx"

[sources.airport_a.refresh]
interval = "1h"
cron = ["0 4 * * *"]

[sources.airport_a.rename]
prefix = "[{source}] "
suffix = ""

[sources.airport_a.filter]
include = "香港|日本|HK|JP"
exclude = "官网|剩余|过期"
exclude_types = ["http"]

[sources.airport_a.plugins.before_fetch.turn_on]
on_failure = "abort" # abort | continue

[plugins.turn_on]
type = "http_action"
method = "POST"
url = "https://example.com/switch"
success_status = [200, 204]
allow_private_network = false
timeout = "10s"

[plugins.turn_on.headers]
Authorization = "Bearer xxx"

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
require_all_sources = false

[routes.phone.output]
format = "provider"
include_meta_comments = false

[routes.phone.rename]
prefix = "[phone] "

[routes.phone.filter]
exclude = "倍率|测试"
```

## Validation

`mpm check` and `mpm serve` both run the same configuration validation. Validation should collect all recoverable errors in one run.

Validate at least:

- TOML syntax and supported top-level tables.
- Unique route paths.
- Route paths start with `/`.
- `health_path`, optional `status_path`, and route paths do not collide.
- Hidden route paths satisfy the configured minimum entropy. The recommended shape is `/p/<128-bit-or-stronger-base64url-token>.yaml`.
- If `status_path` is omitted, the status endpoint is disabled. If it is configured, it must satisfy the same entropy rule as provider route paths.
- All route source references exist.
- All source plugin references exist.
- Regex fields compile.
- Cron expressions are valid.
- `startup_refresh_mode`, `parse_error`, plugin `on_failure`, output `format`, and plugin `type` are supported.
- Required source fields such as `url` exist.
- Source and plugin URLs use supported schemes, defaulting to `http` and `https` only.
- Source and plugin URL IP literals do not target private, loopback, link-local, multicast, or otherwise reserved networks unless that specific source or plugin sets `allow_private_network = true`. `mpm check` must not perform DNS resolution.
- Logging file path parent can be created or is writable.
- Cache directory can be created or is writable.
- Config and cache files should be readable only by the service account. `mpm check` should warn when the config file is group/world-readable.

TOML parse failure is a structural error and can stop immediately. Other validation failures should be listed together.

Runtime fetch and plugin execution must repeat URL safety checks with DNS resolution before each outbound request and each redirect hop. This preserves the no-network behavior of `mpm check` while still blocking hostnames that resolve to private, loopback, link-local, multicast, or otherwise reserved addresses.

## CLI

Initial commands:

```bash
mpm serve -c config.toml
mpm check -c config.toml
mpm refresh -c config.toml airport_a
```

`serve` starts the Starlette app and scheduler. `check` validates config without network access. `refresh` validates config first, then manually refreshes one source using the normal refresh pipeline, including plugins, conditional requests, parsing, transform, and JSON cache writes. `refresh` should print a concise success or failure summary, including source name, node count, warning count, cache path, and last error when applicable.

## Data Flow

Source refresh:

1. Acquire the per-source async refresh lock.
2. Execute `before_fetch` plugins in configured order.
3. Download upstream subscription with source-specific fetch config over global defaults. Redirects are limited by `max_redirects`, and every redirect target must pass the same URL safety checks as the original URL.
4. Send `If-None-Match` and `If-Modified-Since` when cached `etag` or `last_modified` exists.
5. On `304 Not Modified`, update attempt/success metadata and keep cached proxies.
6. Parse YAML or share-link subscription into normalized Mihomo proxy dictionaries.
7. Apply source-level filter, then source-level rename.
8. Persist source JSON cache with a temporary file plus atomic replace.
9. Update in-memory source cache and status.

Route request:

1. Match the exact hidden route path.
2. Load route source caches through the cache interface. The MVP implementation is an in-memory read-through cache backed by source JSON files.
3. If a required cache is missing, trigger refresh.
4. If `require_all_sources = true`, wait up to `server.route_refresh_wait` for missing source refreshes and return `503` if any remain unavailable.
5. If `require_all_sources = false`, return available source nodes. If no nodes are available, wait up to `server.route_refresh_wait` for missing source refreshes once before returning `503`.
6. Apply route-level filter, then route-level rename.
7. Resolve duplicate final node names by appending ` #2`, ` #3`, and so on.
8. Render provider payload YAML.

If a source has stale but still-valid cache and a refresh is due, route requests should return the stale cache and trigger refresh asynchronously. A cache is still-valid only while `now - last_success_at <= cache.max_stale`. After `max_stale`, the source is treated as unavailable unless a refresh succeeds.

Route freshness checks must use cache metadata and configuration directly, not scheduler-only in-memory state. This keeps restart behavior predictable when JSON cache files exist but scheduler state has not yet been rebuilt. Refresh due checks should use `last_attempt_at` when available, falling back to `last_success_at`, so repeated route requests after a recent failed refresh do not trigger a refresh storm.

## Scheduler

Each source can define both fixed-interval and cron refresh triggers.

- `interval` supports human duration strings such as `1h`.
- `cron` uses the configured server timezone.
- `interval` and `cron` are additive triggers. If both fire close together, the source refresh lock deduplicates the work.
- `jitter` is a random uniform delay between `0` and the configured duration before scheduler-triggered refreshes. It avoids refreshing all sources at the same instant.
- Each source has one async lock, so overlapping refresh triggers share or wait on the same in-flight refresh.
- Lock waiters use `scheduler.refresh_lock_timeout`. When the timeout expires, callers use stale cache if it is still-valid or report unavailability.

Startup behavior is configurable:

- `startup_refresh = true`, `startup_refresh_mode = "background"`: start service immediately and refresh all sources in the background.
- `startup_refresh = true`, `startup_refresh_mode = "blocking"`: refresh sources before accepting traffic.
- `startup_refresh = false`: rely on interval, cron, manual refresh, and route-triggered refresh.

Default startup behavior is background refresh.

The server lifespan should cancel scheduler tasks on shutdown, close the shared `httpx.AsyncClient`, and avoid interrupting cache writes in the middle of an atomic replace. Long-running refresh tasks may be cancelled after the normal shutdown grace period; partial temp files should be cleaned up on the next cache operation.

## Cache

The MVP persists source-level cache only. Route outputs are rendered on demand.

Example cache file:

```json
{
  "source": "airport_a",
  "schema_version": 1,
  "last_attempt_at": "2026-06-17T12:00:00+08:00",
  "last_success_at": "2026-06-17T12:00:01+08:00",
  "etag": "\"abc\"",
  "last_modified": "Wed, 17 Jun 2026 04:00:00 GMT",
  "node_count": 42,
  "warnings": [],
  "last_error": null,
  "proxies": [
    {
      "source": "airport_a",
      "data": {
        "name": "[airport_a] HK 01",
        "type": "vmess",
        "server": "example.com",
        "port": 443
      }
    }
  ]
}
```

Cache writes must avoid corrupted partial files by writing to a temporary file and atomically replacing the old cache file.
Cache files should be created with `cache.file_mode`, defaulting to owner-read/write only. Cache operations should use a per-source file lock so `mpm refresh` and a running server cannot write the same cache file concurrently from different processes.

The cache interface is the canonical read and write API. The MVP may implement it as an in-memory read-through cache backed by JSON files, but callers should not reach directly into memory dictionaries or the filesystem. If the MVP keeps in-memory entries, it must detect backing-file changes or otherwise invalidate entries so a running server can observe cache files updated by `mpm refresh`. Future Redis-backed cache implementations should satisfy the same interface.

Cache schema changes should increment `schema_version`. Unknown future schema versions are invalid. Older supported versions can be migrated during load or rejected with a clear validation/runtime error.

## Plugins

Plugins are globally defined and referenced from pipeline hook points. The MVP supports `before_fetch`; later versions can add hook points such as `after_fetch`, `before_render`, or `after_refresh_failed`.

Multiple plugins for the same hook run sequentially in TOML order. MVP plugin references can override only reference-level execution policy such as `on_failure`; they cannot override plugin implementation fields such as URL, method, headers, or body. If two sources need different HTTP action parameters, define two plugins.

The MVP includes one plugin type: `http_action`.

`http_action` fields:

- `method`
- `url`
- `headers`
- optional request body
- `success_status`
- timeout, defaulting to global HTTP timeout unless overridden
- `allow_private_network`, defaulting to the global security setting

`http_action` uses the same URL safety checks, redirect limits, response size limits, and timeout behavior as source downloads.

Each plugin reference can configure failure handling:

- `abort`: stop the current source refresh and keep old cache.
- `continue`: log the plugin failure and continue the refresh.

Default failure handling is `abort`.

## Parsing

MVP input formats:

- Clash/Mihomo YAML full config or provider payload with a `proxies` list.
- Share-link subscriptions containing `ss://`, `vmess://`, `vless://`, `trojan://`, or `hysteria2://`.
- Share-link subscriptions can be plain text or base64 encoded.

For YAML input, preserve proxy dictionaries and validate at least `name` and `type`.

For YAML input, also run per-type required-field validation for supported proxy types. For example, `ss` requires `server`, `port`, `cipher`, and `password`; `vmess` requires `server`, `port`, `uuid`, and `cipher`; `trojan` requires `server`, `port`, and `password`. Missing required fields follow the source `parse_error` policy.

For share links, convert to Mihomo proxy dictionaries. Field mapping is part of the parser contract and must be covered by tests for each supported scheme. MVP mappings must include required identity fields plus common transport and security options such as `type`/`network`, `sni`, `alpn`, TLS/Reality public-key and short-id fields, client fingerprint, VLESS `flow`, WebSocket `host`/`path`, gRPC service name, and Shadowsocks plugin options. If a field cannot be mapped reliably, record a warning and preserve a usable proxy only when required fields are still present. `format = "auto"` detection order:

1. Parse as YAML and use `proxies` when present.
2. Parse as plain text share links.
3. Base64 decode and parse as share links.
4. Treat as parse failure.

Explicit source formats should be supported for `auto`, `yaml`, and `share-links`. `auto` is the default.

Parse failure behavior is source-configurable:

- `skip`: skip bad nodes and record warnings. If no usable node remains, the source refresh fails and old cache is kept.
- `fail`: any bad node fails the source refresh.

Default is `skip`.

## Transform

Source and route layers both support the same transform primitives.

Filtering:

- `include`: regex against proxy `name`.
- `exclude`: regex against proxy `name`.
- `include_types`: case-insensitive exact match against proxy `type`.
- `exclude_types`: case-insensitive exact match against proxy `type`.

Name filters are regular expressions and therefore use regex metacharacter rules. Type filters intentionally use exact matches because proxy types are a small finite vocabulary.

Renaming:

- `prefix`
- `suffix`
- Template variables in MVP: `{source}`.

Route-level transforms should retain each proxy's internal source name while aggregating, so `{source}` remains available for templates. Internal metadata must be removed before rendering YAML.

The service does not deduplicate by server, port, credentials, or final name. It only ensures final node names are unique for valid Mihomo output. If final names collide after all transforms, append ` #2`, ` #3`, and so on. Name repair must be iterative and must avoid consuming original final names that belong to later records. For example, `["HK", "HK", "HK #2"]` must render as `["HK", "HK #3", "HK #2"]`.

## Rendering

MVP renderer:

```yaml
proxies:
  - name: "[airport_a] HK 01"
    type: vmess
    server: example.com
    port: 443
    uuid: 00000000-0000-0000-0000-000000000000
    alterId: 0
    cipher: auto
    tls: true
```

`format = "provider"` is the only MVP route output format. The renderer registry should allow later formats such as full Clash/Mihomo config.

The provider renderer must preserve all proxy fields needed by Mihomo, not only `name` and `type`. It must remove internal metadata before output and use safe YAML serialization so names and string values containing characters such as `:`, `#`, `[`, `]`, `&`, `*`, or Unicode text remain valid YAML. YAML tags from untrusted input must not be emitted.

`include_meta_comments = false` by default. When enabled, comments can include generation time, route name, source count, and node count. Comments must not include hidden route paths, upstream URLs, headers, tokens, or plugin secrets.

## HTTP API

Fixed and optional operational paths:

- `health_path`, default `/healthz`: returns service liveness only.
- `status_path`, optional and configured as a random path: returns non-sensitive source status.

Provider routes:

- Exact hidden paths configured under `[routes.*]`.
- Hidden path is the only MVP access control by design decision. Treat the path as a bearer secret: it must be high entropy, should be served only over TLS in production, should not be logged, and should be rotated by changing the TOML and restarting if leaked.
- Unknown path returns `404`.
- Route unavailable returns `503`.
- The service must not return `200` with `proxies: []` for unavailable routes, because that can cause clients to clear usable nodes.

Status output may include source names, last attempt time, last success time, node count, last error summary, and whether a refresh is in progress. It must not include upstream URLs, request headers, plugin secrets, or hidden route paths.

## Logging

Use `loguru` with independent console and file sinks.

- Console sink defaults to colored `INFO`.
- File sink defaults to `DEBUG`.
- File sink supports rotation, retention, and compression.
- Logs must redact sensitive headers, tokens, upstream subscription URLs with credentials or secret query parameters, hidden route paths, and plugin secret values. Redaction must be applied at the logging sink or global logger patch layer, not only at individual call sites.

## Error Handling

- Configuration errors stop `serve` before the HTTP server starts.
- Plugin failures follow the reference-level `on_failure` policy.
- Download errors record `last_error` and preserve old cache.
- HTTP `304` updates metadata without parsing.
- Parse errors follow source-level `parse_error`.
- Source refresh failures do not delete old cache.
- Cache write failures are refresh failures; old disk and memory cache remain in use if still-valid.
- Caches older than `cache.max_stale` are unavailable even if they can be read.
- A source refresh with zero usable nodes is treated as failure.
- Duplicate final names are repaired, not dropped.
- Missing route path returns `404`.
- Missing route data returns `503`.

## Module Boundaries

```text
mihomo_proxy_manager/
  cli.py
  config.py
  logging.py
  app.py
  scheduler.py
  refresher.py
  fetcher.py
  parsers/
  plugins/
  cache.py
  transform.py
  render.py
  status.py
```

Dependencies should flow from orchestration layers into focused services:

- `cli`, `app`, and `scheduler` call `refresher` and `render`.
- `refresher` calls `plugins`, `fetcher`, `parsers`, `transform`, and `cache`.
- `render` reads cache and applies route-level transform.
- `cache` hides persistence details behind an interface.

Core interfaces should be small and explicit:

```python
class SourceCacheStore:
    async def get(self, source_name: str) -> SourceCache | None: ...
    async def set(self, source_name: str, cache: SourceCache) -> None: ...
    async def status(self, source_name: str) -> SourceStatus: ...
    def set_refreshing(self, source_name: str, refreshing: bool) -> None: ...
    def cache_path(self, source_name: str) -> str | None: ...

class Plugin:
    async def run(self, context: PluginContext) -> PluginResult: ...

class Renderer:
    async def render(self, route: RouteConfig, proxies: list[ProxyRecord]) -> bytes: ...
```

`ProxyRecord` may contain internal metadata such as source name while inside the service. Renderers must strip that metadata from the public YAML.

## Testing

Use `pytest`.

Unit tests:

- TOML model loading and validation.
- Regex validation and filtering.
- Type filtering.
- Prefix/suffix template rendering.
- Duplicate final name repair.
- YAML provider parsing.
- YAML full-config parsing.
- Share-link parsing for `ss`, `vmess`, `vless`, `trojan`, and `hysteria2`.
- Per-type required-field validation.
- URL safety checks for private, loopback, link-local, multicast, and reserved networks.
- JSON cache atomic writes and reads.
- Cache file permissions.
- Cache schema version handling.

HTTP integration tests:

- Health path.
- Optional random status path.
- Hidden provider path output.
- Unknown path returns `404`.
- No available nodes returns `503`.
- `require_all_sources` behavior.
- Stale-while-revalidate behavior.
- Server restart loading source JSON cache through the cache interface.
- Rendered YAML can be parsed back as valid provider payload and contains all required proxy fields.

Async refresh tests:

- `before_fetch` plugin success.
- `before_fetch` plugin failure with `abort`.
- `before_fetch` plugin failure with `continue`.
- ETag and `304 Not Modified`.
- Download error preserves old cache.
- Parse error `skip` and `fail`.
- Concurrent refreshes for the same source share one in-flight refresh.
- Route request triggers refresh when cache is missing.
- Route-triggered refresh respects `server.route_refresh_wait`.
- Refresh lock waiters respect `scheduler.refresh_lock_timeout`.
- Concurrent `mpm refresh` and server refresh cannot corrupt source JSON cache.
- Redirect targets are revalidated for URL safety.

## Open Extension Points

- Redis-backed cache.
- Route output cache.
- Worker process for refreshes.
- Download proxy support.
- More plugin hook points.
- More plugin types.
- Full Clash/Mihomo config renderer.
- CLI route rendering preview.
