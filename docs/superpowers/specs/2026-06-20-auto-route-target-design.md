# Auto Route Target Design

Date: 2026-06-20

## Goal

Allow one route URL to serve multiple subscription formats when the route opts in with `format = "auto"`.

The main use case is a single stable subscription URL for different devices:

```text
https://mpm.example.com/p/token?target=surfboard
https://mpm.example.com/p/token?target=quanx
https://mpm.example.com/p/token?target=v2rayn
```

When the query does not specify a target, the service may detect the client from `User-Agent`.

## Non-Goals

- Do not make query override active for fixed-format routes.
- Do not change the existing behavior of `provider`, `xray-uri`, `quantumult-x`, or `surfboard` routes.
- Do not implement new renderers in this design step.
- Do not silently fall back when the caller explicitly requests an unsupported target.
- Do not generate client policy/rule/DNS sections beyond what each renderer already supports.

## Research Notes

Checked related projects:

- `tindy2013/subconverter`
- `cedar2025/Xboard`
- `sub-store-org/Sub-Store`
- `Anankke/SSPanel-UIM`
- `NaclFire/SSPanel-UIM-210305`

Observed patterns:

- `subconverter` uses `/sub?target=...&url=...`; `target=auto` enables `User-Agent` detection.
- `subconverter` aliases short paths such as `/surfboard` to `/sub?target=surfboard`.
- `XBoard` accepts query `flag`; if absent it reads `User-Agent`.
- `XBoard` also accepts `types` and `filter` for node filtering.
- `Sub-Store` maps `User-Agent` to platform names such as `QX`, `Surfboard`, `Loon`, `ClashMeta`, `V2Ray`, and `sing-box`.
- `SSPanel-UIM` variants use both path-based format selection and query/UA-based selection.

Common useful practice:

- Explicit query target has priority over `User-Agent`.
- `User-Agent` is a fallback, not the only selector.
- Each target renderer must keep its own protocol and field compatibility filter.

## User-Facing Config

New route opt-in:

```toml
[routes.main]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a", "airport_b"]

[routes.main.output]
format = "auto"
auto_default = "provider"
```

`format = "auto"` means:

- Query parameters can choose the output format.
- If query does not choose a format, `User-Agent` detection can choose it.
- If neither query nor `User-Agent` chooses a supported format, use `auto_default`.
- `server.public_base_url` is mandatory for every auto route because currently selectable Surfboard and Quantumult X outputs require absolute embedded URLs.
- This spec does not add an auto target allowlist.

Fixed routes stay fixed:

```toml
[routes.surfboard.output]
format = "surfboard"
```

For fixed routes, query parameters and `User-Agent` do not change the renderer.

## Request Parameters

Support these equivalent query parameters:

```text
target
format
flag
client
```

Priority among them:

```text
target > format > flag > client
```

Overall target resolution priority:

```text
explicit query target > companion suffix implied target > User-Agent > auto_default
```

Examples:

```text
/p/token?target=surfboard
/p/token?format=quanx
/p/token?flag=meta
/p/token?client=v2rayn
```

`target=auto`, `format=auto`, `flag=auto`, or `client=auto` means "no explicit query target"; continue with companion suffix, then `User-Agent`, then `auto_default`.

Query parsing rules:

- Query selector keys are checked in key priority order: `target`, then `format`, then `flag`, then `client`.
- A key wins if it is present in the query string. If a query parameter appears multiple times, use its first value.
- A present winning key suppresses all lower-priority selector keys even when its value is blank, whitespace, `auto`, or unsupported.
- Trim the winning value before target alias matching.
- A missing value, an empty string, or a whitespace-only value after trim is equivalent to `auto`.
- A winning value of `auto` means there is no explicit query target. Continue with companion suffix, then `User-Agent`, then `auto_default`.
- If no selector key is present, there is no query target. Continue with companion suffix, then `User-Agent`, then `auto_default`.
- If the winning value is neither blank nor `auto`, it is an explicit query target. Validate that target after route access policy passes.

Examples:

```text
/p/token-nodes?target=auto
```

Uses Surfboard because `target` wins, `auto` means no explicit query target, and `nodes` companion beats `User-Agent`.

```text
/p/token-import?target=auto
```

Uses Quantumult X when the import companion is registered because `target` wins, `auto` means no explicit query target, and `import` companion beats `User-Agent`.

```text
/p/token?target=&format=quanx
```

Does not use `format=quanx`. `target` wins, blank means no explicit query target, and lower-priority query keys stay suppressed.

## Target Aliases

Initial alias table:

| Alias | Route output format |
| --- | --- |
| `provider` | `provider` |
| `clash` | `provider` |
| `mihomo` | `provider` |
| `clash-meta` | `provider` |
| `clash.meta` | `provider` |
| `meta` | `provider` |
| `xray-uri` | `xray-uri` |
| `xray` | `xray-uri` |
| `v2ray` | `xray-uri` |
| `v2rayn` | `xray-uri` |
| `v2rayng` | `xray-uri` |
| `general` | `xray-uri` |
| `quantumult-x` | `quantumult-x` |
| `quanx` | `quantumult-x` |
| `qx` | `quantumult-x` |
| `quantumult x` | `quantumult-x` |
| `surfboard` | `surfboard` |

Reserved aliases for future implemented renderers:

| Alias | Future route output format |
| --- | --- |
| `sing-box` | `sing-box` |
| `singbox` | `sing-box` |
| `sfa` | `sing-box` |
| `sfi` | `sing-box` |
| `sfm` | `sing-box` |
| `hiddify` | `sing-box` |
| `loon` | `loon` |

Reserved aliases should return `400 unsupported target` until the renderer exists.

Alias matching rules:

- Trim whitespace.
- Match case-insensitively.
- Rely on the HTTP parser's normal one-time URL decoding; do not add a literal `quantumult%20x` alias.
- Normalize `_` to `-`.
- Keep `.` significant so `clash.meta` remains recognizable.

## User-Agent Detection

Detection should be conservative and deterministic.

Initial mapping:

| User-Agent signal | Route output format |
| --- | --- |
| contains `Quantumult%20X` | `quantumult-x` |
| contains `Quantumult X` | `quantumult-x` |
| contains `Quantumult-X` | `quantumult-x` |
| contains `Surfboard` | `surfboard` |
| contains `v2rayN` | `xray-uri` |
| contains `v2rayNG` | `xray-uri` |
| contains `v2ray` | `xray-uri` |
| contains `sing-box` | future `sing-box` |
| contains `singbox` | future `sing-box` |
| contains `Hiddify` | future `sing-box` |
| contains `SFA` | future `sing-box` |
| contains `SFI` | future `sing-box` |
| contains `SFM` | future `sing-box` |
| contains `Loon` | future `loon` |
| contains `Clash` | `provider` |
| contains `Mihomo` | `provider` |
| contains `FlClash` | `provider` |
| contains `clash-verge` | `provider` |
| contains `meta` | `provider` |

Matching rules:

- Match `User-Agent` signals case-insensitively.
- Evaluate signals in a fixed priority order, independent of their position in the `User-Agent` string.
- Implemented renderer signals win over future unimplemented signals. A matching future signal is recorded for warning/debug context but does not stop scanning for a later implemented signal.
- If only future unimplemented signals match, ignore them for selection, use `auto_default`, and log a warning.

Implemented signal priority:

```text
quantumult-x > surfboard > xray-uri > provider
```

Future signal priority, used only after the renderer exists:

```text
sing-box > loon
```

Reason: broad strings such as `meta` are easy to match accidentally. Specific xray clients such as `v2rayn` and `v2rayng` must win before broad provider signals such as `meta`.

## Renderer Selection

Current renderer registry already maps route output formats to renderers. Auto selection should produce an effective output config for the request.

Rules:

1. If `route.output.format != "auto"`, select `route.output.format`.
2. If `route.output.format == "auto"`, the app must call the shared request-time target resolver.
3. The shared resolver is used by both the main route handler and every auto companion route handler.
4. The shared resolver applies query selector priority first. If the winning query value is blank or `auto`, it produces no explicit query target and then applies companion suffix, `User-Agent`, and `auto_default`.
5. If the effective target maps to an unimplemented renderer, return `400 unsupported target`.
6. If the effective target maps to an implemented renderer, render with that renderer.
7. Renderers retain their own compatibility filtering and warning behavior.

Per-request format selection must not mutate `route.output`.

`create_app` must never index `renderers["auto"]`. No startup path registration, request-time selection, or render-time call may look up `renderers["auto"]`. Startup registers main and companion paths for auto routes without choosing a renderer. The effective renderer is selected only at request time by the shared resolver, after route access policy passes and before source fetch or rendering.

## Auto Output Config

Implementation must update `models.RouteOutputConfig`:

```python
format: Literal["provider", "surfboard", "quantumult-x", "xray-uri", "auto"]
auto_default: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = "provider"
```

Also update `config.allowed_route_output_keys` and all output validation paths so `format = "auto"` and `auto_default` are accepted where intended.

All existing output fields remain usable as defaults for the selected renderer:

- `encoding` still affects `xray-uri`.
- `import_link`, `import_response`, `import_target`, and `resource_tag` still affect `quantumult-x`.
- `test_url`, `test_interval`, `test_timeout`, and `test_tolerance` still affect `surfboard`.
- `include_meta_comments` is allowed for `auto`, but only the provider renderer uses it; non-provider effective renderers ignore it.
- For `format = "auto"`, `mode` must be omitted or set to `default`.
- Reject `mode = "full-profile"` and `mode = "server-remote"` for `format = "auto"` because the effective renderer varies per request and fixed renderer mode compatibility differs.
- Existing fixed formats retain their current `mode` rules.

Validation rules:

- Reject invalid output key names globally.
- Reject invalid values globally.
- Reject `auto_default = "auto"`.
- Reject `format = "auto"` with any `mode` other than `default`.
- For `format = "auto"`, allow fields used by currently implemented renderers.

## Companion Paths

Auto routes need companion handling for selected formats.

Existing fixed renderers may expose:

- `surfboard`: `-nodes`
- `quantumult-x`: `-import`

For `format = "auto"`, register implemented companion paths:

```text
/p/token
/p/token-nodes
/p/token-import
```

`-nodes` is always registered for auto routes. `-import` is registered only when `import_link = true`.

Companion path semantics:

- `/p/token?target=surfboard` renders Surfboard main profile.
- `/p/token-nodes?target=surfboard` renders Surfboard nodes companion.
- `/p/token-import?target=quanx` renders QX import companion.
- Companion path with incompatible target returns `400 target does not support companion`.

Preferred behavior:

- If companion suffix uniquely implies a format and no explicit query target is supplied, use that implied format before `User-Agent`.
- `-nodes` implies `surfboard`.
- `-import` implies `quantumult-x`.
- Explicit query target still wins; if query target conflicts with suffix, return `400 target does not support companion`.
- A winning query value of blank or `auto` is not an explicit query target and does not conflict with the companion suffix.
- App layer validates the companion/target matrix before calling any renderer.

Examples:

```text
/p/token-nodes
```

Uses Surfboard because `nodes` companion is Surfboard-only.

```text
/p/token-import
```

Uses Quantumult X because `import` companion is QX-only.

```text
/p/token-import?target=surfboard
```

Returns `400 target does not support companion`.

```text
/p/token-nodes?target=auto
```

Uses Surfboard even if `User-Agent` looks like Clash, because the winning query key has no explicit target and companion suffix wins before `User-Agent`.

```text
/p/token-import?target=auto
```

Uses Quantumult X when the import companion is registered.

Auto companion path collisions are configuration errors. If an auto route would register a companion path that collides with any existing route path or fixed companion path, reject config startup the same way fixed companion collisions are rejected. Do not let later route registration override or shadow the auto companion path.

## URL Embedding

Some selected formats embed absolute URLs:

- Surfboard full profile embeds `-nodes`.
- QX import embeds the main subscription URL.

For `format = "auto"`, `server.public_base_url` is required because implemented auto targets include Surfboard and QX, and both need embedded absolute URLs.

Every auto route must have `server.public_base_url` configured. This spec does not add an auto target allowlist, so config cannot prove that Surfboard or QX will never be selected for a given auto route.

Canonical embedded targets:

| Effective renderer | Canonical embedded target |
| --- | --- |
| `surfboard` | `surfboard` |
| `quantumult-x` | `quanx` |
| `xray-uri` | `v2rayn` |
| `provider` | `clash` |

Embedded URLs should always use canonical `?target=...`, regardless of whether the incoming request used `target`, `format`, `flag`, `client`, companion suffix, or `User-Agent`:

```ini
policy-path=https://mpm.example.com/p/token-nodes?target=surfboard
```

QX import should embed:

```text
https://mpm.example.com/p/token?target=quanx
```

This prevents UA-dependent embedded URLs from changing format when fetched by another HTTP client.

Canonical embedded URL data shape:

- For auto routes, the app builds per-request `main_public_url` with canonical `?target=...` for the effective renderer.
- For auto routes, the app builds per-request `companion_public_urls` keyed by companion kind. Each value uses canonical `?target=...` for the companion's required target.
- For fixed routes, existing embedded public URLs stay queryless.
- Renderers consume these URLs from the request context instead of reconstructing target selection from the incoming URL.

Do not add required fields to `RenderRequest`. Any new request fields needed for URL embedding or target context, including `main_public_url` or `companion_public_urls`, must have defaults so existing renderers and tests continue to typecheck.

## Error Policy

Explicit bad query target:

```text
400 unsupported target
```

Future but unimplemented target:

```text
400 unsupported target
```

UA maps to future but unimplemented target:

- Continue scanning for an implemented UA match.
- If no implemented UA match remains, use `auto_default` and log a warning.
- Do not fail only because an unimplemented UA signal was detected.

Explicit query conflicts with companion suffix:

```text
400 target does not support companion
```

Selected renderer has no supported nodes:

```text
422 no supported nodes for <format> output
```

Fixed route receives query target:

- Ignore query target.
- Render fixed format.

Order of checks for auto routes:

1. Match the existing main route or companion path. Unknown paths still return the normal route-not-found response.
2. Apply route access policy for both main and companion paths before explicit target validation, source fetch, or rendering.
3. If access policy denies the request, return `403` without exposing target parser, target support, or companion compatibility details.
4. Resolve target with the shared request-time resolver.
5. Validate explicit query target and companion compatibility.
6. Fetch sources and render with the effective renderer.

Explicit future target returns `400 unsupported target`. Fixed routes ignore all query selection keys.

## Security And Compatibility

- Query target changes only serialization, not sources or route access.
- Route access policy applies equally to main and companion paths and runs before target validation for matched route paths.
- Do not log raw subscription URLs, tokens, passwords, or generated share links.
- Use existing secret redaction for render warnings.
- Do not silently drop security-critical node fields in renderers.
- Renderer allowlists remain mandatory because client support differs by target.

## Testing Strategy

Add tests for:

- Config accepts `format = "auto"`.
- Config accepts `auto_default = "provider"`.
- Config rejects `auto_default = "auto"`.
- Config rejects `format = "auto"` with `mode = "full-profile"`.
- Config rejects `format = "auto"` with `mode = "server-remote"`.
- Fixed route ignores `?target=...`.
- Fixed route ignores selector query and `User-Agent` together.
- Auto route `?target=surfboard` uses Surfboard renderer.
- Auto route `?format=quanx` uses QX renderer.
- Auto route `?flag=meta` uses provider renderer.
- Auto route `?client=v2rayn` uses xray-uri renderer.
- Query priority is `target > format > flag > client`.
- Winning selector key suppresses lower-priority selector keys even when the winning value is blank.
- Blank, missing-value, or whitespace-only selector values are treated as `auto` and no explicit query target.
- `target=auto` on the main path uses UA then `auto_default`.
- `target=auto` on `-nodes` with Clash UA resolves to Surfboard.
- `target=auto` on `-import` resolves to QX when import companion exists.
- UA `Quantumult%20X/...` selects QX.
- UA `Surfboard/...` selects Surfboard.
- UA `v2rayN/...` selects xray-uri.
- UA matching is case-insensitive.
- Mixed UA with future unimplemented and implemented signals selects the implemented signal.
- Specific xray UA signals such as `v2rayn` and `v2rayng` beat broad provider signals such as `meta`.
- UA `FlClash/...` selects provider.
- Unknown query target returns `400`.
- Future unimplemented query target such as `singbox` returns `400`.
- Unknown UA falls back to `auto_default`.
- Auto route registers `-nodes` and `-import` companion paths.
- Auto route requires `server.public_base_url`.
- `-import` is not registered when `import_link = false`.
- Auto companion route collisions are rejected by config.
- `-nodes` without query implies Surfboard.
- `-import` without query implies QX.
- Companion suffix beats `User-Agent`.
- Conflicting companion target returns `400`.
- Access policy denial on an auto route returns `403` before invalid explicit target validation.
- Access policy denial on an auto companion route returns `403` before companion/target compatibility validation.
- Embedded Surfboard policy path includes `?target=surfboard`.
- Embedded QX import URL includes `?target=quanx`.
- Auto embedded `main_public_url` and `companion_public_urls` use canonical `?target=...`.
- Embedded URLs use canonical targets for incoming `target`, `format`, `flag`, and `client` requests.
- Fixed-format embedded URLs remain queryless.
- `RenderRequest` keeps defaults for new fields and passes typecheck.
- Route access policy applies to auto companion paths.
- Existing fixed-format route tests still pass.

## Documentation Updates

Implementation should update:

- `README.md`
- `README_EN.md`
- `docs/route-formats.md`
- `examples/config.toml`

Docs should show:

```toml
[routes.main.output]
format = "auto"
auto_default = "provider"
```

And subscription examples:

```text
https://mpm.example.com/p/token?target=surfboard
https://mpm.example.com/p/token?target=quanx
https://mpm.example.com/p/token?target=v2rayn
https://mpm.example.com/p/token?target=clash
```

## Rollout Plan

1. Extend model and validation for `format = "auto"` and `auto_default`.
2. Add target alias resolver and UA resolver with unit tests.
3. Add app-layer effective renderer selection.
4. Register auto route companion paths.
5. Preserve explicit target in embedded companion/main URLs.
6. Update docs, examples, and README files.
7. Run typecheck and full test suite.
