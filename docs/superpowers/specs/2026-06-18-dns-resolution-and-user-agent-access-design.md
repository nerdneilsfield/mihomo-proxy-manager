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

The resolver only examines proxy records whose `data["server"]` is a domain name. It leaves IP literals, missing values, empty strings, and non-string values unchanged.

Resolution uses the source DNS server list in order. For a node, the resolver queries the first server; if it fails or times out, it tries the next server. The first successful IP address is used. If every server fails, the source-level `failure` policy decides whether to keep, drop, or fail.

Only `data["server"]` is rewritten. TLS and HTTP host fields such as `sni`, `servername`, `ws-opts.headers.Host`, plugin options, and names remain unchanged so the upstream protocol handshake still uses the original host metadata when required.

The resolver should support A and AAAA answers. If both are available, use the first address returned by the DNS response. Address family preference is not part of this feature.

## DNS Protocol Implementation

Implement a small internal DNS client module rather than introducing a large dependency. It should:

- Encode standard DNS wire-format queries.
- Decode DNS wire-format responses enough to read A and AAAA answers.
- Support UDP with a single request/response exchange.
- Support TCP and TLS using DNS-over-TCP framing with a two-byte length prefix.
- Support DNS-over-HTTPS by POSTing `application/dns-message` to the configured endpoint with the existing HTTP stack or a small local HTTPX client.

Each server attempt uses the configured timeout. The source refresh remains async.

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

Route User-Agent denials should log route name and a sanitized `User-Agent` value at debug or info level. They should not trigger source refresh.

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
- TLS/HTTP host metadata is preserved when `server` is rewritten.

Provider route tests cover:

- No route access config preserves existing open behavior.
- Missing `User-Agent` is forbidden when patterns are configured.
- Non-matching `User-Agent` is forbidden.
- Matching `User-Agent` receives provider YAML.
- Health and status endpoints ignore route access patterns.

## Non-Goals

- Do not rewrite SNI, Host headers, WebSocket Host headers, or other protocol host metadata.
- Do not resolve the subscription source URL host before fetching.
- Do not add route-time DNS resolution.
- Do not add DNS caching beyond the source cache that already stores refreshed proxy records.
- Do not add address family preference settings.
