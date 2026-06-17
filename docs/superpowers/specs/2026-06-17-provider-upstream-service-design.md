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
- HTTP server: `starlette` served by `uvicorn`.
- Async HTTP client: `httpx`.
- Configuration: TOML loaded with Python `tomllib`.
- Logging: `loguru`.
- Type checking: `ty`.
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
status_path = "/s/random-status-path"

[cache]
dir = "data/cache"
write_indent = 2

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

[scheduler]
startup_refresh = true
startup_refresh_mode = "background" # background | blocking
jitter = "30s"

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

[plugins.turn_on.headers]
Authorization = "Bearer xxx"

[routes.phone]
path = "/p/7rKx9mQe.yaml"
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
- All route source references exist.
- All source plugin references exist.
- Regex fields compile.
- Cron expressions are valid.
- `startup_refresh_mode`, `parse_error`, plugin `on_failure`, output `format`, and plugin `type` are supported.
- Required source fields such as `url` exist.
- Logging file path parent can be created or is writable.
- Cache directory can be created or is writable.

TOML parse failure is a structural error and can stop immediately. Other validation failures should be listed together.

## CLI

Initial commands:

```bash
mpm serve -c config.toml
mpm check -c config.toml
mpm refresh -c config.toml airport_a
```

`serve` starts the Starlette app and scheduler. `check` validates config without network access. `refresh` manually refreshes one source using the normal refresh pipeline, including plugins, conditional requests, parsing, transform, and JSON cache writes.

## Data Flow

Source refresh:

1. Acquire the per-source async refresh lock.
2. Execute `before_fetch` plugins in configured order.
3. Download upstream subscription with source-specific fetch config over global defaults.
4. Send `If-None-Match` and `If-Modified-Since` when cached `etag` or `last_modified` exists.
5. On `304 Not Modified`, update attempt/success metadata and keep cached proxies.
6. Parse YAML or share-link subscription into normalized Mihomo proxy dictionaries.
7. Apply source-level filter and rename.
8. Persist source JSON cache with a temporary file plus atomic replace.
9. Update in-memory source cache and status.

Route request:

1. Match the exact hidden route path.
2. Load route source caches from memory, falling back to source JSON files.
3. If a required cache is missing, trigger refresh.
4. If `require_all_sources = true`, wait for missing source refreshes and return `503` if any remain unavailable.
5. If `require_all_sources = false`, return available source nodes. If no nodes are available, wait for missing source refreshes once before returning `503`.
6. Apply route-level filter and rename.
7. Resolve duplicate final node names by appending ` #2`, ` #3`, and so on.
8. Render provider payload YAML.

If a source has stale but valid cache and a refresh is due, route requests should return the stale cache and trigger refresh asynchronously.

## Scheduler

Each source can define both fixed-interval and cron refresh triggers.

- `interval` supports human duration strings such as `1h`.
- `cron` uses the configured server timezone.
- `jitter` avoids refreshing all sources at the same instant.
- Each source has one async lock, so overlapping refresh triggers share or wait on the same in-flight refresh.

Startup behavior is configurable:

- `startup_refresh = true`, `startup_refresh_mode = "background"`: start service immediately and refresh all sources in the background.
- `startup_refresh = true`, `startup_refresh_mode = "blocking"`: refresh sources before accepting traffic.
- `startup_refresh = false`: rely on interval, cron, manual refresh, and route-triggered refresh.

Default startup behavior is background refresh.

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
      "name": "[airport_a] HK 01",
      "type": "vmess",
      "server": "example.com",
      "port": 443
    }
  ]
}
```

Cache writes must avoid corrupted partial files by writing to a temporary file and atomically replacing the old cache file.

## Plugins

Plugins are globally defined and referenced from pipeline hook points. The MVP supports `before_fetch`; later versions can add hook points such as `after_fetch`, `before_render`, or `after_refresh_failed`.

The MVP includes one plugin type: `http_action`.

`http_action` fields:

- `method`
- `url`
- `headers`
- optional request body
- `success_status`
- timeout, defaulting to global HTTP timeout unless overridden

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

For share links, convert to Mihomo proxy dictionaries. If a field cannot be mapped reliably, record a warning. `format = "auto"` detection order:

1. Parse as YAML and use `proxies` when present.
2. Parse as plain text share links.
3. Base64 decode and parse as share links.
4. Treat as parse failure.

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

Renaming:

- `prefix`
- `suffix`
- Template variables in MVP: `{source}`.

Route-level transforms should retain each proxy's internal source name while aggregating, so `{source}` remains available for templates. Internal metadata must be removed before rendering YAML.

The service does not deduplicate by server, port, credentials, or final name. It only ensures final node names are unique for valid Mihomo output. If final names collide after all transforms, append ` #2`, ` #3`, and so on.

## Rendering

MVP renderer:

```yaml
proxies:
  - name: ...
    type: ...
```

`format = "provider"` is the only MVP route output format. The renderer registry should allow later formats such as full Clash/Mihomo config.

`include_meta_comments = false` by default. When enabled, comments can include generation time, route name, source count, and node count. Comments must not include hidden route paths, upstream URLs, headers, tokens, or plugin secrets.

## HTTP API

Fixed and optional operational paths:

- `health_path`, default `/healthz`: returns service liveness only.
- `status_path`, optional and configured as a random path: returns non-sensitive source status.

Provider routes:

- Exact hidden paths configured under `[routes.*]`.
- Hidden path is the only MVP access control.
- Unknown path returns `404`.
- Route unavailable returns `503`.
- The service must not return `200` with `proxies: []` for unavailable routes, because that can cause clients to clear usable nodes.

Status output may include source names, last attempt time, last success time, node count, last error summary, and whether a refresh is in progress. It must not include upstream URLs, request headers, plugin secrets, or hidden route paths.

## Logging

Use `loguru` with independent console and file sinks.

- Console sink defaults to colored `INFO`.
- File sink defaults to `DEBUG`.
- File sink supports rotation, retention, and compression.
- Logs should redact sensitive headers, tokens, upstream subscription URLs when needed, and plugin secret values.

## Error Handling

- Configuration errors stop `serve` before the HTTP server starts.
- Plugin failures follow the reference-level `on_failure` policy.
- Download errors record `last_error` and preserve old cache.
- HTTP `304` updates metadata without parsing.
- Parse errors follow source-level `parse_error`.
- Source refresh failures do not delete old cache.
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
- JSON cache atomic writes and reads.

HTTP integration tests:

- Health path.
- Optional random status path.
- Hidden provider path output.
- Unknown path returns `404`.
- No available nodes returns `503`.
- `require_all_sources` behavior.

Async refresh tests:

- `before_fetch` plugin success.
- `before_fetch` plugin failure with `abort`.
- `before_fetch` plugin failure with `continue`.
- ETag and `304 Not Modified`.
- Download error preserves old cache.
- Parse error `skip` and `fail`.
- Concurrent refreshes for the same source share one in-flight refresh.
- Route request triggers refresh when cache is missing.

## Open Extension Points

- Redis-backed cache.
- Route output cache.
- Worker process for refreshes.
- Download proxy support.
- More plugin hook points.
- More plugin types.
- Full Clash/Mihomo config renderer.
- CLI route rendering preview.
