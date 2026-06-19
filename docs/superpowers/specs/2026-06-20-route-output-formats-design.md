# Route Output Formats Design

Date: 2026-06-20

## Goal

Add first-class route outputs that users can subscribe/import directly in Surfboard, Quantumult X, and v2rayN-compatible Xray clients, while preserving the existing Mihomo provider behavior.

## Scope

Implement these route formats first:

- `surfboard`: direct-import Surfboard profile plus companion proxy-list endpoint.
- `quantumult-x`: Quantumult X remote server snippet plus companion one-click import endpoint.
- `xray-uri`: v2rayN-compatible URI subscription, base64 by default.

Keep these out of scope for this phase:

- sing-box output.
- Loon output.
- Full Xray JSON configs.
- Client-side DNS/filter/rewrite/MITM policy generation.
- Browser/UI pages for managing import links.
- Guaranteed compatibility with every historical client version.

## Existing Code Context

Current route rendering is centered on `ProviderRenderer` in `src/mihomo_proxy_manager/render.py`.

Important current behavior:

- `RouteOutputConfig.format` in `src/mihomo_proxy_manager/models.py` only allows `provider`.
- `LoadedConfig.validate()` rejects every route output format except `provider`.
- `create_app()` in `src/mihomo_proxy_manager/app.py` creates one `ProviderRenderer`.
- `ProviderRenderer.render_sync()` applies route-level filter/rename, normalizes records through Mihomo schema, repairs duplicate names, then renders `proxies:` YAML.
- Share-link parser already normalizes `ss`, `vmess`, `vless`, `trojan`, `hysteria2`, and `hy2` input into proxy dictionaries.

The new design keeps source parsing and route filtering unchanged. All new outputs render from normalized `ProxyRecord` data after route-level filter, rename, validation, and duplicate-name repair.

## User-Facing Config

Existing provider routes remain valid:

```toml
[routes.phone.output]
format = "provider"
include_meta_comments = false
```

New fields on `RouteOutputConfig`:

```toml
[routes.surfboard.output]
format = "surfboard"
mode = "full-profile"
test_url = "http://www.gstatic.com/generate_204"
test_interval = 600
test_timeout = 5
test_tolerance = 100

[routes.qx.output]
format = "quantumult-x"
mode = "server-remote"
import_link = true
import_response = "redirect"
import_target = "app-scheme"
resource_tag = "MPM"

[routes.v2rayn.output]
format = "xray-uri"
encoding = "base64"
```

Field meanings:

- `format`: `provider`, `surfboard`, `quantumult-x`, or `xray-uri`.
- `mode`: format-specific output mode.
- `encoding`: only used by `xray-uri`; valid values are `base64` and `plain`.
- `import_link`: only used by `quantumult-x`; when true, expose one-click import endpoint.
- `import_response`: only used by `quantumult-x`; valid values are `redirect` and `plain`.
- `import_target`: only used by `quantumult-x`; valid values are `app-scheme` and `universal-link`.
- `resource_tag`: label used in Quantumult X remote resource import JSON.
- `test_url`, `test_interval`, `test_timeout`, `test_tolerance`: Surfboard group test settings.

Default choices:

- `surfboard.mode = "full-profile"`.
- `quantumult-x.mode = "server-remote"`.
- `quantumult-x.import_link = true`.
- `quantumult-x.import_response = "redirect"`.
- `quantumult-x.import_target = "app-scheme"`.
- `quantumult-x.resource_tag = route.name`.
- `xray-uri.encoding = "base64"`.
- Surfboard `test_url` defaults to `http://www.gstatic.com/generate_204` because Surfboard proxy-group docs require HTTP scheme test URLs.

Concrete model fields:

```python
RouteOutputFormat = Literal["provider", "surfboard", "quantumult-x", "xray-uri"]
RouteOutputMode = Literal[
    "default",
    "full-profile",
    "server-remote",
]
RouteOutputEncoding = Literal["base64", "plain"]
QxImportResponse = Literal["redirect", "plain"]
QxImportTarget = Literal["app-scheme", "universal-link"]

@dataclass(frozen=True)
class RouteOutputConfig:
    format: RouteOutputFormat = "provider"
    include_meta_comments: bool = False
    mode: RouteOutputMode = "default"
    encoding: RouteOutputEncoding = "base64"
    import_link: bool = True
    import_response: QxImportResponse = "redirect"
    import_target: QxImportTarget = "app-scheme"
    resource_tag: str | None = None
    test_url: str = "http://www.gstatic.com/generate_204"
    test_interval: int = 600
    test_timeout: int = 5
    test_tolerance: int = 100
```

Unknown output keys should remain configuration errors. This keeps typos from silently changing subscription semantics.

Implement this by validating `[routes.<name>.output]` keys against an explicit allowlist before constructing `RouteOutputConfig`. The current loader reads known keys and would otherwise ignore extra keys, so the allowlist check is required.

Add one server-level field:

```toml
[server]
public_base_url = "https://mpm.example.com"
```

`public_base_url` is optional for existing `provider` and `xray-uri` routes, but required for `surfboard` and for `quantumult-x` when `import_link = true`, because their payloads embed absolute URLs. It must include `http` or `https` scheme and host, may include a path prefix, and must not end with `/`. It must not include query or fragment.

## Derived Companion Endpoints

Some direct-import formats need more than one payload. A route can expose its main path and renderer-owned companion paths.

Derived path rules:

- Companion paths are generated from `route.path`.
- If `route.path` has an extension, append suffix after the full path: `/p/token.surfboard` -> `/p/token.surfboard-nodes`.
- If `route.path` has no extension, append suffix directly: `/p/token` -> `/p/token-nodes`.
- Validate that no explicit route path collides with any derived companion path.
- Companion endpoints use the same route, sources, filters, and access policy as the main endpoint.

Required companion endpoints:

| Format | Main endpoint | Companion endpoint |
| --- | --- | --- |
| `surfboard` | full profile | `-nodes`: Surfboard proxy lines without `[Proxy]` header |
| `quantumult-x` | QX server snippet | `-import`: one-click import endpoint when `import_link = true` |
| `xray-uri` | URI subscription | none |
| `provider` | Mihomo provider YAML | none |

Absolute URL handling:

- Surfboard full profile must reference its `-nodes` endpoint by absolute URL in `policy-path`.
- QX import endpoint must embed the absolute main endpoint URL in `server_remote`.
- Use `server.public_base_url` when configured.
- Do not rely on `request.url` for embedded subscription URLs behind reverse proxies.
- If `public_base_url` is absent and a renderer needs an embedded absolute URL, validation fails before serving.
- For direct route bodies that do not embed URLs, Starlette request URL behavior is irrelevant.

## Render Pipeline

Create a format-neutral render pipeline:

1. Collect records for the route, as today.
2. Apply route filter and rename once.
3. Normalize render records once.
4. Repair duplicate names once.
5. Pass repaired records to target renderer.
6. Target renderer serializes bytes and content type.

The current `ProviderRenderer` does steps 2-5 internally. Move those steps into a shared helper so every renderer sees identical records.

Proposed internal types:

```python
@dataclass(frozen=True)
class RenderRequest:
    route: RouteConfig
    records: list[ProxyRecord]
    main_public_url: str
    companion_public_urls: Mapping[str, str]
    companion: str | None = None

@dataclass(frozen=True)
class RenderResponse:
    body: bytes
    media_type: str
    status_code: int = 200
    headers: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
```

Renderer interface:

```python
class RouteRenderer(Protocol):
    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        ...

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        ...
```

Keep async wrapper simple. Rendering is CPU/string work and can stay sync internally.

## Surfboard Output

Main endpoint returns a complete profile that can be imported directly.

Surfboard full-profile mode embeds node lines in `[Proxy]` and also exposes `-nodes` for group `policy-path`. This avoids relying exclusively on remote `policy-path` during first import, while still giving `Auto` and `Proxy` groups a refreshable proxy list.

Default profile shape:

```ini
[General]
proxy-test-url = http://www.gstatic.com/generate_204
test-timeout = 5

[Proxy]
SS 01 = ss, example.com, 443, encrypt-method=chacha20-ietf-poly1305, password=password, udp-relay=true
VMess 01 = vmess, example.com, 443, username=00000000-0000-0000-0000-000000000000, ws=true, tls=true, ws-path=/ws, ws-headers=Host:example.com, sni=example.com, vmess-aead=true
Trojan 01 = trojan, example.com, 443, password=password, udp-relay=true, skip-cert-verify=false, sni=example.com

[Proxy Group]
Main = select, Auto, Proxy, DIRECT
Auto = url-test, SS 01, VMess 01, Trojan 01, policy-path=https://mpm.example.com/p/token.surfboard-nodes, policy-regex-filter=.*, url=http://www.gstatic.com/generate_204, interval=600, tolerance=100, timeout=5
Proxy = select, SS 01, VMess 01, Trojan 01, policy-path=https://mpm.example.com/p/token.surfboard-nodes, policy-regex-filter=.*

[Rule]
FINAL,Main
```

Companion `-nodes` endpoint returns Surfboard proxy lines without `[Proxy]` header:

```ini
SS 01 = ss, example.com, 443, encrypt-method=chacha20-ietf-poly1305, password=password, udp-relay=true
VMess 01 = vmess, example.com, 443, username=00000000-0000-0000-0000-000000000000, ws=true, tls=true, ws-path=/ws, ws-headers=Host:example.com, sni=example.com, vmess-aead=true
Trojan 01 = trojan, example.com, 443, password=password, udp-relay=true, skip-cert-verify=false, sni=example.com
```

Supported protocols in phase one:

- `ss`
- `vmess`
- `trojan`

Explicitly unsupported in Surfboard phase one:

- `vless`, because the fetched Surfboard docs did not confirm a stable VLESS proxy line syntax.
- `hysteria2`, unless a test fixture confirms exact Surfboard import behavior. The overview shows an example, but upload/download key compatibility should be tested before claiming support.

Unsupported nodes are skipped with warnings. If all nodes are skipped, route returns `422 Unprocessable Entity`.

Surfboard direct-import risk:

- Surfboard docs show `policy-path` examples and state that a profile URL can be used as a policy path, using only proxies from `[Proxy]`.
- To reduce import risk, full-profile output includes local `[Proxy]` lines and explicit proxy names in `Auto` and `Proxy` groups.
- The `policy-path` remains in both groups so the same route can refresh node lists after import if the client supports it.

## Quantumult X Output

Main endpoint returns a QX server snippet suitable for `server_remote`:

```ini
shadowsocks=example.com:443, method=chacha20-ietf-poly1305, password=password, udp-relay=true, tag=SS 01
vmess=example.com:443, method=none, password=00000000-0000-0000-0000-000000000000, obfs=wss, obfs-host=example.com, obfs-uri=/ws, udp-relay=true, tag=VMess 01
vless=example.com:443, method=none, password=00000000-0000-0000-0000-000000000000, obfs=wss, obfs-host=example.com, obfs-uri=/ws, udp-relay=true, tag=VLESS 01
trojan=example.com:443, password=password, over-tls=true, tls-host=example.com, tls-verification=true, udp-relay=true, tag=Trojan 01
```

Companion `-import` endpoint behavior depends on `import_response` and `import_target`.

Default `import_response = "redirect"` returns HTTP 302:

```http
Location: quantumult-x:///add-resource?remote-resource=<url-encoded-json>
```

`import_response = "plain"` returns one text line:

```text
quantumult-x:///add-resource?remote-resource=<url-encoded-json>
```

`import_target = "universal-link"` changes only the target URL prefix. With redirect response it becomes the `Location`; with plain response it becomes the text body:

```text
https://quantumult.app/x/open-app/add-resource?remote-resource=<url-encoded-json>
```

The encoded JSON shape:

```json
{
  "server_remote": [
    "https://mpm.example.com/p/token.qx, tag=MPM, update-interval=86400, enabled=true"
  ]
}
```

Supported protocols in phase one:

- `ss`
- `vmess`
- `vless`
- `trojan`

Explicitly unsupported in QX phase one:

- `hysteria2`, until a reliable QX syntax is confirmed.
- VLESS Reality and VLESS Vision unless all required `reality-base64-pubkey`, `reality-hex-shortid`, and `vless-flow` fields map cleanly from source data.
- Non-node QX sections such as `filter_remote`, `rewrite_remote`, tasks, and MITM.

QX transport mapping:

| Normalized node shape | QX output |
| --- | --- |
| VMess/VLESS websocket with TLS | `obfs=wss`, `obfs-host=<host>`, `obfs-uri=<path>` |
| VMess/VLESS websocket without TLS | `obfs=ws`, `obfs-host=<host>`, `obfs-uri=<path>` |
| VMess/VLESS TCP with TLS | `obfs=over-tls`, `obfs-host=<sni>` |
| Trojan with TLS | `over-tls=true`, `tls-host=<sni>`, `tls-verification=<inverse skip-cert-verify>` |
| VLESS flow without Reality | `vless-flow=<flow>` only if QX supports the flow in sample-compatible syntax |

Nodes with unsupported Reality, ECH, unsupported `flow`, or unknown transport are skipped with warnings.

## Xray URI Output

Main endpoint returns a v2rayN-compatible URI subscription.

Default response:

- Join URI lines with `\n`.
- Base64-encode the entire joined text.
- Return `text/plain; charset=utf-8`.

Plain debug response:

- Same URI lines without outer base64 wrapper.

Supported protocols in phase one:

- `ss`
- `vmess`
- `vless`
- `trojan`

Hysteria2 remains optional. Only enable it if tests establish client-compatible parameter names.

URI rendering rules:

- Percent-encode URL fragments, userinfo, paths, and query values.
- `name` becomes URL fragment.
- `server` and `port` become host and port.
- VMess uses base64 JSON.
- VLESS uses `vless://uuid@host:port?...#name`.
- Trojan uses `trojan://password@host:port?...#name`.
- Shadowsocks uses SIP002-style URI.
- Base64 wrapper encodes the whole subscription payload, not each line separately.

## Error And Warning Policy

Renderer warnings should be included in logs. Response body should remain valid target payload and should not include warnings by default, because comments can break some importers.

Rules:

- Missing required field: skip node and warn.
- Unsupported protocol for target: skip node and warn.
- Unsupported security-critical option, such as Reality fields, ECH, unsupported certificate pinning fields, obfs, or flow: skip node and warn rather than silently dropping it.
- Supported certificate verification fields must be mapped explicitly. For QX, `skip-cert-verify=true` maps to `tls-verification=false`; `skip-cert-verify=false` maps to `tls-verification=true`.
- If every node is skipped: return HTTP 422 with a short plaintext error.
- Existing provider behavior remains unchanged unless it already drops invalid nodes.

Security note:

- Never log raw passwords, UUID-bearing full links, tokens, or original subscription URLs in warnings.
- Reuse existing secret redaction helpers where available.

## App Routing Changes

`create_app()` should build a renderer registry:

```python
renderers = {
    "provider": ProviderRenderer(...),
    "surfboard": SurfboardRenderer(...),
    "quantumult-x": QuantumultXRenderer(...),
    "xray-uri": XrayUriRenderer(...),
}
```

Route table should include main route paths and companion paths:

```python
route_by_path = {
    route.path: (route, None),
    "/p/token.surfboard-nodes": (route, "nodes"),
    "/p/token.qx-import": (route, "import"),
}
```

When serving:

1. Resolve route and companion kind by request path.
2. Gather source records exactly as current route serving does.
3. Build absolute public URL for main and companion endpoints.
4. Call target renderer.
5. Log `response.warnings` after redacting secrets.
6. Return `Response(body, status_code=response.status_code, media_type=response.media_type, headers=dict(response.headers))`.

## Validation Changes

`LoadedConfig.validate()` should:

- Accept new formats.
- Validate format-specific mode and encoding.
- Reject `xray-uri.encoding` values other than `base64` and `plain`.
- Reject Surfboard `test_url` if it does not start with `http://`.
- Reject companion path collisions.
- Reject `include_meta_comments = true` for non-provider formats unless comments are explicitly supported.
- Require `server.public_base_url` for `surfboard`.
- Require `server.public_base_url` for `quantumult-x` when `import_link = true`.
- Reject `server.public_base_url` unless it has `http` or `https` scheme, a host, no query, no fragment, and no trailing `/`.
- Validate companion path collisions against explicit route paths, other companion paths, `health_path`, `status_path`, and the status API path under `status_path`.
- Reject unknown `[routes.<name>.output]` keys before building `RouteOutputConfig`.
- Reject `import_response` values other than `redirect` and `plain`.
- Reject `import_target` values other than `app-scheme` and `universal-link`.
- Reject non-positive Surfboard timing fields and intervals over 31 days.

## Testing Strategy

Use TDD with golden outputs.

Required tests:

- Config accepts `surfboard`, `quantumult-x`, and `xray-uri`.
- Config rejects invalid modes/encodings.
- Companion path collision fails validation.
- Provider renderer output remains byte-for-byte compatible for existing tests.
- Surfboard full profile contains `Main`, `Auto`, `Proxy`, `DIRECT`, and `FINAL,Main`.
- Surfboard nodes endpoint returns proxy lines without `[Proxy]`.
- QX main endpoint returns server lines.
- QX import endpoint returns HTTP 302 by default, with `Location: quantumult-x:///add-resource?...` and valid decoded JSON.
- QX `import_response = "plain"` returns text instead of redirect.
- QX `import_target = "universal-link"` uses `https://quantumult.app/x/open-app/add-resource?...`.
- QX `import_link = false` leaves `-import` unregistered and returns 404.
- Xray default output is base64 subscription.
- Xray plain output is newline-separated URI lines.
- Unsupported protocols are skipped and all-skipped output returns 422.
- Names with spaces, `#`, `%`, comma, quote, slash, Unicode, and IPv6 addresses are either encoded correctly or rejected consistently per target.
- TLS certificate verification inversion is tested for QX and any target that needs it.
- Surfboard full profile includes `[Proxy]` lines and groups that reference both explicit proxy names and `policy-path`.
- Companion endpoints enforce the same route access policy as the main endpoint.
- Embedded absolute URLs use `server.public_base_url`, including when the incoming test request uses `http://testserver`.
- Invalid `server.public_base_url` forms fail validation: missing scheme, unsupported scheme, missing host, query, fragment, and trailing slash.
- Unknown output keys fail validation.
- Renderer warnings redact secrets.
- Media types are correct for YAML, text, redirects, and errors.
- Xray base64 output decodes to URI lines accepted by the existing share-link parser where possible.

## Rollout Order

1. Extend config model and validation.
2. Extract shared render pipeline while keeping provider behavior unchanged.
3. Add renderer registry and companion path routing.
4. Implement `xray-uri`.
5. Implement `quantumult-x`.
6. Implement `surfboard`.
7. Update docs and examples.

This order gives one simple no-companion renderer first, then one import-link companion renderer, then the Surfboard full-profile plus nodes pairing.

## References

- `docs/route-formats.md`
- Surfboard profile overview: https://getsurfboard.com/docs/profile-format/overview/
- Surfboard policy path / auto group docs: https://getsurfboard.com/docs/profile-format/proxygroup/auto/
- Quantumult X URL scheme: https://github.com/crossutility/Quantumult-X/blob/master/url-scheme.md
- Quantumult X sample config: https://github.com/crossutility/Quantumult-X/blob/master/sample.conf
- v2rayN VMess share link: https://github.com/2dust/v2rayN/wiki/Description-of-VMess-share-link
