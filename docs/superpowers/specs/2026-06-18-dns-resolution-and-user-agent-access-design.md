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

Supported DNS server URL forms:

- `udp://host:53`
- `tcp://host:53`
- `tls://host:853`
- `https://host/dns-query`

`failure` values:

- `keep`: keep the original domain in the node and add a warning.
- `drop`: remove that node from the refreshed source and add a warning.
- `fail`: fail the whole source refresh.

Validation rejects empty `servers`, unsupported schemes, missing hosts, invalid timeout values, and unknown failure values. Validation should not make network calls.

## DNS Resolution Behavior

The resolver only examines proxy records whose `data["server"]` is a domain name. It leaves IP literals, missing values, empty strings, and non-string values unchanged. It must work on copied proxy data and must not mutate the input `ProxyRecord` objects in place.

Resolution uses the source DNS server list in order. For a node, the resolver queries the first server; if it fails or times out, it tries the next server. The first successful IP address is used. If every server fails, the source-level `failure` policy decides whether to keep, drop, or fail.

The top-level `data["server"]` field is the target field to rewrite. Host metadata fields are not interchangeable with `server`:

- Existing `servername`, `sni`, `ws-opts.headers.Host`, plugin options, and names must be preserved exactly and must not be overwritten with the resolved IP.
- If a TLS-like node has no explicit `servername` or `sni`, the resolver should add `servername` with the original domain before replacing `server`, so Mihomo does not implicitly use the resolved IP as the TLS hostname.
- If a WebSocket node has no explicit `ws-opts.headers.Host`, the resolver should add that Host value with the original domain before replacing `server`, so virtual-host routing remains stable.
- Existing explicit host metadata always wins. The resolver only fills missing metadata needed to preserve the original hostname semantics.

The resolver supports A and AAAA answers with deterministic selection. For each DNS server, query A first. If the A response has at least one usable address, use the first A address from that response. If no A address is usable, query AAAA and use the first usable AAAA address. IPv6 addresses are written as plain string values in `server`. Address family preference configuration is not part of this feature.

Any server attempt that produces no usable address counts as a failed attempt for that server. This includes timeout, transport failure, malformed or mismatched response, truncated UDP response, SERVFAIL, REFUSED, NXDOMAIN, NODATA, CNAME-only response with no final A/AAAA answer, or response without a matching question. The resolver then tries the next configured server. After all configured servers fail, the source-level `failure` policy applies.

When `failure = "drop"` removes every transformed proxy from a source, the source refresh fails with the same stale-cache behavior as other refresh failures. Successful cache writes must never contain an empty proxy list.

DNS-enabled sources should not use conditional subscription fetches. Because the cache stores resolved `server` values, a `304 Not Modified` response would not provide the original domains needed for re-resolution after DNS answers, DNS config, or failure policy changes. For DNS-enabled sources, refresh should fetch the full subscription body without `If-None-Match` or `If-Modified-Since`.

## DNS Protocol Implementation

Implement a small internal DNS client module rather than introducing a large dependency. It should:

- Encode standard DNS wire-format queries.
- Decode DNS wire-format responses enough to read A and AAAA answers.
- Support UDP with a single request/response exchange.
- Support TCP and TLS using DNS-over-TCP framing with a two-byte length prefix.
- Support DNS-over-HTTPS by POSTing `application/dns-message` to the configured endpoint.

DoH and DoT must preserve the existing network safety posture:

- DoH must bound response size, avoid unsafe redirects, redact configured endpoint secrets in errors, and avoid leaking raw DNS payloads in logs.
- DoT must use `ssl.create_default_context`, pass the DNS server host as `server_hostname`, validate certificates, and avoid plaintext fallback.
- TCP, TLS, and DoH responses must enforce reasonable DNS message size bounds.

Each server attempt uses the configured timeout. DNS work in the source refresh remains async, cancellation-safe, and must close sockets or HTTP responses on cancellation. The resolver should process nodes with bounded per-source concurrency so `nodes x servers x A/AAAA x timeout` does not serialize the entire refresh unnecessarily.

`SourceRefresher` should receive a resolver dependency through its constructor or another explicit injection point. Tests must be able to use a fake resolver without real DNS or monkeypatching private implementation details.

## User-Agent Access Configuration

Add route-level access config:

```toml
[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*", "clash.meta/*"]
```

`user_agent` is a list of shell-style glob patterns. Matching uses Python `fnmatch.fnmatchcase`, so matching is case-sensitive and does not normalize client values.

Default behavior remains open: when `routes.<name>.access.user_agent` is omitted or empty, the route accepts any `User-Agent`, including a missing header.

When patterns are configured:

- Missing `User-Agent` returns `403 Forbidden`.
- Non-matching `User-Agent` returns `403 Forbidden`.
- Matching `User-Agent` proceeds through the existing provider route flow.

Health and status endpoints are not affected by route User-Agent access rules.

## Error Handling and Logging

DNS warnings are source refresh warnings and are stored with the source cache when refresh succeeds. Warning messages should name the source and proxy name but must not include subscription URLs, secrets, or raw share links.

For `failure = "fail"`, the refresh result becomes a failed refresh and stale cache behavior remains the same as existing refresh failures.

For successful DNS-enabled refreshes, DNS warnings are appended to parser warnings, stored in `SourceCache.warnings`, and included in `RefreshResult.warning_count`. Warning fields derived from proxy names or DNS errors must be sanitized and length-limited because subscription content is attacker-controlled.

Route User-Agent denials should log route name and a sanitized `User-Agent` value at debug or info level. Sanitization strips control characters and truncates long values. Denials must run immediately after route lookup and before cache reads, background refresh spawning, blocking refresh waits, or rendering.

## Testing Strategy

Configuration tests cover:

- Global DNS defaults.
- Source DNS inheritance and overrides.
- Multiple DNS server parsing.
- Invalid DNS schemes and invalid failure values.
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
- DoH uses POST with `application/dns-message`, bounds response size, and parses response messages.
- UDP, TCP, TLS, and DoH transports are tested with fake/injected transports and do not use real network.

Refresher integration tests cover:

- Disabled DNS makes no resolver calls.
- Enabled DNS runs after source rename/filter and before cache write.
- DNS-enabled refresh does not send conditional request validators.
- DNS warnings are appended to parser warnings and reflected in `RefreshResult.warning_count`.
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

- Do not rewrite SNI, Host headers, WebSocket Host headers, or other protocol host metadata.
- Do not resolve the subscription source URL host before fetching.
- Do not add route-time DNS resolution.
- Do not add DNS caching beyond the source cache that already stores refreshed proxy records.
- Do not add address family preference settings.
