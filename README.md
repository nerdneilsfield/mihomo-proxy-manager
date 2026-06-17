# mihomo-proxy-manager

Async upstream provider service for aggregating Clash/Mihomo subscriptions.

## What it does

- Downloads configured upstream subscriptions.
- Parses Clash/Mihomo YAML and common share-link subscriptions.
- Applies source-level and route-level filtering and renaming.
- Caches source-level parsed proxies to JSON files.
- Exposes hidden provider payload URLs for Mihomo `proxy-providers`.

## Commands

```bash
mpm check -c examples/config.toml
mpm serve -c examples/config.toml
mpm refresh -c examples/config.toml airport_a
```

## Provider output

Configured provider routes return:

```yaml
proxies:
  - name: "[airport_a] HK 01"
    type: vmess
    server: example.com
    port: 443
```

## Security notes

Provider paths are bearer secrets. Use high-entropy route paths, serve over TLS in production, and rotate paths if leaked.
