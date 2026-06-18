<div align="center">

# mihomo-proxy-manager

A friendly, lightweight, async-first upstream provider service for Clash/Mihomo subscriptions.

[![CI](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/ci.yml)
[![Docker](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/docker.yaml/badge.svg)](https://github.com/nerdneilsfield/mihomo-proxy-manager/actions/workflows/docker.yaml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](https://github.com/nerdneilsfield/mihomo-proxy-manager)
[![License](https://img.shields.io/github/license/nerdneilsfield/mihomo-proxy-manager?color=blue)](https://github.com/nerdneilsfield/mihomo-proxy-manager/blob/main/LICENSE)
[![Stars](https://img.shields.io/github/stars/nerdneilsfield/mihomo-proxy-manager?style=social)](https://github.com/nerdneilsfield/mihomo-proxy-manager/stargazers)

[中文](README.md) · [GitHub](https://github.com/nerdneilsfield/mihomo-proxy-manager) · [Issues](https://github.com/nerdneilsfield/mihomo-proxy-manager/issues)

</div>

## Overview

`mihomo-proxy-manager` is an upstream provider service for Clash/Mihomo proxy subscriptions. It downloads multiple upstream subscriptions, parses YAML, share-link, and base64 payloads, then filters, renames, caches, merges, and renders them as Mihomo-compatible `proxy-providers` YAML.

It is useful when you want to hide raw subscription URLs, normalize proxy node names, combine multiple providers, and expose separate provider routes for different devices or scenarios.

## Features

- Multi-source aggregation with route-level source selection.
- Mihomo provider YAML output with a top-level `proxies:` list.
- Parsers for Clash/Mihomo YAML, `ss://`, `vmess://`, `vless://`, `trojan://`, `hysteria2://`, and base64 subscriptions.
- Source-level and route-level filtering, type filtering, and rename templates.
- Safer HTTP fetching with redirect limits, response size limits, private-network checks, and no shared Cookie jar.
- Conditional requests with ETag and Last-Modified support.
- Source-level JSON cache with atomic writes and default `0600` permissions.
- Scheduled refreshes with interval, cron, startup refresh, and jitter support.
- HTTP Action plugin support for the `before_fetch` hook.
- CI/CD with Python 3.12, 3.13, 3.14 test coverage and multi-arch Docker image publishing.

## Quick Start

### With pip

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

Validate a config:

```bash
mpm check -c examples/config.toml
```

Run the service:

```bash
mpm serve -c examples/config.toml
```

Refresh one source manually:

```bash
mpm refresh -c examples/config.toml airport_a
```

### With Docker

Images are published by GitHub Actions to:

```text
ghcr.io/nerdneislfield/mihomo-proxy-manager
docker.io/${DOCKER_USERNAME}/mihomo-proxy-manager
```

Mount your own config file, cache directory, and logs directory:

```bash
docker run --rm \
  -p 8080:8080 \
  -v "$PWD/examples/config.toml:/app/config.toml:ro" \
  -v "$PWD/data:/app/data" \
  -v "$PWD/logs:/app/logs" \
  ghcr.io/nerdneislfield/mihomo-proxy-manager:latest
```

## Configuration

```toml
[server]
host = "127.0.0.1"
port = 8080
health_path = "/healthz"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"
format = "auto"
parse_error = "skip"

[sources.airport_a.refresh]
interval = "1h"

[sources.airport_a.rename]
prefix = "[{source}] "

[routes.phone]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
sources = ["airport_a"]
require_all_sources = false
```

The provider route returns:

```yaml
proxies:
  - name: "[airport_a] HK 01"
    type: vmess
    server: example.com
    port: 443
```

See [examples/config.toml](examples/config.toml) for a complete configuration.

## Security Notes

- Provider paths are bearer secrets. Use high-entropy random paths.
- Put the service behind HTTPS or a trusted reverse proxy in production.
- Treat upstream URLs, plugin headers, and proxy credentials as sensitive data.
- Private-network URLs are blocked by default. Only allow them when you understand the risk.
- Cache files contain proxy credentials. Protect the runtime directory permissions.

## Development

```bash
make install
make all
```

Useful targets:

```bash
make test
make lint
make typecheck
make check
```

The pip-based CI path is:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m ruff check .
python -m ty check
python -m pytest -q
```

## License

Released under the license declared in [LICENSE](LICENSE).
