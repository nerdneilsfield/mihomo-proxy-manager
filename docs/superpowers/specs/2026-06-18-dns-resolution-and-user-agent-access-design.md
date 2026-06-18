# DNS Resolution and User-Agent Access Design

## Goal

Add two opt-in controls for subscription routes:

- Resolve proxy node domain names to IP addresses during source refresh using configured DNS servers.
- Restrict client access to generated subscription routes by matching request `User-Agent` values against route-level glob patterns.

Both features must preserve existing behavior when omitted from the configuration.

## Current Flow

The application loads TOML into dataclass configs, validates it, refreshes each source by downloading and parsing subscriptions, applies source transforms, then stores `ProxyRecord` values in the source cache. Provider routes read cached records, apply route transforms, repair duplicate names, and render Mihomo provider YAML.

DNS resolution belongs in the refresh flow after parsing and source-level transform, before the cache write. User-Agent access control belongs at the provider route entry point before cache refresh or rendering work.

## DNS Configuration

Add a global `[dns]` table with defaults:

```toml
[dns]
servers = ["udp://1.1.1.1:53"]
timeout = "5s"
failure = "keep"
```

Add source-level DNS config under `[sources.<name>.dns]`:

```toml
[sources.airport_a.dns]
enabled = true
servers = ["https://dns.google/dns-query", "tls://1.1.1.1:853"]
timeout = "3s"
failure = "drop"
```

Source DNS defaults:

- `enabled`: defaults to `false`.
- `servers`: defaults to global `dns.servers`.
- `timeout`: defaults to global `dns.timeout`.
- `failure`: defaults to global `dns.failure`.

## Configuration Model Changes

Add these config models:

- `DnsConfig`: global DNS defaults with `servers`, `timeout`, and `failure`.
- `SourceDnsConfig`: source DNS behavior with `enabled`, `servers`, `timeout`, and `failure`.
- `RouteAccessConfig`: route access rules with `user_agent` patterns.

Add `dns: DnsConfig` to `AppConfig`, `dns: SourceDnsConfig` to `SourceConfig`, and `access: RouteAccessConfig` to `RouteConfig`. Add `"dns"` to the `load_config()` top-level table allowlist. Source DNS parsing follows the existing source sub-table pattern used by `fetch`, `refresh`, `rename`, `filter`, and `plugins`.

Supported DNS server URL forms:

- `udp://host:53`
- `tcp://host:53`
- `tls://host:853`
- `tls://ip:853?servername=dns.example.com`
- `https://host/dns-query`

`failure` values:

- `keep`: keep the original domain in the node and add a warning.
- `drop`: remove that node from the refreshed source and add a warning.
- `fail`: fail the whole source refresh.

Validation rejects empty `servers`, unsupported schemes, missing hosts, invalid timeout values, and unknown failure values. Validation should not make network calls.

DNS server endpoints are outbound network targets and must use the same SSRF posture as subscription URLs. Static validation rejects private IP literals, loopback, link-local, multicast, reserved, unspecified addresses, and blocked private hostnames unless `security.allow_private_network_urls = true`. Runtime connection code must also resolve DNS server hostnames with the system resolver before connecting and reject any resolved private address unless private-network URLs are allowed. This rule applies to UDP, TCP, TLS, and DoH endpoints. DoH additionally uses the existing safe HTTP request path.

DoT certificate verification uses the DNS server host as the TLS SNI and certificate hostname by default. For DoT endpoints whose network address is an IP but whose certificate is issued to a DNS name, users can set a `servername` query parameter:

```toml
[dns]
servers = ["tls://1.1.1.1:853?servername=cloudflare-dns.com"]
```

The `servername` value is only for validating the DNS server connection. It is unrelated to proxy node `server` or proxy node `servername`.

## DNS Resolution Behavior

The resolver only examines proxy records whose `data["server"]` is a domain name. It leaves IP literals, missing values, empty strings, and non-string values unchanged.

Resolution uses the source DNS server list in order. For a node, the resolver queries the first server; if it fails or times out, it tries the next server. The first successful IP address is used. If every server fails, the source-level `failure` policy decides whether to keep, drop, or fail.

The top-level `data["server"]` field is the target field to rewrite. Host metadata fields are not interchangeable with `server`:

- Existing `servername`, `sni`, `ws-opts.headers.Host`, plugin options, and names must be preserved exactly and must not be overwritten with the resolved IP.
- If a TLS-like node has no explicit `servername` or `sni`, the resolver should add `servername` with the original domain before replacing `server`, so Mihomo does not implicitly use the resolved IP as the TLS hostname. A TLS-like node is one with `tls = true`, `security = "tls"`, or `security = "reality"`.
- If a WebSocket node has no explicit `ws-opts.headers.Host`, the resolver should add that Host value with the original domain before replacing `server`, so virtual-host routing remains stable. A WebSocket node is one with `network = "ws"`.
- Existing explicit host metadata always wins. The resolver only fills missing metadata needed to preserve the original hostname semantics.

The resolver supports A and AAAA answers with deterministic selection. For each DNS server, query A first. If the A response has at least one usable address, use the first A address from that response. If no A address is usable, query AAAA and use the first usable AAAA address. IPv6 addresses are written as plain string values in `server`. Address family preference configuration is not part of this feature.

This is intentionally IPv4-preferred for dual-stack domains in v1. Pure IPv6 domains still work after the A query returns no usable address. Configurable address-family preference and racing A/AAAA queries are non-goals for this feature.

Any server attempt that produces no usable address counts as a failed attempt for that server. This includes timeout, transport failure, malformed or mismatched response, truncated UDP response, SERVFAIL, REFUSED, NXDOMAIN, NODATA, CNAME-only response with no final A/AAAA answer, or response without a matching question. The resolver then tries the next configured server. After all configured servers fail, the source-level `failure` policy applies.

When `failure = "drop"` removes every transformed proxy from a source, the source refresh fails with the same stale-cache behavior as other refresh failures. Successful cache writes must never contain an empty proxy list.

DNS-enabled sources should not use conditional subscription fetches. Because the cache stores resolved `server` values, a `304 Not Modified` response would not provide the original domains needed for re-resolution after DNS answers, DNS config, or failure policy changes. For DNS-enabled sources, refresh should fetch the full subscription body without `If-None-Match` or `If-Modified-Since`.

DNS config changes take effect on the next source refresh. Existing cache entries remain resolved with the previous DNS settings until refresh runs or the cache is cleared. This matches the existing cache model where config changes do not rewrite already stored cache entries at route-read time.

Resolved IP freshness is controlled by the source refresh cadence. The resolver does not store DNS TTLs, does not track per-node resolution timestamps, and does not refresh IPs independently of source refresh. Operators should set `sources.<name>.refresh.interval` lower than the DNS freshness they require. `cache.max_stale` can still serve stale resolved IPs after refresh failures, just as it can serve stale subscription data today.

The resolver runs after `apply_transform`, whose output already owns copied proxy `data` dictionaries. The resolver may mutate those transformed dictionaries in place while producing the cache candidate. It must not mutate parsed records, old cache records, or route-render input records.

## DNS Protocol Implementation

Implement a small internal DNS client module rather than introducing a large dependency. It should:

- Encode standard DNS wire-format queries.
- Decode DNS wire-format responses enough to read A and AAAA answers.
- Support UDP with a single request/response exchange.
- Support TCP and TLS using DNS-over-TCP framing with a two-byte length prefix.
- Support DNS-over-HTTPS by POSTing `application/dns-message` to the configured endpoint.

DoH and DoT must preserve the existing network safety posture:

- DoH must use the existing `SafeHttpClient` redirect and SSRF behavior, send `POST`, set `Content-Type: application/dns-message`, strip unsafe headers on cross-origin redirects, respect the configured redirect limit, and reject DNS messages larger than the DNS response cap.
- DoT must use `ssl.create_default_context`, pass the DNS server host or explicit DNS endpoint `servername` as `server_hostname`, validate certificates, and avoid plaintext fallback.
- DNS payloads must never be logged.
- UDP responses are capped at 512 bytes and do not use EDNS0 in v1.
- TCP, TLS, and DoH DNS messages are capped at 4096 bytes in v1.

The configured DNS timeout is a per-query timeout: one A or AAAA query to one DNS server. DNS work in the source refresh remains async, cancellation-safe, and must close sockets or HTTP responses on cancellation. The resolver processes nodes with bounded per-source concurrency. V1 uses a fixed bound of 16 concurrent node resolutions per source and does not add a separate concurrency config option.

`SourceRefresher` should receive `dns_resolver: DnsResolver | None` through its constructor. `DnsResolver` exposes an async method that accepts transformed records and a `SourceDnsConfig`, then returns resolved records plus DNS warnings. Tests must be able to use a fake resolver without real DNS or monkeypatching private implementation details.

## User-Agent Access Configuration

Add route-level access config:

```toml
[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*", "clash.meta/*"]
```

`user_agent` is a list of shell-style glob patterns. Matching uses Python `fnmatch.fnmatchcase`, so matching is case-sensitive and does not normalize client values.

Pattern syntax is shell glob syntax, not a regular expression language. `*` matches any characters, including `/`, and bracket expressions such as `[abc]` and `[!abc]` use `fnmatch` character-class semantics. Users who need literal bracket characters must escape or avoid them according to Python `fnmatch` behavior.

Default behavior remains open: when `routes.<name>.access.user_agent` is omitted or empty, the route accepts any `User-Agent`, including a missing header.

When patterns are configured:

- Missing `User-Agent` returns `403 Forbidden`.
- Non-matching `User-Agent` returns `403 Forbidden`.
- Matching `User-Agent` proceeds through the existing provider route flow.

Health and status endpoints are not affected by route User-Agent access rules.

Existing path-collision validation still applies: a route path cannot collide with `server.health_path` or `server.status_path`, so route access rules cannot shadow health or status endpoints.

## Error Handling and Logging

DNS warnings are source refresh warnings and are stored with the source cache when refresh succeeds. Warning messages should name the source and proxy name but must not include subscription URLs, secrets, or raw share links.

For `failure = "fail"`, the refresh result becomes a failed refresh and stale cache behavior remains the same as existing refresh failures.

For successful DNS-enabled refreshes, DNS warnings are appended to parser warnings, stored in `SourceCache.warnings`, and included in `RefreshResult.warning_count`. Warning fields derived from proxy names or DNS errors must be sanitized and length-limited because subscription content is attacker-controlled.

Warnings are capped to keep cache files bounded. V1 stores at most 100 warning strings per source refresh. If more warnings are produced, append one final summary warning with the omitted count and drop the remaining detailed warnings.

Route User-Agent denials should log route name and a sanitized `User-Agent` value at debug or info level. Sanitization strips control characters and truncates long values. Denials must run immediately after route lookup and before cache reads, background refresh spawning, blocking refresh waits, or rendering.

The cache schema does not need to change for v1. DNS rewriting changes values inside proxy dictionaries, and optional added proxy fields such as `servername` are normal Mihomo proxy fields already represented by the existing `ProxyRecord.data` mapping. If a future implementation stores DNS TTLs, resolver metadata, or original/resolved parallel records in `SourceCache`, that change must bump `CURRENT_SCHEMA_VERSION`.

## Testing Strategy

Configuration tests cover:

- `[dns]` is accepted as an allowed top-level table.
- Global DNS defaults.
- Source DNS inheritance and overrides.
- Multiple DNS server parsing.
- DNS server SSRF checks for private IP literals, blocked private hostnames, and runtime-resolved private DNS server hosts.
- Invalid DNS schemes and invalid failure values.
- DoT `servername` query parameter parsing and validation.
- Route access user-agent patterns.

DNS resolver tests cover:

- Domain `server` values are replaced with returned IPs.
- IP literals and non-domain values are unchanged.
- Ordered failover tries the second server after the first fails.
- `keep`, `drop`, and `fail` failure policies.
- Existing `servername`, `sni`, `ws-opts.headers.Host`, plugin options, and names are preserved when `server` is rewritten.
- Missing `servername` and WebSocket Host metadata are filled with the original domain only when needed to preserve hostname semantics.
- Input `ProxyRecord` objects are not mutated.
- `drop` failure that removes every node fails the refresh rather than writing an empty cache.

DNS client tests cover:

- DNS query encoding for A and AAAA.
- DNS response decoding for A, AAAA, compressed names, NXDOMAIN, NODATA, CNAME-only responses, malformed responses, mismatched transaction IDs, and truncated packets.
- TCP and TLS two-byte length framing.
- DoH uses `SafeHttpClient`, POSTs with `application/dns-message`, follows the existing redirect policy, bounds DNS response size to 4096 bytes, and parses response messages.
- DoT verifies certificates with the DNS endpoint host or explicit endpoint `servername`.
- UDP, TCP, TLS, and DoH transports are tested with fake/injected transports and do not use real network.

Refresher integration tests cover:

- Disabled DNS makes no resolver calls.
- Enabled DNS runs after source rename/filter and before cache write.
- DNS-enabled refresh does not send conditional request validators.
- DNS config changes take effect on next refresh, not route read.
- DNS warnings are appended to parser warnings and reflected in `RefreshResult.warning_count`.
- Warning detail is capped at 100 entries plus an omitted-count summary.
- `failure = "fail"` preserves stale cache and records `last_error`.

Provider route tests cover:

- No route access config preserves existing open behavior.
- Missing `User-Agent` is forbidden when patterns are configured.
- Non-matching `User-Agent` is forbidden.
- Matching `User-Agent` receives provider YAML.
- Health and status endpoints ignore route access patterns.
- Missing or non-matching `User-Agent` returns before `cache_store.get`, `refresher.refresh`, background refresh spawning, or rendering.
- Matching is case-sensitive: `mihomo/1.19.5` matches `mihomo/*`, while `Mihomo/1.19.5` does not.
- Empty configured pattern lists behave like omitted access config and keep the route open.
- Logged denied `User-Agent` values are sanitized and length-limited.

## Non-Goals

- Do not overwrite existing SNI, `servername`, Host headers, WebSocket Host headers, or other protocol host metadata.
- Do not resolve the subscription source URL host before fetching.
- Do not add route-time DNS resolution.
- Do not add DNS caching beyond the source cache that already stores refreshed proxy records.
- Do not add address family preference settings.
- Do not add DNS TTL tracking or independent DNS refresh scheduling.
- Do not preserve implicit hostname behavior for every possible transport option in v1. V1 preserves explicit existing host metadata and fills missing `servername` for TLS-like nodes plus missing WebSocket Host for WebSocket nodes. Other transport-specific hostname requirements remain user-configured.
