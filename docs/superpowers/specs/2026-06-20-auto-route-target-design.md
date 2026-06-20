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
query parameter > User-Agent > auto_default
```

Examples:

```text
/p/token?target=surfboard
/p/token?format=quanx
/p/token?flag=meta
/p/token?client=v2rayn
```

`target=auto`, `format=auto`, `flag=auto`, or `client=auto` means "ignore query target and use `User-Agent`, then `auto_default`".

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
| `quantumult%20x` | `quantumult-x` |
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
- URL-decode query values before matching.
- Match case-insensitively.
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

If multiple signals match, use a fixed order:

```text
quantumult-x > surfboard > sing-box > loon > provider > xray-uri
```

Reason: broad strings such as `v2ray` or `meta` are easy to match accidentally, so more specific app names should win first.

## Renderer Selection

Current renderer registry already maps route output formats to renderers. Auto selection should produce an effective output config for the request.

Rules:

1. If `route.output.format != "auto"`, select `route.output.format`.
2. If `route.output.format == "auto"`, resolve target by query, then UA, then `auto_default`.
3. If target maps to an unimplemented renderer, return `400 unsupported target`.
4. If target maps to an implemented renderer, render with that renderer.
5. Renderers retain their own compatibility filtering and warning behavior.

Per-request format selection must not mutate `route.output`.

## Auto Output Config

`RouteOutputConfig` needs:

```python
format: Literal["provider", "surfboard", "quantumult-x", "xray-uri", "auto"]
auto_default: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = "provider"
```

All existing output fields remain usable as defaults for the selected renderer:

- `encoding` still affects `xray-uri`.
- `import_link`, `import_response`, `import_target`, and `resource_tag` still affect `quantumult-x`.
- `test_url`, `test_interval`, `test_timeout`, and `test_tolerance` still affect `surfboard`.
- `include_meta_comments` remains provider-only.

Validation should reject `auto_default = "auto"`.

## Companion Paths

Auto routes need companion handling for selected formats.

Existing fixed renderers may expose:

- `surfboard`: `-nodes`
- `quantumult-x`: `-import`

For `format = "auto"`, register the union of companion paths for all implemented renderers that may be selected by aliases:

```text
/p/token
/p/token-nodes
/p/token-import
```

Companion path semantics:

- `/p/token?target=surfboard` renders Surfboard main profile.
- `/p/token-nodes?target=surfboard` renders Surfboard nodes companion.
- `/p/token-import?target=quanx` renders QX import companion.
- Companion path with incompatible target returns `400 target does not support companion`.

Preferred behavior:

- If companion suffix uniquely implies a format and no query/UA target is supplied, use that implied format.
- `-nodes` implies `surfboard`.
- `-import` implies `quantumult-x`.
- Explicit query target still wins; if query target conflicts with suffix, return `400 target does not support companion`.

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

## URL Embedding

Some selected formats embed absolute URLs:

- Surfboard full profile embeds `-nodes`.
- QX import embeds the main subscription URL.

For `format = "auto"`, `server.public_base_url` is required because implemented auto targets include Surfboard and QX, and both need embedded absolute URLs.

Embedded URLs should preserve the needed query target:

```ini
policy-path=https://mpm.example.com/p/token-nodes?target=surfboard
```

QX import should embed:

```text
https://mpm.example.com/p/token?target=quanx
```

This prevents UA-dependent embedded URLs from changing format when fetched by another HTTP client.

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

- If `auto_default` exists, use `auto_default` and log a warning.
- Do not fail only because an unimplemented UA was detected.

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

## Security And Compatibility

- Query target changes only serialization, not sources or route access.
- Route access policy applies equally to main and companion paths.
- Do not log raw subscription URLs, tokens, passwords, or generated share links.
- Use existing secret redaction for render warnings.
- Do not silently drop security-critical node fields in renderers.
- Renderer allowlists remain mandatory because client support differs by target.

## Testing Strategy

Add tests for:

- Config accepts `format = "auto"`.
- Config accepts `auto_default = "provider"`.
- Config rejects `auto_default = "auto"`.
- Fixed route ignores `?target=...`.
- Auto route `?target=surfboard` uses Surfboard renderer.
- Auto route `?format=quanx` uses QX renderer.
- Auto route `?flag=meta` uses provider renderer.
- Auto route `?client=v2rayn` uses xray-uri renderer.
- Query priority is `target > format > flag > client`.
- `target=auto` uses UA then `auto_default`.
- UA `Quantumult%20X/...` selects QX.
- UA `Surfboard/...` selects Surfboard.
- UA `v2rayN/...` selects xray-uri.
- UA `FlClash/...` selects provider.
- Unknown query target returns `400`.
- Future unimplemented query target such as `singbox` returns `400`.
- Unknown UA falls back to `auto_default`.
- Auto route registers `-nodes` and `-import` companion paths.
- `-nodes` without query implies Surfboard.
- `-import` without query implies QX.
- Conflicting companion target returns `400`.
- Embedded Surfboard policy path includes `?target=surfboard`.
- Embedded QX import URL includes `?target=quanx`.
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
