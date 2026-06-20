# Route Format Research

Date: 2026-06-19

本文记录 `mihomo-proxy-manager` route 输出格式的格式边界、字段映射、写法示例和后续实现检查项。当前实现支持：

- source input: `auto`, `yaml`, `share-links`
- route output: `provider`, `xray-uri`, `quantumult-x`, `surfboard`
- provider payload: Mihomo/Clash `proxies:` YAML
- direct subscription payloads: Xray/V2Ray URI subscription, Quantumult X server lines plus import companion, Surfboard minimal full profile plus nodes companion

目标不是一次做成所有客户端的完整配置生成器，而是先把本服务已经规范化的 proxy node，可靠输出成各客户端可消费的订阅片段。完整 profile 只在格式确实需要 wrapper 时生成。

## Terms And Boundaries

新增格式时先区分四件事：

- source format: 上游订阅如何解析进来，例如 YAML、URI lines、base64 URI lines。
- normalized proxy: 项目内部统一 proxy dictionary，例如 `name/type/server/port/password/uuid/tls/ws-opts`。
- route output format: 本服务 route 返回什么内容，例如 Mihomo provider、Xray URI lines、sing-box JSON。
- client profile wrapper: 客户端完整配置如何引用本服务 route，例如 `proxy-providers`、`server_remote`、`policy-path`。

容易混淆的边界：

- Clash/Mihomo provider 是 node provider payload，不是完整 Clash 配置。
- Xray 订阅常见是 URI lines 或 base64 URI lines；Xray-core 官方配置是 JSON，两者不是同一种东西。
- sing-box 官方配置是 JSON；只输出 `outbounds` fragment 与输出完整 client config 是两件事。
- Surfboard、Quantumult X、Loon 更接近 profile/snippet 格式；它们的 DNS、rule、rewrite、MITM 等配置通常属于用户本机策略，不应由本服务默认猜测。
- `ss/vmess/vless/trojan/hysteria2` 这些协议可以被转换，但 Reality、ECH、uTLS fingerprint、UDP over TCP、port hopping 等扩展字段必须逐项确认目标客户端是否支持。

## Support Matrix

| Target | Current support | Route format | Recommended MVP | Notes |
| --- | --- | --- | --- | --- |
| Clash/Mihomo provider | Supported as input and output | `provider` | Keep current `proxies:` YAML | Mihomo provider content也可为 URI lines/base64，但 YAML 最稳。 |
| Xray/V2Ray subscription | Implemented route output | `xray-uri` | Default base64 URI subscription; `encoding = "plain"` optional | Direct v2rayN-compatible subscription; no companion endpoint. Full Xray JSON remains future work. |
| Quantumult X profile | Implemented route output | `quantumult-x` | Server lines on main route plus `-import` companion | `server_remote`/server lines output; app-scheme/universal-link and redirect/plain import supported. |
| Surfboard profile | Implemented route output | `surfboard` | Minimal full profile with `Main`, `Auto`, `Proxy`, `FINAL,Main` | Main route returns profile; `-nodes` companion returns proxy lines for `policy-path`. |
| sing-box subscription | Future / not implemented | `sing-box` | `{ "outbounds": [...] }` JSON fragment | Full config 需 DNS、route、inbounds、selector/urltest 策略。 |
| Loon profile | Future / not implemented | `loon` | `[Proxy]` snippet | Loon 手册稳定示例覆盖 HTTP/HTTPS/SS/SSR/VMess/Trojan。 |

Minimal direct route output snippets:

```toml
[server]
public_base_url = "https://mpm.example.com"

[routes.surfboard.output]
format = "surfboard"

[routes.qx.output]
format = "quantumult-x"

[routes.v2rayn.output]
format = "xray-uri"
```

Each `[routes.*]` still needs `path` and `sources`; omitted here for brevity.

## Common Source Input

Current source parsing already accepts common Xray/V2Ray style subscription payloads:

```toml
[sources.airport_a]
url = "https://example.com/sub"
format = "auto"        # auto | yaml | share-links
parse_error = "skip"   # skip | fail
```

`auto` detection order:

1. Parse as YAML and use `proxies` when present.
2. Parse as plain text share links.
3. Base64 decode and parse as share links.
4. Treat as parse failure.

Supported share-link schemes today:

- `ss://`
- `vmess://`
- `vless://`
- `trojan://`
- `hysteria2://` and `hy2://`

For output expansion, keep source parsing independent from route rendering. A route should render from normalized proxy dictionaries, not from raw upstream text, otherwise route-level filtering/renaming/cache behavior will diverge by output format.

## Clash/Mihomo Provider

### What To Output

Route output `provider` returns Mihomo/Clash provider content:

```yaml
proxies:
  - name: "HK 01"
    type: ss
    server: example.com
    port: 443
    cipher: chacha20-ietf-poly1305
    password: "password"
```

Mihomo provider file content supports three mutually exclusive payload styles:

- YAML with top-level `proxies:`
- URI lines
- base64-encoded URI lines

This project should keep YAML as the default because it preserves normalized proxy dictionaries, nested options, numeric ports, booleans, quoting, and comments better than URI serialization. If a future `clash-uri` mode is added, it should be explicit, not a behavior change of `provider`.

### Service Config

```toml
[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a", "airport_b"]
require_all_sources = false

[routes.phone.output]
format = "provider"
include_meta_comments = false
```

### Client Config

```yaml
proxy-providers:
  phone:
    type: http
    url: "https://mpm.example.com/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    path: ./proxy_providers/phone.yaml
    interval: 3600
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300
```

### Field Notes

| Normalized field | Mihomo field | Notes |
| --- | --- | --- |
| `name` | `name` | Must be unique after route rename/filter. |
| `type = ss` | `type: ss` | Needs `cipher` and `password`. |
| `type = vmess` | `type: vmess` | Needs `uuid`; `alterId` should default to `0` only when absent and target accepts AEAD. |
| `type = vless` | `type: vless` | Needs `uuid`; `flow`, Reality fields, TLS fields must be preserved when present. |
| `type = trojan` | `type: trojan` | Needs `password`; TLS is normally expected. |
| `type = hysteria2` | `type: hysteria2` | Needs `password` or auth field; port hopping and obfs need explicit mapping. |

MVP keeps the existing `provider` renderer unchanged. No provider alias is implemented.

## Xray/V2Ray Subscription

### What It Means

Xray-core official configuration is JSON. A complete client config has top-level modules such as `log`, `dns`, `routing`, `inbounds`, and `outbounds`.

In subscription ecosystems, "Xray subscription" usually means one of these:

- plain share-link lines
- base64-encoded share-link lines
- complete Xray JSON config

The first two are valid source input and route output for this project. The implemented `xray-uri` route returns a direct v2rayN-compatible subscription. Complete Xray JSON config output remains future work.

### Implemented `xray-uri` Output

Route config:

```toml
[routes.phone.output]
format = "xray-uri"
encoding = "base64" # default; plain is also available
```

The main route directly returns URI lines. There is no companion endpoint. With default `encoding = "base64"`, the joined URI-line payload is base64-wrapped for common subscription clients. `encoding = "plain"` returns raw lines.

Good plain route output:

```text
vmess://<base64-json>
vless://00000000-0000-0000-0000-000000000000@example.com:443?encryption=none&security=tls&sni=example.com&type=ws&host=example.com&path=%2Fws#VLESS%2001
trojan://password@example.com:443?sni=example.com&type=ws&host=example.com&path=%2Fws#Trojan%2001
ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpwYXNzd29yZA@example.com:443#SS%2001
hysteria2://password@example.com:443/?sni=example.com&insecure=1#HY2%2001
```

`encoding = "base64"` wraps the whole text payload after joining lines with `\n`. Do not base64 each URI separately.

Supported protocols are `ss`, `vmess`, `vless`, `trojan`, and `hysteria2`. Unsupported protocols, Shadowsocks plugin fields, and unsupported security-critical fields are skipped with route render warnings; if every node is skipped, the route returns HTTP 422 with `no supported nodes for xray-uri output`.

### URI Mapping

| Protocol | Required fields | URI shape | Notes |
| --- | --- | --- | --- |
| `ss` | `cipher`, `password`, `server`, `port` | `ss://base64(method:password)@host:port#name` | SIP002 plugin opts are not implemented; nodes with `plugin` / `plugin-opts` are skipped. |
| `vmess` | `uuid`, `server`, `port` | `vmess://base64(json)` | JSON carries `v`, `ps`, `add`, `port`, `id`, `aid`, `net`, `type`, `host`, `path`, `tls`, `sni`, and optional `fp`。 |
| `vless` | `uuid`, `server`, `port`, `encryption=none` | `vless://uuid@host:port?...#name` | Implemented query params include `encryption=none`, `security=tls`, `sni`, `fp`, `type`, `path`, `host`, and `serviceName`; `flow` and Reality are skipped. |
| `trojan` | `password`, `server`, `port` | `trojan://password@host:port?...#name` | Implemented query params include `sni`, `allowInsecure`, `fp`, `type`, `path`, `host`, and `serviceName`. |
| `hysteria2` | `password`, `server`, optional `port`/`ports` | `hysteria2://password@host:port/?...#name` | Implemented query params include `sni`, `insecure=1`, `obfs`, and `obfs-password`; bandwidth fields are intentionally not shared by URI per Hysteria2 docs. |

Shared URI rules:

- `name` -> URL fragment; always percent-encode.
- `server`, `port` -> URL host and port; IPv6 host must be bracketed.
- `uuid` or `password` -> URL userinfo; percent-encode reserved characters.
- `network = ws` -> `type=ws`, `path`, `host`.
- TLS fields -> VLESS uses `security=tls`; VMess JSON uses `tls`; SNI and client fingerprint map where supported by the implemented renderer.
- Hysteria2 `ports` can render the URI authority port-hopping form.
- Reality, ECH, certificate pinning-like fields, and VLESS `flow` are skipped until exact mapping is implemented.

### Future `xray-json` Output

Only add this if callers need a full config, because a correct Xray config needs local inbound and routing decisions, not only proxy nodes:

```toml
[routes.phone.output]
format = "xray-json"
mode = "outbounds" # outbounds | full-config
```

Outbounds fragment example:

```json
{
  "outbounds": [
    {
      "tag": "VLESS 01",
      "protocol": "vless",
      "settings": {
        "address": "example.com",
        "port": 443,
        "id": "00000000-0000-0000-0000-000000000000",
        "encryption": "none",
        "flow": "xtls-rprx-vision"
      },
      "streamSettings": {
        "network": "ws",
        "security": "tls",
        "tlsSettings": {
          "serverName": "example.com"
        },
        "wsSettings": {
          "path": "/ws",
          "headers": {
            "Host": "example.com"
          }
        }
      }
    }
  ]
}
```

Full config generation should require explicit defaults:

```toml
[routes.phone.output]
format = "xray-json"
mode = "full-config"
inbound = "socks"
listen = "127.0.0.1"
port = 10808
final_outbound = "selector" # future if selector/routing is implemented
```

Do not invent local `inbounds`, DNS, routing, sniffing, or balancer behavior without config.

## Future sing-box Subscription

### What To Output

sing-box uses JSON. Proxy nodes live under `outbounds`.

Recommended MVP:

```toml
[routes.phone.output]
format = "sing-box"
mode = "outbounds" # outbounds | full-config
```

Example:

```json
{
  "outbounds": [
    {
      "type": "shadowsocks",
      "tag": "SS 01",
      "server": "example.com",
      "server_port": 443,
      "method": "chacha20-ietf-poly1305",
      "password": "password"
    }
  ]
}
```

Official outbound types include many protocols, but this project should initially map only protocols already normalized by the parser:

- `ss` -> `shadowsocks`
- `vmess` -> `vmess`
- `vless` -> `vless`
- `trojan` -> `trojan`
- `hysteria2` / `hy2` -> `hysteria2`

### Outbound Skeletons

Shadowsocks:

```json
{
  "type": "shadowsocks",
  "tag": "SS 01",
  "server": "example.com",
  "server_port": 443,
  "method": "chacha20-ietf-poly1305",
  "password": "password",
  "network": "tcp"
}
```

VMess:

```json
{
  "type": "vmess",
  "tag": "VMess 01",
  "server": "example.com",
  "server_port": 443,
  "uuid": "00000000-0000-0000-0000-000000000000",
  "security": "auto",
  "alter_id": 0,
  "tls": {
    "enabled": true,
    "server_name": "example.com"
  },
  "transport": {
    "type": "ws",
    "path": "/ws",
    "headers": {
      "Host": "example.com"
    }
  }
}
```

VLESS:

```json
{
  "type": "vless",
  "tag": "VLESS 01",
  "server": "example.com",
  "server_port": 443,
  "uuid": "00000000-0000-0000-0000-000000000000",
  "flow": "xtls-rprx-vision",
  "tls": {
    "enabled": true,
    "server_name": "example.com"
  }
}
```

Trojan:

```json
{
  "type": "trojan",
  "tag": "Trojan 01",
  "server": "example.com",
  "server_port": 443,
  "password": "password",
  "tls": {
    "enabled": true,
    "server_name": "example.com"
  }
}
```

Hysteria2:

```json
{
  "type": "hysteria2",
  "tag": "HY2 01",
  "server": "example.com",
  "server_port": 443,
  "password": "password",
  "up_mbps": 100,
  "down_mbps": 100,
  "obfs": {
    "type": "salamander",
    "password": "obfs-pass"
  },
  "tls": {
    "enabled": true,
    "server_name": "example.com",
    "insecure": false
  }
}
```

### Field Mapping Notes

| Normalized field | sing-box field | Notes |
| --- | --- | --- |
| `name` | `tag` | Must be unique across outbounds. |
| `server` | `server` | Required for all five MVP protocols. |
| `port` | `server_port` | Hysteria2 may use `server_ports`; do not set both. |
| `cipher` | `method` | Shadowsocks only. |
| `uuid` | `uuid` | VMess/VLESS. |
| `alterId` | `alter_id` | VMess; `0` means AEAD. |
| `password` | `password` | Shadowsocks/Trojan/Hysteria2. |
| `network` | `network` | sing-box uses `tcp`/`udp`; websocket is `transport.type = "ws"` rather than `network = "ws"`。 |
| `ws-opts.path` | `transport.path` | Only when transport type is `ws`. |
| `ws-opts.headers.Host` | `transport.headers.Host` | Preserve case as string key. |
| `tls/servername/sni` | `tls.enabled`, `tls.server_name` | `skip-cert-verify` maps to `tls.insecure`. |
| Reality fields | `tls.reality` / related fields | Add only after confirming exact sing-box version syntax. |

Use `mode = "outbounds"` first. Full config generation needs explicit decisions for local inbounds, DNS, route rules, selector/urltest groups, final outbound, and clash API compatibility.

## Surfboard Profile

Surfboard follows Surge profile format. A complete profile commonly uses `[General]`, `[Proxy]`, `[Proxy Group]`, `[Rule]`, and optional sections such as `[Host]` or `[Panel]`.

### Implemented Output Mode

```toml
[server]
public_base_url = "https://mpm.example.com"

[routes.phone.output]
format = "surfboard"
# mode omitted/default is treated as full-profile; mode = "full-profile" is equivalent
```

`public_base_url` is required. The effective mode is full-profile only. The main route returns a minimal Surfboard profile; `<route.path>-nodes` returns proxy lines without `[Proxy]` for `policy-path` use.

### Nodes Companion

```ini
SS 01 = ss, example.com, 443, encrypt-method=chacha20-ietf-poly1305, password=password
VMess 01 = vmess, example.com, 443, username=00000000-0000-0000-0000-000000000000, ws=true, tls=true, ws-path=/ws, ws-headers=Host:example.com, sni=example.com
Trojan 01 = trojan, example.com, 443, password=password, skip-cert-verify=false, sni=example.com
```

### Main Route Full Profile

```ini
[General]

[Proxy]
SS 01 = ss, example.com, 443, encrypt-method=chacha20-ietf-poly1305, password=password
VMess 01 = vmess, example.com, 443, username=00000000-0000-0000-0000-000000000000, udp-relay=false, ws=true, tls=true, ws-path=/ws, ws-headers=Host:example.com, sni=example.com, vmess-aead=true
Trojan 01 = trojan, example.com, 443, password=password, udp-relay=false, skip-cert-verify=false, sni=example.com

[Proxy Group]
Main = select, Auto, Proxy, DIRECT
Auto = url-test, SS 01, VMess 01, Trojan 01, policy-path=https://mpm.example.com/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml-nodes, policy-regex-filter=.*, url=http://www.gstatic.com/generate_204, interval=600, tolerance=100, timeout=5
Proxy = select, SS 01, VMess 01, Trojan 01, policy-path=https://mpm.example.com/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml-nodes, policy-regex-filter=.*

[Rule]
FINAL,Main
```

The main group is always `Main = select, Auto, Proxy, DIRECT`; the rule tail is `FINAL,Main`. The nodes companion uses the same route access policy as the main route.

Client-compatible protocols are `ss`, `vmess`, and `trojan`. Although Surfboard documentation lists Hysteria2, some Surfboard client decoders reject `hysteria2` profile lines; `hysteria2` is therefore skipped automatically for import safety. Surfboard's documented protocol list does not include VLESS, so `vless` nodes are also skipped with route render warnings. Nodes using unsupported security-critical fields such as `reality-opts`, `ech-opts`, `flow`, or client TLS `fingerprint` are skipped; if every node is skipped, the route returns HTTP 422 with `no supported nodes for surfboard output`.

### Field Mapping Notes

| Normalized field | Surfboard field | Notes |
| --- | --- | --- |
| `name` | left side before `=` | Escape commas and line breaks; avoid duplicate names. |
| `ss.cipher` | `encrypt-method=` | Shadowsocks only. |
| `uuid` | `username=` | VMess. VLESS is skipped because Surfboard does not document VLESS support. |
| `password` | `password=` | SS/Trojan. |
| `udp` / `udp-relay` | `udp-relay=` | Emitted for SS, VMess, and Trojan when present. |
| `network = ws` | `ws=true`, `ws-path=`, `ws-headers=` | `ws-headers` uses `Header:value` pairs; multiple headers use `|` in examples. |
| TLS enabled | `tls=true` or protocol-specific TLS defaults | VMess uses `tls=true`; Trojan is TLS-based. |
| `sni` | `sni=` | Preserve if present. |
| `skip-cert-verify` | `skip-cert-verify=` | Boolean lower-case. |
| `alterId` | `vmess-aead=` | VMess uses `true` when absent or `0`, `false` for non-zero legacy nodes. |

## Quantumult X Profile

Quantumult X uses sections:

- `[server_remote]` for remote resources.
- `[server_local]` for inline nodes.
- `[policy]` for policy groups.
- `[filter_remote]` and `[filter_local]` for rules.
- `[rewrite_*]`, `[task_local]`, `[mitm]` for features outside route node output scope.

### Implemented Output Mode

```toml
[server]
public_base_url = "https://mpm.example.com"

[routes.phone.output]
format = "quantumult-x"
# mode omitted/default is treated as server-remote; mode = "server-remote" is equivalent
```

The effective mode is server-remote. The main route returns server lines only, without a `[server_local]` header. When `import_link = true` (default), `<route.path>-import` returns a one-click import target for adding the main route as a `server_remote` resource. `public_base_url` is required when `import_link = true`.

Import response defaults to redirect:

```toml
[routes.phone.output]
format = "quantumult-x"
import_response = "redirect"      # redirect | plain
import_target = "app-scheme"      # app-scheme | universal-link
resource_tag = "MPM"
```

Default redirect target starts with `quantumult-x:///add-resource?...`. `import_response = "plain"` returns the target as text instead of HTTP 302. `import_target = "universal-link"` uses `https://quantumult.app/x/open-app/add-resource?...`.

### Remote Subscription Reference

Client profile wrapper:

```ini
[server_remote]
https://mpm.example.com/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.qx, tag=MPM, update-interval=86400, enabled=true

[policy]
static = MPM, resource-tag-regex=^MPM, server-tag-regex=.*, img-url=https://example.com/icon.png
available = MPM-Auto, resource-tag-regex=^MPM, server-tag-regex=.*, check-interval=600
```

The route itself returns server definitions, not the wrapper above.

### Main Route Server Lines

```ini
shadowsocks=example.com:443, method=chacha20-ietf-poly1305, password=password, udp-relay=true, tag=SS 01
vmess=example.com:443, method=none, password=00000000-0000-0000-0000-000000000000, obfs=wss, obfs-host=example.com, obfs-uri=/ws, tag=VMess 01
vless=example.com:443, method=none, password=00000000-0000-0000-0000-000000000000, obfs=wss, obfs-host=example.com, obfs-uri=/ws, tag=VLESS 01
trojan=example.com:443, password=password, over-tls=true, tls-host=example.com, tls-verification=true, udp-relay=false, tag=Trojan 01
```

Supported protocols are `shadowsocks`, `vmess`, `vless`, and `trojan`. QX Reality fields are supported when the source carries `reality-opts.public-key`; unsupported security-critical fields and unsupported Shadowsocks plugin fields are skipped with route render warnings; if every node is skipped, the route returns HTTP 422 with `no supported nodes for quantumult-x output`.

Reality and Vision mapping uses QX-specific params:

```ini
vless=example.com:443, method=none, password=00000000-0000-0000-0000-000000000000, obfs=over-tls, obfs-host=apple.com, reality-base64-pubkey=base64pubkey, reality-hex-shortid=0123456789abcdef, vless-flow=xtls-rprx-vision, tag=VLESS Reality
```

### Field Mapping Notes

| Normalized field | Quantumult X field | Notes |
| --- | --- | --- |
| `name` | `tag=` | `tag` may contain spaces; quote only if QX requires for special chars. |
| `server`, `port` | `host:port` after protocol prefix | IPv6 needs validation. |
| `ss.cipher` | `method=` | QX sample includes classic and 2022 methods. |
| `uuid` | `password=` for VMess/VLESS | QX uses `password` key for UUID-like IDs. |
| `vless.method` | `method=none` | Required in sample. |
| `vmess.cipher` | `method=none/aes-128-gcm/chacha20-poly1305` | `aead=false` only for legacy VMess. |
| `udp` / `udp-relay` | `udp-relay=` | Emitted when source explicitly sets UDP relay. |
| SS simple obfs | `obfs=http/tls`, `obfs-host=`, `obfs-uri=` | Direct Mihomo `obfs*` fields map to QX fields. |
| TLS over TCP | `obfs=over-tls` or `over-tls=true` | SS/VMess/VLESS use `obfs=over-tls`; Trojan uses `over-tls=true`. |
| WebSocket TLS | `obfs=wss`, `obfs-host=`, `obfs-uri=` | `obfs-host` is also TLS host for `wss`. |
| WebSocket cleartext | `obfs=ws`, `obfs-host=`, `obfs-uri=` | No `over-tls`. |
| `skip-cert-verify` | `tls-verification=false` | Inverted boolean. |
| `reality-opts.public-key` | `reality-base64-pubkey=` | Supported for QX over-tls/wss capable protocols. |
| `reality-opts.short-id` | `reality-hex-shortid=` | Optional. |
| `flow = "xtls-rprx-vision"` | `vless-flow=xtls-rprx-vision` | VLESS only; other flow values are skipped. |

Do not include `[filter_local]`, `[rewrite_local]`, scripts, or MITM in route output. Those are user policy, not node serialization.

## Future Loon Profile

Loon supports inline proxy nodes under `[Proxy]`. The documented examples cover HTTP, HTTPS, Shadowsocks, ShadowsocksR, VMess, and Trojan. Loon UI also distinguishes subscription nodes when adding nodes to a policy group, so remote subscription behavior should be tested separately from inline `[Proxy]` serialization.

### Recommended Output Modes

```toml
[routes.phone.output]
format = "loon"
mode = "proxy-section" # proxy-section | proxy-lines
```

`proxy-section` emits `[Proxy]` and node lines. `proxy-lines` emits only node lines if a Loon remote subscription import flow requires it.

### Inline Node Snippet

```ini
[Proxy]
SS 01 = Shadowsocks, example.com, 443, chacha20-ietf-poly1305, "password"
VMess 01 = vmess, example.com, 443, aes-128-gcm, "00000000-0000-0000-0000-000000000000", transport:ws, path:/ws, host:example.com, over-tls:true, tls-name:example.com, skip-cert-verify:false
Trojan 01 = trojan, example.com, 443, password, tls-name:example.com, skip-cert-verify:false
```

### Policy Group Snippet

```ini
[Proxy Group]
MPM = select, SS 01, VMess 01, Trojan 01
Auto = url-test, SS 01, VMess 01, Trojan 01, url=http://www.gstatic.com/generate_204, interval=600

[Rule]
FINAL,MPM
```

### Field Mapping Notes

| Normalized field | Loon field | Notes |
| --- | --- | --- |
| `name` | left side before `=` | Keep unique; avoid comma/newline. |
| `ss.type` | `Shadowsocks` | Capitalization follows examples. |
| `ss.cipher` | positional encryption method | Password is quoted in examples. |
| `vmess.uuid` | quoted positional UUID | Encryption method is positional before UUID. |
| `vmess.network = ws` | `transport:ws`, `path:`, `host:` | Comma-separated key-value suffixes. |
| TLS enabled | `over-tls:true` | VMess uses suffix; Trojan is TLS-based. |
| `sni` | `tls-name:` | Preserve from source `sni/servername`. |
| `skip-cert-verify` | `skip-cert-verify:true/false` | Boolean lower-case. |
| `vless`, `hysteria2` | not in fetched reliable examples | Treat as unsupported until validated in Loon client docs/tests. |

Future `loon` output should start with inline node snippets. Remote subscription group wiring differs by Loon UI and profile style; keep it documented separately from node serialization.

## Escaping And Serialization Rules

These rules should be shared by all non-YAML renderers:

- Always render from normalized proxy dictionaries after route filters and renames.
- Make names unique after route prefix/suffix changes. If duplicates remain, append a stable suffix like `#2`, not a random value.
- Percent-encode URL fragments, paths, userinfo, and query values for URI formats.
- JSON renderers must use `json.dumps(..., ensure_ascii=False)` or equivalent, with stable key ordering only if tests need deterministic snapshots.
- INI/profile renderers must reject raw newlines in names, passwords, headers, paths, and tags.
- Commas in INI-style values need target-specific quoting or rejection. Do not rely on naive `split(",")` behavior.
- Booleans must follow target syntax: YAML booleans for Mihomo, JSON booleans for sing-box/Xray, lower-case text for Surfboard/Loon/QX.
- For inverted booleans, keep tests explicit: `skip-cert-verify=true` often maps to `tls-verification=false` in QX and `tls.insecure=true` in sing-box.
- IPv6 hosts must be tested per format; URI formats need `[addr]`, while many INI formats may not accept brackets consistently.
- Header maps need deterministic ordering so rendered lines are stable.
- Unsupported but security-relevant fields must not be silently dropped: TLS, Reality, certificate verification, flow, obfs, plugin, UDP behavior.

## Unsupported Field Policy

Use a renderer-level result object rather than returning only text:

```python
@dataclass(frozen=True)
class RenderResult:
    content: str
    content_type: str
    warnings: list[str]
    skipped: list[str]
```

Recommended behavior:

- Missing required field for a protocol: skip node with warning when route parse policy is permissive; fail route render when strict.
- Unknown optional field: render node and warn only when field may affect security or connectivity.
- Unsupported protocol in target renderer: skip node with warning; if all nodes are skipped, return an error.
- Unsupported TLS/Reality/obfs field: fail that node by default, because silently dropping it can produce a wrong or insecure connection.
- Renderer should include enough warning context: route name, node name, protocol, field, target format.

## Recommended Config Shape

Current and future output enum shape:

```toml
[routes.phone.output]
format = "provider" # implemented: provider | xray-uri | surfboard | quantumult-x
mode = "default"    # surfboard also accepts full-profile; quantumult-x also accepts server-remote
encoding = "base64" # xray-uri: base64 | plain
```

Target-specific modes:

| Format | MVP mode | Later modes |
| --- | --- | --- |
| `provider` | existing YAML provider | `uri` or `base64-uri` if needed later |
| `xray-uri` | default base64 URI subscription | plain URI lines |
| `xray-json` | future `outbounds` | future `full-config` |
| `sing-box` | future `outbounds` | future `full-config`, `selector`, `urltest` |
| `surfboard` | full profile | future additional modes if needed |
| `quantumult-x` | server-remote lines plus import companion | future full-profile if needed |
| `loon` | future `proxy-section` | future `proxy-lines`, `full-profile` |

Renderer split:

- `provider`: existing `proxies:` YAML renderer.
- `xray-uri`: URI line renderer.
- `xray-json`: future JSON outbounds/full config renderer.
- `sing-box`: future JSON outbounds/full config renderer.
- `surfboard`: INI-style profile/snippet renderer.
- `quantumult-x`: INI-style server snippet renderer.
- `loon`: future INI-style proxy snippet renderer.

## Cross-Target Field Map

| Normalized proxy | Mihomo | Xray URI | Xray JSON | sing-box | Surfboard | Quantumult X | Loon |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `name` | `name` | fragment | `tag` | `tag` | left side before `=` | `tag=` | left side before `=` |
| `type` | `type` | scheme | `protocol` | `type` | positional protocol | line prefix | positional protocol |
| `server` | `server` | host | `settings.address` | `server` | positional host | `host:port` | positional host |
| `port` | `port` | port | `settings.port` | `server_port` | positional port | `host:port` | positional port |
| `cipher` | `cipher` | SS userinfo / VMess `scy` | protocol-specific | `method` | `encrypt-method` | `method` | encryption method |
| `uuid` | `uuid` | userinfo / VMess JSON `id` | `settings.id` | `uuid` | `username` for VMess | `password` for VMess/VLESS | quoted UUID for VMess |
| `password` | `password` | userinfo | protocol-specific | `password` | `password` | `password` | quoted/positional password |
| `alterId` | `alterId` | VMess JSON `aid` | VMess settings | `alter_id` | `vmess-aead` legacy flag | `aead=false` for legacy | not documented in fetched example |
| `network = ws` | `network`, `ws-opts` | `type=ws`, `path`, `host` | `streamSettings.wsSettings` | `transport.type = ws` | `ws=true`, `ws-path`, `ws-headers` | `obfs=ws/wss`, `obfs-uri`, `obfs-host` | `transport:ws`, `path`, `host` |
| TLS enabled | `tls` | VLESS `security=tls`; VMess JSON `tls`; Trojan keeps TLS implicit unless query params are present | `streamSettings.security` | `tls.enabled` | `tls=true` / protocol default | `over-tls=true` or `obfs=wss` | `over-tls:true` |
| `sni` / `servername` | `sni` / `servername` | `sni` | TLS server name | `tls.server_name` | `sni` | `tls-host` / `obfs-host` | `tls-name` |
| `skip-cert-verify` | `skip-cert-verify` | `allowInsecure` / `insecure` | `allowInsecure` | `tls.insecure` | `skip-cert-verify` | `tls-verification=false` | `skip-cert-verify:true` |
| Reality public key | `reality-opts.public-key` | skipped | Reality settings | target-version-specific TLS Reality | not validated | `reality-base64-pubkey` | not validated |
| Reality short id | `reality-opts.short-id` | skipped | Reality settings | target-version-specific TLS Reality | not validated | `reality-hex-shortid` | not validated |
| VLESS flow | `flow` | skipped | `settings.flow` | `flow` | not validated | `vless-flow` for `xtls-rprx-vision` | not validated |

## Implementation Checklist

Before adding any renderer:

1. Define exact route `format`, `mode`, `encoding`, and content type.
2. Add golden tests for one node of each supported protocol.
3. Add tests for duplicate names after route rename.
4. Add tests for escaping: spaces, `#`, `%`, comma, quote, slash, Unicode, IPv6.
5. Add tests for `skip-cert-verify` inversion where needed.
6. Add tests for unsupported protocol and unsupported security fields.
7. Add route-level integration test that source filters still apply before rendering.
8. Keep cache key independent from output format only if normalized proxies are cached before render; otherwise include output config in rendered cache key.
9. Set `Content-Type` deliberately:
   - YAML provider: `application/yaml` or existing behavior.
   - URI lines and INI snippets: `text/plain; charset=utf-8`.
   - JSON: `application/json; charset=utf-8`.
10. Document examples in README only after the renderer exists; keep this research doc as design reference.

## References

- Mihomo proxy-providers configuration: https://wiki.metacubex.one/en/config/proxy-providers/
- Mihomo proxy-provider content: https://wiki.metacubex.one/en/config/proxy-providers/content/
- Xray configuration file: https://xtls.github.io/en/config/
- Xray VLESS outbound: https://xtls.github.io/en/config/outbounds/vless.html
- v2rayN VMess share link: https://github.com/2dust/v2rayN/wiki/Description-of-VMess-share-link
- Trojan-Go URL scheme: https://azadzadeh.github.io/trojan-go/en/developer/url/
- sing-box configuration: https://sing-box.sagernet.org/configuration/
- sing-box outbounds: https://sing-box.sagernet.org/configuration/outbound/
- sing-box Shadowsocks outbound: https://sing-box.sagernet.org/configuration/outbound/shadowsocks/
- sing-box VMess outbound: https://sing-box.sagernet.org/configuration/outbound/vmess/
- sing-box VLESS outbound: https://sing-box.sagernet.org/configuration/outbound/vless/
- sing-box Trojan outbound: https://sing-box.sagernet.org/configuration/outbound/trojan/
- sing-box Hysteria2 outbound: https://sing-box.sagernet.org/configuration/outbound/hysteria2/
- Surfboard profile overview: https://getsurfboard.com/docs/profile-format/overview/
- Surfboard VMess format: https://getsurfboard.com/docs/profile-format/proxy/external-proxy/vmess/
- Quantumult X sample config: https://github.com/crossutility/Quantumult-X/blob/master/sample.conf
- Loon proxy examples: https://github.com/TiyNa/LoonManual/blob/main/Plus_EN/Proxy_EN.md
- Loon remote proxy group notes: https://github.com/TiyNa/LoonManual/blob/main/Plus_EN/Remote_Proxy_in_Proxy_Group_EN.md
