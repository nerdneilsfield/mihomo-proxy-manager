# DNS Resolution and User-Agent Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in DNS resolution for source proxy nodes and route-level `User-Agent` access control while preserving existing default behavior.

**Architecture:** Extend config dataclasses first, then add a small route access helper, then build DNS primitives in isolated modules before integrating them into `SourceRefresher`. DNS-enabled sources skip conditional fetch validators, run DNS after source transform, cache resolved proxy records, preserve existing host metadata, and expose warnings through the existing cache/result flow.

**Tech Stack:** Python 3.11+, Starlette, httpx2, pytest, pytest-asyncio, PyYAML, internal async DNS wire/client modules.

---

## File Structure

- Modify `src/mihomo_proxy_manager/models.py`: add `DnsConfig`, `SourceDnsConfig`, `RouteAccessConfig`; add fields to `AppConfig`, `SourceConfig`, and `RouteConfig`.
- Modify `src/mihomo_proxy_manager/config.py`: parse `[dns]`, `[sources.<name>.dns]`, `[routes.<name>.access]`; validate DNS endpoints, DNS failure enum, and route access patterns.
- Modify `src/mihomo_proxy_manager/app.py`: check route access `User-Agent` immediately after provider route lookup.
- Create `src/mihomo_proxy_manager/access.py`: route `User-Agent` matching and sanitized logging helper.
- Create `src/mihomo_proxy_manager/dns.py`: DNS endpoint parsing, DNS message encode/decode, DNS client transports, and proxy-record resolver.
- Modify `src/mihomo_proxy_manager/refresher.py`: inject optional DNS resolver, skip conditional validators for DNS-enabled sources, append DNS warnings, fail if DNS drop removes all nodes.
- Modify `src/mihomo_proxy_manager/cli.py`: construct DNS resolver and pass it to `SourceRefresher`.
- Modify `tests/test_config.py`: config parsing and validation tests.
- Modify `tests/test_app.py`: route access tests.
- Create `tests/test_dns.py`: DNS endpoint, wire codec, DNS client, and resolver tests.
- Create `tests/test_refresher_dns.py`: refresher integration tests.
- Modify `README.md`, `README_EN.md`, and `config.toml`: document and example the new opt-in config.

## Task 1: Config Models and Validation

**Files:**
- Modify: `src/mihomo_proxy_manager/models.py`
- Modify: `src/mihomo_proxy_manager/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Append tests to `tests/test_config.py`:

```python
def test_dns_config_defaults_and_source_overrides(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://1.1.1.1:53", "https://dns.google/dns-query"]
timeout = "5s"
failure = "keep"

[sources.airport_a.dns]
enabled = true
servers = ["tls://1.1.1.1:853?servername=cloudflare-dns.com"]
timeout = "3s"
failure = "drop"

[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*"]
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.dns.servers == ("udp://1.1.1.1:53", "https://dns.google/dns-query")
    assert config.dns.timeout.total_seconds() == 5
    assert config.dns.failure == "keep"
    assert config.sources["airport_a"].dns.enabled is True
    assert config.sources["airport_a"].dns.servers == (
        "tls://1.1.1.1:853?servername=cloudflare-dns.com",
    )
    assert config.sources["airport_a"].dns.timeout.total_seconds() == 3
    assert config.sources["airport_a"].dns.failure == "drop"
    assert config.routes["phone"].access.user_agent == ("mihomo/*", "clash-meta/*")


def test_source_dns_defaults_to_disabled_with_global_defaults(
    temp_config_path: Path,
) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["tcp://8.8.8.8:53"]
timeout = "4s"
failure = "fail"
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.sources["airport_a"].dns.enabled is False
    assert config.sources["airport_a"].dns.servers == ("tcp://8.8.8.8:53",)
    assert config.sources["airport_a"].dns.timeout.total_seconds() == 4
    assert config.sources["airport_a"].dns.failure == "fail"


def test_validation_rejects_invalid_dns_config(temp_config_path: Path) -> None:
    body = (
        minimal_config()
        + """
[dns]
servers = ["udp://127.0.0.1:53", "ftp://example.com/dns"]
failure = "explode"

[sources.airport_a.dns]
enabled = true
servers = []
failure = "panic"
"""
    )
    config = load_config(write_config(temp_config_path, body), validate=False)
    report = config.validate(config_path=temp_config_path)
    joined = "\n".join(report.errors)

    assert not report.ok
    assert "dns server resolves to non-public address" in joined
    assert "unsupported DNS server scheme" in joined
    assert "dns failure must be" in joined
    assert "source 'airport_a' dns servers must not be empty" in joined
    assert "source 'airport_a' dns failure must be" in joined


def test_route_access_empty_user_agent_list_keeps_route_open(
    temp_config_path: Path,
) -> None:
    body = (
        minimal_config()
        + """
[routes.phone.access]
user_agent = []
"""
    )

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["phone"].access.user_agent == ()
```

- [ ] **Step 2: Run config tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_config.py -q
```

Expected: FAIL with missing `dns`/`access` attributes or unsupported top-level table `"dns"`.

- [ ] **Step 3: Add dataclasses**

In `src/mihomo_proxy_manager/models.py`, add these config models with defaults so existing direct test constructors remain compatible:

```python
@dataclass(frozen=True)
class DnsConfig:
    """Global DNS resolution defaults."""

    servers: tuple[str, ...] = ("udp://1.1.1.1:53",)
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    failure: Literal["keep", "drop", "fail"] = "keep"


@dataclass(frozen=True)
class SourceDnsConfig:
    """Per-source DNS resolution behavior."""

    enabled: bool = False
    servers: tuple[str, ...] = ("udp://1.1.1.1:53",)
    timeout: timedelta = field(default_factory=lambda: timedelta(seconds=5))
    failure: Literal["keep", "drop", "fail"] = "keep"


@dataclass(frozen=True)
class RouteAccessConfig:
    """Route access control configuration."""

    user_agent: tuple[str, ...] = ()
```

Update existing dataclasses by adding these fields at the end of the existing class definitions. Keep defaulted fields at the end so dataclass field ordering stays valid:

```python
class SourceConfig:
    name: str
    url: str
    format: Literal["auto", "yaml", "share-links"]
    parse_error: Literal["skip", "fail"]
    fetch: FetchConfig
    refresh: RefreshConfig
    rename: RenameConfig
    filter: FilterConfig
    plugins: SourcePluginConfig
    dns: SourceDnsConfig = field(default_factory=SourceDnsConfig)


class RouteConfig:
    name: str
    path: str
    sources: tuple[str, ...]
    require_all_sources: bool
    output: RouteOutputConfig
    rename: RenameConfig
    filter: FilterConfig
    access: RouteAccessConfig = field(default_factory=RouteAccessConfig)


class AppConfig:
    server: ServerConfig
    cache: CacheConfig
    logging_console: LoggingSinkConfig
    logging_file: LoggingSinkConfig
    http: HttpConfig
    scheduler: SchedulerConfig
    security: SecurityConfig
    parser: ParserConfig
    output: OutputConfig
    sources: dict[str, SourceConfig]
    routes: dict[str, RouteConfig]
    plugins: dict[str, PluginConfig]
    dns: DnsConfig = field(default_factory=DnsConfig)
```

Before editing models, identify direct constructors that may be affected:

```bash
rtk grep -n "SourceConfig\\(|RouteConfig\\(|AppConfig\\(|LoadedConfig\\(" src tests
```

Expected current direct constructor sites include `tests/test_coverage_gaps.py`, `tests/test_logging.py`, `tests/test_plugins_refresher.py`, `tests/test_render.py`, and `tests/test_scheduler.py`. The default factories above should keep those call sites working; update them only if a test needs to assert DNS or access fields explicitly.

- [ ] **Step 4: Add config parsing and validation**

In `src/mihomo_proxy_manager/config.py`, import the new models and `urlparse`. Add helpers:

```python
DNS_FAILURES = {"keep", "drop", "fail"}
DNS_SCHEMES = {"udp", "tcp", "tls", "https"}


def _as_tuple(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _dns(data: dict[str, Any]) -> DnsConfig:
    return DnsConfig(
        servers=_as_tuple(data.get("servers"), default=("udp://1.1.1.1:53",)),
        timeout=parse_duration(data.get("timeout", "5s")),
        failure=data.get("failure", "keep"),
    )


def _source_dns(data: dict[str, Any], dns: DnsConfig) -> SourceDnsConfig:
    return SourceDnsConfig(
        enabled=bool(data.get("enabled", False)),
        servers=_as_tuple(data.get("servers"), default=dns.servers),
        timeout=parse_duration(
            data.get("timeout", f"{int(dns.timeout.total_seconds())}s")
        ),
        failure=data.get("failure", dns.failure),
    )


def _route_access(data: dict[str, Any]) -> RouteAccessConfig:
    return RouteAccessConfig(user_agent=_as_tuple(data.get("user_agent")))
```

Add endpoint validation helper:

```python
def _validate_dns_servers(
    servers: tuple[str, ...],
    *,
    label: str,
    allow_private_network: bool,
) -> list[str]:
    errors: list[str] = []
    if not servers:
        return [f"{label} dns servers must not be empty"]
    for server in servers:
        parsed = urlparse(server)
        if parsed.scheme not in DNS_SCHEMES:
            errors.append(f"{label} unsupported DNS server scheme: {parsed.scheme!r}")
            continue
        if not parsed.hostname:
            errors.append(f"{label} DNS server host is required: {server!r}")
            continue
        if parsed.scheme in {"udp", "tcp", "tls"} and parsed.port is None:
            errors.append(f"{label} DNS server port is required: {server!r}")
        if parsed.scheme == "https":
            try:
                assert_safe_url(
                    server,
                    allow_private_network=allow_private_network,
                    resolve_dns=False,
                )
            except SecurityError as exc:
                errors.append(f"{label} DNS server is unsafe: {exc}")
        else:
            # Reuse static URL host checks by temporarily validating as https.
            static_url = f"https://{parsed.hostname}/"
            try:
                assert_safe_url(
                    static_url,
                    allow_private_network=allow_private_network,
                    resolve_dns=False,
                )
            except SecurityError as exc:
                errors.append(f"{label} dns server resolves to non-public address: {exc}")
    return errors
```

Update `allowed_top_level` to include `"dns"`, parse `dns_raw = _table(raw, "dns")`, construct `dns = _dns(dns_raw)`, pass it into source parsing, parse route access, and pass `dns=dns` into `LoadedConfig`.

Use these exact constructor additions in `load_config()`:

```python
dns_raw = _table(raw, "dns")
dns = _dns(dns_raw)
```

```python
sources[name] = SourceConfig(
    name=name,
    url=values.get("url", ""),
    format=values.get("format", parser.default_format),
    parse_error=values.get("parse_error", parser.default_parse_error),
    fetch=source_fetch,
    refresh=_refresh(_table(values, "refresh")),
    rename=_rename(_table(values, "rename")),
    filter=_filter(_table(values, "filter")),
    plugins=_source_plugins(_table(values, "plugins")),
    dns=_source_dns(_table(values, "dns"), dns),
)
```

```python
routes[name] = RouteConfig(
    name=name,
    path=values.get("path", ""),
    sources=tuple(values.get("sources", ())),
    require_all_sources=bool(values.get("require_all_sources", False)),
    output=RouteOutputConfig(
        format=output_values.get("format", "provider"),
        include_meta_comments=bool(
            output_values.get(
                "include_meta_comments", output.default_include_meta_comments
            )
        ),
    ),
    rename=_rename(_table(values, "rename")),
    filter=_filter(_table(values, "filter")),
    access=_route_access(_table(values, "access")),
)
```

```python
config = LoadedConfig(
    server=server,
    cache=cache,
    logging_console=logging_console,
    logging_file=logging_file,
    http=http,
    scheduler=scheduler,
    security=security,
    parser=parser,
    output=output,
    sources=sources,
    routes=routes,
    plugins=plugins,
    dns=dns,
)
```

In `LoadedConfig.validate()`, add:

```python
if self.dns.failure not in DNS_FAILURES:
    errors.append("dns failure must be 'keep', 'drop', or 'fail'")
errors.extend(
    _validate_dns_servers(
        self.dns.servers,
        label="global",
        allow_private_network=self.security.allow_private_network_urls,
    )
)
...
if source.dns.failure not in DNS_FAILURES:
    errors.append(
        f"source {source.name!r} dns failure must be 'keep', 'drop', or 'fail'"
    )
errors.extend(
    _validate_dns_servers(
        source.dns.servers,
        label=f"source {source.name!r}",
        allow_private_network=self.security.allow_private_network_urls,
    )
)
```

- [ ] **Step 5: Run config tests to verify GREEN**

Run:

```bash
rtk uv run pytest tests/test_config.py tests/test_logging.py tests/test_scheduler.py tests/test_render.py tests/test_plugins_refresher.py tests/test_coverage_gaps.py -q
```

Expected: PASS. This verifies both new config behavior and compatibility for existing direct dataclass constructors.

- [ ] **Step 6: Commit**

```bash
rtk git add src/mihomo_proxy_manager/models.py src/mihomo_proxy_manager/config.py tests/test_config.py
rtk git commit -m "feat(config): add dns and route access config"
```

## Task 2: Route User-Agent Access Control

**Files:**
- Create: `src/mihomo_proxy_manager/access.py`
- Modify: `src/mihomo_proxy_manager/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing route access tests**

Append to `tests/test_app.py`:

```python
class ExplodingCacheStore:
    async def get(self, source_name: str):
        raise AssertionError("cache must not be read")

    def set_refreshing(self, source_name: str, refreshing: bool) -> None:
        raise AssertionError("refresh state must not change")

    def cache_path(self, source_name: str) -> str | None:
        return None


def access_config_file(tmp_path):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + """
[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*"]
""",
        encoding="utf-8",
    )
    return path


def test_provider_forbids_missing_user_agent_before_cache_read(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": ""},
        )

    assert response.status_code == 403


def test_provider_forbids_non_matching_user_agent_before_cache_read(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": "Mihomo/1.19.5"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_provider_allows_matching_user_agent(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    store = JsonSourceCacheStore(config.cache)
    await store.set(
        "airport_a",
        SourceCache(
            "airport_a",
            1,
            datetime.now(UTC),
            datetime.now(UTC),
            None,
            None,
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "HK", "type": "vmess"}),),
        ),
    )
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(
            "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
            headers={"User-Agent": "mihomo/1.19.5"},
        )

    assert response.status_code == 200
    assert "proxies:" in response.text


def test_health_ignores_route_user_agent_access(tmp_path) -> None:
    config = load_config(access_config_file(tmp_path))
    app = create_app(
        config,
        cache_store=JsonSourceCacheStore(config.cache),
        refresher=None,
        scheduler=None,
    )

    with TestClient(app) as client:
        response = client.get("/healthz", headers={"User-Agent": ""})

    assert response.status_code == 200
```

- [ ] **Step 2: Run app tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_app.py -q
```

Expected: FAIL because route access is not enforced.

- [ ] **Step 3: Implement access helper**

Create `src/mihomo_proxy_manager/access.py`:

```python
"""Route access-control helpers."""

from __future__ import annotations

import fnmatch
import re

from .models import RouteAccessConfig

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


def user_agent_allowed(config: RouteAccessConfig, user_agent: str | None) -> bool:
    if not config.user_agent:
        return True
    if not user_agent:
        return False
    return any(fnmatch.fnmatchcase(user_agent, pattern) for pattern in config.user_agent)


def sanitize_user_agent(value: str | None, *, limit: int = 200) -> str:
    if not value:
        return "<missing>"
    sanitized = _CONTROL_CHARS_RE.sub(" ", value).strip()
    if len(sanitized) > limit:
        return sanitized[:limit] + "...<truncated>"
    return sanitized
```

- [ ] **Step 4: Enforce access in provider handler**

In `src/mihomo_proxy_manager/app.py`, import helpers:

```python
from .access import sanitize_user_agent, user_agent_allowed
```

In `provider()`, immediately after route lookup succeeds and before `records = []`, add:

```python
        request_user_agent = request.headers.get("user-agent")
        if not user_agent_allowed(route.access, request_user_agent):
            logger.info(
                "provider forbidden: route={route} user_agent={user_agent}",
                route=route.name,
                user_agent=sanitize_user_agent(request_user_agent),
            )
            return PlainTextResponse("forbidden", status_code=403)
```

- [ ] **Step 5: Run app tests to verify GREEN**

Run:

```bash
rtk uv run pytest tests/test_app.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
rtk git add src/mihomo_proxy_manager/access.py src/mihomo_proxy_manager/app.py tests/test_app.py
rtk git commit -m "feat(app): restrict provider routes by user agent"
```

## Task 3: DNS Endpoint Parsing, Security, and Wire Codec

**Files:**
- Create: `src/mihomo_proxy_manager/dns.py`
- Test: `tests/test_dns.py`

- [ ] **Step 1: Write failing DNS endpoint and codec tests**

Create `tests/test_dns.py` with:

```python
from datetime import timedelta

import pytest

from mihomo_proxy_manager.dns import (
    DnsEndpoint,
    DnsMessageError,
    build_query,
    decode_addresses,
    parse_dns_endpoint,
    validate_dns_endpoint_static,
)


def test_parse_tls_endpoint_with_certificate_servername() -> None:
    endpoint = parse_dns_endpoint("tls://1.1.1.1:853?servername=cloudflare-dns.com")

    assert endpoint == DnsEndpoint(
        scheme="tls",
        host="1.1.1.1",
        port=853,
        path="",
        servername="cloudflare-dns.com",
    )


def test_static_validation_rejects_private_dns_server() -> None:
    endpoint = parse_dns_endpoint("udp://127.0.0.1:53")

    with pytest.raises(ValueError, match="non-public"):
        validate_dns_endpoint_static(endpoint, allow_private_network=False)


def test_build_query_encodes_a_question() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)

    assert query[:2] == b"\x12\x34"
    assert b"\x07example\x03com\x00" in query
    assert query.endswith(b"\x00\x01\x00\x01")


def test_decode_a_response() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)
    response = (
        query[:2]
        + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        + query[12:]
        + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x5d\xb8\xd8\x22"
    )

    assert decode_addresses(response, "example.com", "A", transaction_id=0x1234) == [
        "93.184.216.34"
    ]


def test_decode_rejects_mismatched_transaction_id() -> None:
    query = build_query("example.com", "A", transaction_id=0x1234)
    response = b"\x99\x99" + query[2:]

    with pytest.raises(DnsMessageError, match="transaction"):
        decode_addresses(response, "example.com", "A", transaction_id=0x1234)
```

- [ ] **Step 2: Run DNS tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: FAIL because `mihomo_proxy_manager.dns` does not exist.

- [ ] **Step 3: Implement endpoint parser and wire codec**

Create `src/mihomo_proxy_manager/dns.py` with these initial pieces:

```python
"""DNS endpoint parsing, wire codec, clients, and proxy node resolution."""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .security import SecurityError, assert_safe_url

QTYPE = {"A": 1, "AAAA": 28}
RTYPE = {1: "A", 28: "AAAA", 5: "CNAME"}


class DnsMessageError(ValueError):
    """Raised for malformed or unusable DNS messages."""


@dataclass(frozen=True)
class DnsEndpoint:
    scheme: str
    host: str
    port: int
    path: str
    servername: str | None = None


def parse_dns_endpoint(value: str) -> DnsEndpoint:
    parsed = urlparse(value)
    if parsed.scheme not in {"udp", "tcp", "tls", "https"}:
        raise ValueError(f"unsupported DNS server scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("DNS server host is required")
    if parsed.scheme == "https":
        port = parsed.port or 443
        path = parsed.path or "/dns-query"
    else:
        if parsed.port is None:
            raise ValueError("DNS server port is required")
        port = parsed.port
        path = ""
    query = parse_qs(parsed.query)
    servername = query.get("servername", [None])[-1]
    return DnsEndpoint(parsed.scheme, parsed.hostname, port, path, servername)


def validate_dns_endpoint_static(
    endpoint: DnsEndpoint, *, allow_private_network: bool
) -> None:
    if endpoint.scheme == "https":
        assert_safe_url(
            f"https://{endpoint.host}:{endpoint.port}{endpoint.path}",
            allow_private_network=allow_private_network,
            resolve_dns=False,
        )
        return
    try:
        assert_safe_url(
            f"https://{endpoint.host}/",
            allow_private_network=allow_private_network,
            resolve_dns=False,
        )
    except SecurityError as exc:
        raise ValueError(f"DNS server resolves to non-public address: {exc}") from exc


def _encode_name(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    if not labels or any(not label for label in labels):
        raise DnsMessageError("invalid domain name")
    output = bytearray()
    for label in labels:
        encoded = label.encode("idna")
        if len(encoded) > 63:
            raise DnsMessageError("domain label too long")
        output.append(len(encoded))
        output.extend(encoded)
    output.append(0)
    return bytes(output)


def build_query(name: str, qtype: str, *, transaction_id: int) -> bytes:
    question = _encode_name(name) + struct.pack("!HH", QTYPE[qtype], 1)
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    return header + question
```

Then add `decode_addresses()` with name decompression:

```python
def _read_name(message: bytes, offset: int, *, depth: int = 0) -> tuple[str, int]:
    if depth > 20:
        raise DnsMessageError("too many compression pointers")
    labels: list[str] = []
    original_offset = offset
    jumped = False
    while True:
        if offset >= len(message):
            raise DnsMessageError("name exceeds message")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise DnsMessageError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            pointed, _ = _read_name(message, pointer, depth=depth + 1)
            labels.append(pointed)
            offset += 2
            jumped = True
            break
        if length == 0:
            offset += 1
            break
        offset += 1
        label = message[offset : offset + length]
        if len(label) != length:
            raise DnsMessageError("truncated label")
        labels.append(label.decode("idna"))
        offset += length
    name = ".".join(item for item in labels if item)
    return name, offset
```

```python
def decode_addresses(
    message: bytes, name: str, qtype: str, *, transaction_id: int
) -> list[str]:
    if len(message) < 12:
        raise DnsMessageError("truncated DNS response")
    (
        response_id,
        flags,
        qdcount,
        ancount,
        _nscount,
        _arcount,
    ) = struct.unpack("!HHHHHH", message[:12])
    if response_id != transaction_id:
        raise DnsMessageError("transaction id mismatch")
    if flags & 0x0200:
        raise DnsMessageError("truncated DNS response")
    rcode = flags & 0x000F
    if rcode:
        raise DnsMessageError(f"dns rcode {rcode}")
    offset = 12
    for _ in range(qdcount):
        qname, offset = _read_name(message, offset)
        if offset + 4 > len(message):
            raise DnsMessageError("truncated question")
        question_type, question_class = struct.unpack("!HH", message[offset : offset + 4])
        offset += 4
        if qname.rstrip(".").lower() != name.rstrip(".").lower():
            raise DnsMessageError("question name mismatch")
        if question_type != QTYPE[qtype] or question_class != 1:
            raise DnsMessageError("question type mismatch")
    addresses: list[str] = []
    for _ in range(ancount):
        _answer_name, offset = _read_name(message, offset)
        if offset + 10 > len(message):
            raise DnsMessageError("truncated answer")
        answer_type, answer_class, _ttl, rdlength = struct.unpack(
            "!HHIH", message[offset : offset + 10]
        )
        offset += 10
        rdata = message[offset : offset + rdlength]
        if len(rdata) != rdlength:
            raise DnsMessageError("truncated rdata")
        offset += rdlength
        if answer_class != 1:
            continue
        if answer_type == 1 and qtype == "A" and rdlength == 4:
            addresses.append(str(ipaddress.IPv4Address(rdata)))
        elif answer_type == 28 and qtype == "AAAA" and rdlength == 16:
            addresses.append(str(ipaddress.IPv6Address(rdata)))
    if not addresses:
        raise DnsMessageError("no usable addresses")
    return addresses
```

- [ ] **Step 4: Run DNS tests to verify GREEN**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/mihomo_proxy_manager/dns.py tests/test_dns.py
rtk git commit -m "feat(dns): add endpoint parsing and wire codec"
```

## Task 4: DNS Client Transports

**Files:**
- Modify: `src/mihomo_proxy_manager/dns.py`
- Test: `tests/test_dns.py`

- [ ] **Step 1: Write failing DNS client tests**

Append to `tests/test_dns.py`:

```python
import httpx2 as httpx

from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.models import HttpConfig


@pytest.mark.asyncio
async def test_doh_client_uses_safe_http_post() -> None:
    requests: list[httpx.Request] = []
    query = build_query("example.com", "A", transaction_id=0x1234)
    response_message = (
        query[:2]
        + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
        + query[12:]
        + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x5d\xb8\xd8\x22"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/dns-message"
        return httpx.Response(200, content=response_message)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    safe_http = SafeHttpClient(
        http_client,
        HttpConfig(timedelta(seconds=30), "mihomo/1.19.5", 4096, 3),
    )
    client = DnsClient(safe_http=safe_http)

    result = await client.query(
        DnsEndpoint("https", "dns.example.com", 443, "/dns-query"),
        "example.com",
        "A",
        timeout=timedelta(seconds=5),
        allow_private_network=False,
        transaction_id=0x1234,
    )

    assert result == ["93.184.216.34"]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_dns_client_rejects_oversized_doh_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4097)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    safe_http = SafeHttpClient(
        http_client,
        HttpConfig(timedelta(seconds=30), "mihomo/1.19.5", 8192, 3),
    )
    client = DnsClient(safe_http=safe_http)

    with pytest.raises(DnsMessageError, match="too large"):
        await client.query(
            DnsEndpoint("https", "dns.example.com", 443, "/dns-query"),
            "example.com",
            "A",
            timeout=timedelta(seconds=5),
            allow_private_network=False,
            transaction_id=0x1234,
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: FAIL because `DnsClient` does not exist.

- [ ] **Step 3: Implement DoH and transport skeleton**

In `src/mihomo_proxy_manager/dns.py`, add:

```python
import asyncio
import random
import ssl

from .fetcher import SafeHttpClient

DNS_UDP_MAX_SIZE = 512
DNS_MESSAGE_MAX_SIZE = 4096
```

Add runtime host safety:

```python
async def validate_dns_endpoint_runtime(
    endpoint: DnsEndpoint, *, allow_private_network: bool
) -> None:
    validate_dns_endpoint_static(endpoint, allow_private_network=allow_private_network)
    if allow_private_network:
        return
    infos = await asyncio.to_thread(socket.getaddrinfo, endpoint.host, endpoint.port)
    for info in infos:
        ip = info[4][0]
        try:
            assert_safe_url(
                f"https://{ip}/",
                allow_private_network=False,
                resolve_dns=False,
            )
        except SecurityError as exc:
            raise ValueError(f"DNS server resolves to non-public address: {ip}") from exc
```

Add client:

```python
class DnsClient:
    def __init__(self, *, safe_http: SafeHttpClient) -> None:
        self.safe_http = safe_http

    async def query(
        self,
        endpoint: DnsEndpoint,
        name: str,
        qtype: str,
        *,
        timeout: timedelta,
        allow_private_network: bool,
        transaction_id: int | None = None,
    ) -> list[str]:
        await validate_dns_endpoint_runtime(
            endpoint, allow_private_network=allow_private_network
        )
        tid = transaction_id if transaction_id is not None else random.randrange(0, 65536)
        query = build_query(name, qtype, transaction_id=tid)
        if endpoint.scheme == "https":
            response = await self._query_https(
                endpoint,
                query,
                timeout,
                allow_private_network=allow_private_network,
            )
        elif endpoint.scheme == "udp":
            response = await self._query_udp(endpoint, query, timeout)
        elif endpoint.scheme == "tcp":
            response = await self._query_tcp(endpoint, query, timeout, tls=False)
        elif endpoint.scheme == "tls":
            response = await self._query_tcp(endpoint, query, timeout, tls=True)
        else:
            raise ValueError(f"unsupported DNS server scheme: {endpoint.scheme!r}")
        return decode_addresses(response, name, qtype, transaction_id=tid)

    async def _query_https(
        self,
        endpoint: DnsEndpoint,
        query: bytes,
        timeout: timedelta,
        *,
        allow_private_network: bool,
    ) -> bytes:
        response = await self.safe_http.request(
            "POST",
            f"https://{endpoint.host}:{endpoint.port}{endpoint.path}",
            headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"},
            timeout=timeout.total_seconds(),
            allow_private_network=allow_private_network,
            body=query,
        )
        response.raise_for_status()
        if len(response.content) > DNS_MESSAGE_MAX_SIZE:
            raise DnsMessageError("DNS response too large")
        return response.content
```

Implement UDP/TCP/TLS with asyncio APIs:

```python
    async def _query_udp(
        self, endpoint: DnsEndpoint, query: bytes, timeout: timedelta
    ) -> bytes:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _DnsDatagramProtocol(query),
            remote_addr=(endpoint.host, endpoint.port),
        )
        try:
            return await asyncio.wait_for(protocol.response, timeout.total_seconds())
        finally:
            transport.close()

    async def _query_tcp(
        self,
        endpoint: DnsEndpoint,
        query: bytes,
        timeout: timedelta,
        *,
        tls: bool,
    ) -> bytes:
        ssl_context = None
        server_hostname = None
        if tls:
            ssl_context = ssl.create_default_context()
            server_hostname = endpoint.servername or endpoint.host
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                endpoint.host,
                endpoint.port,
                ssl=ssl_context,
                server_hostname=server_hostname,
            ),
            timeout.total_seconds(),
        )
        try:
            writer.write(struct.pack("!H", len(query)) + query)
            await asyncio.wait_for(writer.drain(), timeout.total_seconds())
            size_bytes = await asyncio.wait_for(reader.readexactly(2), timeout.total_seconds())
            size = struct.unpack("!H", size_bytes)[0]
            if size > DNS_MESSAGE_MAX_SIZE:
                raise DnsMessageError("DNS response too large")
            return await asyncio.wait_for(reader.readexactly(size), timeout.total_seconds())
        finally:
            writer.close()
            await writer.wait_closed()
```

Add datagram protocol:

```python
class _DnsDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, query: bytes) -> None:
        self.query = query
        self.response: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.sendto(self.query)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: object) -> None:
        if len(data) > DNS_UDP_MAX_SIZE:
            self.response.set_exception(DnsMessageError("DNS response too large"))
        elif not self.response.done():
            self.response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.response.done():
            self.response.set_exception(exc)
```

- [ ] **Step 4: Run DNS tests**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/mihomo_proxy_manager/dns.py tests/test_dns.py
rtk git commit -m "feat(dns): add safe dns client transports"
```

## Task 5: Proxy Record DNS Resolver

**Files:**
- Modify: `src/mihomo_proxy_manager/dns.py`
- Test: `tests/test_dns.py`

- [ ] **Step 1: Write failing resolver tests**

Append to `tests/test_dns.py`:

```python
from mihomo_proxy_manager.models import ProxyRecord, SourceDnsConfig


class FakeDnsClient:
    def __init__(self, responses: dict[tuple[str, str, str], list[str] | Exception]):
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

    async def query(
        self,
        endpoint: DnsEndpoint,
        name: str,
        qtype: str,
        *,
        timeout: timedelta,
        allow_private_network: bool,
        transaction_id: int | None = None,
    ) -> list[str]:
        key = (endpoint.scheme, name, qtype)
        self.calls.append(key)
        result = self.responses.get(key, DnsMessageError("no answer"))
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_resolver_rewrites_server_and_preserves_existing_servername() -> None:
    client = FakeDnsClient({("udp", "example.com", "A"): ["93.184.216.34"]})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [
        ProxyRecord(
            "airport_a",
            {
                "name": "HK",
                "type": "vmess",
                "server": "example.com",
                "tls": True,
                "servername": "custom.example.com",
            },
        )
    ]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    resolved, warnings = await resolver.resolve_records(records, config, source="airport_a")

    assert warnings == []
    assert resolved[0].data["server"] == "93.184.216.34"
    assert resolved[0].data["servername"] == "custom.example.com"
    assert records[0].data["server"] == "example.com"


@pytest.mark.asyncio
async def test_resolver_fills_missing_tls_servername_and_ws_host() -> None:
    client = FakeDnsClient({("udp", "example.com", "A"): ["93.184.216.34"]})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [
        ProxyRecord(
            "airport_a",
            {
                "name": "HK",
                "type": "vmess",
                "server": "example.com",
                "tls": True,
                "network": "ws",
                "ws-opts": {"path": "/ws"},
            },
        )
    ]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "keep")

    resolved, warnings = await resolver.resolve_records(records, config, source="airport_a")

    data = resolved[0].data
    assert warnings == []
    assert data["server"] == "93.184.216.34"
    assert data["servername"] == "example.com"
    assert data["ws-opts"]["headers"]["Host"] == "example.com"


@pytest.mark.asyncio
async def test_resolver_failover_uses_second_server() -> None:
    client = FakeDnsClient(
        {
            ("udp", "example.com", "A"): DnsMessageError("first failed"),
            ("tcp", "example.com", "A"): ["93.184.216.34"],
        }
    )
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(
        True,
        ("udp://1.1.1.1:53", "tcp://8.8.8.8:53"),
        timedelta(seconds=5),
        "keep",
    )

    resolved, warnings = await resolver.resolve_records(records, config, source="airport_a")

    assert warnings == []
    assert resolved[0].data["server"] == "93.184.216.34"
    assert client.calls == [
        ("udp", "example.com", "A"),
        ("udp", "example.com", "AAAA"),
        ("tcp", "example.com", "A"),
    ]


@pytest.mark.asyncio
async def test_resolver_drop_policy_removes_failed_records() -> None:
    client = FakeDnsClient({})
    resolver = DnsResolver(client=client, allow_private_network=False)
    records = [ProxyRecord("airport_a", {"name": "HK", "server": "example.com"})]
    config = SourceDnsConfig(True, ("udp://1.1.1.1:53",), timedelta(seconds=5), "drop")

    resolved, warnings = await resolver.resolve_records(records, config, source="airport_a")

    assert resolved == []
    assert len(warnings) == 1
    assert "HK" in warnings[0]
```

- [ ] **Step 2: Run resolver tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: FAIL because `DnsResolver` is missing.

- [ ] **Step 3: Implement `DnsResolver`**

In `src/mihomo_proxy_manager/dns.py`, add:

```python
from copy import deepcopy
from typing import Protocol

from .models import ProxyRecord, SourceDnsConfig
from .security import redact_secret

DNS_WARNING_LIMIT = 100
DNS_NODE_CONCURRENCY = 16


class DnsClientProtocol(Protocol):
    async def query(
        self,
        endpoint: DnsEndpoint,
        name: str,
        qtype: str,
        *,
        timeout: timedelta,
        allow_private_network: bool,
        transaction_id: int | None = None,
    ) -> list[str]:
        raise NotImplementedError
```

Add helpers:

```python
def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_tls_like(data: dict[str, object]) -> bool:
    return data.get("tls") is True or data.get("security") in {"tls", "reality"}


def _is_ws(data: dict[str, object]) -> bool:
    return data.get("network") == "ws"


def _preserve_host_metadata(data: dict[str, object], original_host: str) -> None:
    if _is_tls_like(data) and "servername" not in data and "sni" not in data:
        data["servername"] = original_host
    if _is_ws(data):
        ws_opts = data.get("ws-opts")
        if not isinstance(ws_opts, dict):
            ws_opts = {}
            data["ws-opts"] = ws_opts
        headers = ws_opts.get("headers")
        if not isinstance(headers, dict):
            headers = {}
            ws_opts["headers"] = headers
        headers.setdefault("Host", original_host)
```

Add resolver:

```python
class DnsResolver:
    def __init__(self, *, client: DnsClientProtocol, allow_private_network: bool) -> None:
        self.client = client
        self.allow_private_network = allow_private_network

    async def resolve_records(
        self,
        records: list[ProxyRecord],
        config: SourceDnsConfig,
        *,
        source: str,
    ) -> tuple[list[ProxyRecord], list[str]]:
        if not config.enabled:
            return records, []
        endpoints = [parse_dns_endpoint(value) for value in config.servers]
        semaphore = asyncio.Semaphore(DNS_NODE_CONCURRENCY)
        warnings: list[str] = []

        async def resolve_one(record: ProxyRecord) -> ProxyRecord | None:
            async with semaphore:
                return await self._resolve_one(record, config, endpoints, source, warnings)

        resolved = await asyncio.gather(*(resolve_one(record) for record in records))
        kept = [record for record in resolved if record is not None]
        if len(warnings) > DNS_WARNING_LIMIT:
            omitted = len(warnings) - DNS_WARNING_LIMIT
            warnings = warnings[:DNS_WARNING_LIMIT] + [
                f"dns warning limit reached for source {source!r}; omitted {omitted} warnings"
            ]
        return kept, warnings
```

```python
    async def _resolve_one(
        self,
        record: ProxyRecord,
        config: SourceDnsConfig,
        endpoints: list[DnsEndpoint],
        source: str,
        warnings: list[str],
    ) -> ProxyRecord | None:
        server = record.data.get("server")
        if not isinstance(server, str) or not server or _is_ip_literal(server):
            return record
        last_error = "no DNS server returned an address"
        for endpoint in endpoints:
            for qtype in ("A", "AAAA"):
                try:
                    addresses = await self.client.query(
                        endpoint,
                        server,
                        qtype,
                        timeout=config.timeout,
                        allow_private_network=self.allow_private_network,
                    )
                except Exception as exc:
                    last_error = redact_secret(str(exc))[:200]
                    continue
                if addresses:
                    data = deepcopy(record.data)
                    _preserve_host_metadata(data, server)
                    data["server"] = addresses[0]
                    return ProxyRecord(record.source, data)
        proxy_name = str(record.data.get("name", "<unnamed>"))[:120]
        warning = (
            f"dns resolution failed: source={source!r} proxy={proxy_name!r} "
            f"server={server!r} error={last_error}"
        )
        if config.failure == "keep":
            warnings.append(warning)
            return record
        if config.failure == "drop":
            warnings.append(warning)
            return None
        raise RuntimeError(warning)
```

- [ ] **Step 4: Run DNS tests**

Run:

```bash
rtk uv run pytest tests/test_dns.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add src/mihomo_proxy_manager/dns.py tests/test_dns.py
rtk git commit -m "feat(dns): resolve proxy node hosts"
```

## Task 6: Refresher DNS Integration

**Files:**
- Modify: `src/mihomo_proxy_manager/refresher.py`
- Modify: `src/mihomo_proxy_manager/cli.py`
- Create: `tests/test_refresher_dns.py`

- [ ] **Step 1: Write failing refresher integration tests**

Create `tests/test_refresher_dns.py`:

```python
from datetime import timedelta

import pytest

from mihomo_proxy_manager.cache import JsonSourceCacheStore
from mihomo_proxy_manager.fetcher import FetchResult
from mihomo_proxy_manager.models import (
    CacheConfig,
    FetchConfig,
    FilterConfig,
    ProxyRecord,
    RefreshConfig,
    RenameConfig,
    SourceConfig,
    SourceDnsConfig,
    SourcePluginConfig,
)
from mihomo_proxy_manager.refresher import SourceRefresher


class FakeFetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str | None, str | None]] = []

    async def fetch(self, url, fetch_config, *, etag=None, last_modified=None):
        self.calls.append((etag, last_modified))
        return FetchResult(self.body, '"new"', "Fri, 19 Jun 2026 00:00:00 GMT")


class FakeResolver:
    def __init__(self, records: list[ProxyRecord], warnings: list[str] | None = None):
        self.records = records
        self.warnings = warnings or []
        self.calls = 0

    async def resolve_records(self, records, config, *, source):
        self.calls += 1
        return self.records, self.warnings


def source_config(*, dns_enabled: bool) -> SourceConfig:
    return SourceConfig(
        name="airport_a",
        url="https://example.com/sub",
        format="yaml",
        parse_error="fail",
        fetch=FetchConfig(timedelta(seconds=30), "mihomo/1.19.5", {}, False),
        refresh=RefreshConfig(interval=None, cron=()),
        rename=RenameConfig(prefix="[A] "),
        filter=FilterConfig(),
        plugins=SourcePluginConfig(),
        dns=SourceDnsConfig(
            dns_enabled,
            ("udp://1.1.1.1:53",),
            timedelta(seconds=5),
            "keep",
        ),
    )


@pytest.mark.asyncio
async def test_refresher_applies_dns_after_source_transform(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    resolver = FakeResolver(
        [ProxyRecord("airport_a", {"name": "[A] HK", "type": "vmess", "server": "93.184.216.34"})],
        ["dns warning"],
    )
    refresher = SourceRefresher(
        sources={"airport_a": source_config(dns_enabled=True)},
        plugins={},
        cache_store=cache_store,
        fetcher=FakeFetcher(
            b"proxies:\n"
            b"  - name: HK\n"
            b"    type: vmess\n"
            b"    server: example.com\n"
            b"    port: 443\n"
            b"    uuid: 00000000-0000-0000-0000-000000000000\n"
            b"    cipher: auto\n"
        ),
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=resolver,
    )

    result = await refresher.refresh("airport_a")
    cache = await cache_store.get("airport_a")

    assert result.ok is True
    assert result.warning_count == 1
    assert resolver.calls == 1
    assert cache is not None
    assert cache.proxies[0].data["server"] == "93.184.216.34"
    assert cache.proxies[0].data["name"] == "[A] HK"
    assert cache.warnings == ("dns warning",)


@pytest.mark.asyncio
async def test_dns_enabled_source_skips_conditional_fetch_headers(tmp_path) -> None:
    cache_store = JsonSourceCacheStore(
        CacheConfig(tmp_path / "cache", 2, 0o600, timedelta(days=7))
    )
    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await cache_store.set(
        "airport_a",
        __import__("mihomo_proxy_manager.models").models.SourceCache(
            "airport_a",
            1,
            now,
            now,
            '"old"',
            "Thu, 18 Jun 2026 00:00:00 GMT",
            1,
            (),
            None,
            (ProxyRecord("airport_a", {"name": "old", "server": "1.1.1.1"}),),
        ),
    )
    fetcher = FakeFetcher(
        b"proxies:\n"
        b"  - name: HK\n"
        b"    type: vmess\n"
        b"    server: example.com\n"
        b"    port: 443\n"
        b"    uuid: 00000000-0000-0000-0000-000000000000\n"
        b"    cipher: auto\n"
    )
    resolver = FakeResolver(
        [ProxyRecord("airport_a", {"name": "HK", "type": "vmess", "server": "93.184.216.34"})]
    )
    refresher = SourceRefresher(
        sources={"airport_a": source_config(dns_enabled=True)},
        plugins={},
        cache_store=cache_store,
        fetcher=fetcher,
        http_plugin=None,
        refresh_lock_timeout=timedelta(seconds=5),
        dns_resolver=resolver,
    )

    await refresher.refresh("airport_a")

    assert fetcher.calls == [(None, None)]
```

- [ ] **Step 2: Run refresher DNS tests to verify RED**

Run:

```bash
rtk uv run pytest tests/test_refresher_dns.py -q
```

Expected: FAIL because `SourceRefresher.__init__` has no `dns_resolver` parameter and does not call DNS.

- [ ] **Step 3: Integrate DNS into refresher**

In `src/mihomo_proxy_manager/refresher.py`, import:

```python
from .dns import DnsResolver
```

Update constructor:

```python
    def __init__(
        self,
        *,
        sources: dict[str, SourceConfig],
        plugins: dict[str, PluginConfig],
        cache_store: SourceCacheStore,
        fetcher: Any,
        http_plugin: HttpActionPlugin | None,
        refresh_lock_timeout: timedelta,
        dns_resolver: DnsResolver | None = None,
    ) -> None:
        self.sources = sources
        self.plugins = plugins
        self.cache_store = cache_store
        self.fetcher = fetcher
        self.http_plugin = http_plugin
        self.refresh_lock_timeout = refresh_lock_timeout
        self.dns_resolver = dns_resolver
```

In `_refresh_locked()`, call fetch with conditional validators only when DNS is disabled:

```python
            etag = old_cache.etag if old_cache and not source.dns.enabled else None
            last_modified = (
                old_cache.last_modified if old_cache and not source.dns.enabled else None
            )
            fetched = await self.fetcher.fetch(
                source.url,
                source.fetch,
                etag=etag,
                last_modified=last_modified,
            )
```

After source transform and before the empty check/cache write, add:

```python
            warnings = list(parsed.warnings)
            if source.dns.enabled:
                if self.dns_resolver is None:
                    raise RuntimeError("dns resolver is not configured")
                transformed, dns_warnings = await self.dns_resolver.resolve_records(
                    transformed,
                    source.dns,
                    source=source_name,
                )
                warnings.extend(dns_warnings)
            if not transformed:
                raise ParseError("no usable proxies after source transform")
```

Use `tuple(warnings)` and `len(warnings)` for `SourceCache.warnings` and `RefreshResult.warning_count`.

- [ ] **Step 4: Wire DNS resolver in CLI**

In `src/mihomo_proxy_manager/cli.py`, find construction of `SafeHttpClient`, `SubscriptionFetcher`, and `SourceRefresher`. Add:

```python
from .dns import DnsClient, DnsResolver
from .models import HttpConfig
```

Replace the runtime HTTP component construction in `_build_runtime()` with explicit shared `SafeHttpClient` instances:

```python
client = httpx.AsyncClient(cookies=_NoOpCookies())
plugin_safe_http = SafeHttpClient(client, config.http)
dns_http_config = HttpConfig(
    timeout=config.http.timeout,
    user_agent=config.http.user_agent,
    max_response_size=4096,
    max_redirects=config.http.max_redirects,
)
dns_safe_http = SafeHttpClient(client, dns_http_config)
fetcher = SubscriptionFetcher(client, config.http)
plugin = HttpActionPlugin(plugin_safe_http)
dns_client = DnsClient(safe_http=dns_safe_http)
dns_resolver = DnsResolver(
    client=dns_client,
    allow_private_network=config.security.allow_private_network_urls,
)
```

Pass `dns_resolver=dns_resolver` into `SourceRefresher`:

```python
refresher = SourceRefresher(
    sources=config.sources,
    plugins=config.plugins,
    cache_store=cache_store,
    fetcher=fetcher,
    http_plugin=plugin,
    refresh_lock_timeout=config.scheduler.refresh_lock_timeout,
    dns_resolver=dns_resolver,
)
```

- [ ] **Step 5: Run refresher DNS tests**

Run:

```bash
rtk uv run pytest tests/test_refresher_dns.py -q
```

Expected: PASS.

- [ ] **Step 6: Run impacted tests**

Run:

```bash
rtk uv run pytest tests/test_config.py tests/test_app.py tests/test_dns.py tests/test_refresher_dns.py tests/test_coverage_gaps.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/refresher.py src/mihomo_proxy_manager/cli.py tests/test_refresher_dns.py
rtk git commit -m "feat(refresher): resolve source proxy hosts with dns"
```

## Task 7: Documentation, Examples, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `config.toml`
- Optional modify: `tests/test_cli_smoke.py` if constructor changes require smoke test updates.

- [ ] **Step 1: Add config example docs**

In `README.md`, add a section near the configuration examples:

```markdown
### DNS 解析节点域名

默认不改写节点域名。需要时在 source 上显式启用：

```toml
[dns]
servers = ["udp://1.1.1.1:53", "https://dns.google/dns-query"]
timeout = "5s"
failure = "keep"

[sources.airport_a.dns]
enabled = true
servers = ["tls://1.1.1.1:853?servername=cloudflare-dns.com"]
failure = "drop"
```

`failure` 可选 `keep`、`drop`、`fail`。只会替换节点顶层 `server` 字段；已有
`servername`、`sni` 和 Host 相关字段不会被 IP 覆盖。
```
```

Add English equivalent to `README_EN.md`.

- [ ] **Step 2: Add route access docs**

In `README.md`, add:

```markdown
### 限制客户端 User-Agent

```toml
[routes.phone.access]
user_agent = ["mihomo/*", "clash-meta/*", "clash.meta/*"]
```

匹配使用大小写敏感的 shell glob。未配置或配置为空列表时保持开放。
```
```

Add English equivalent to `README_EN.md`.

- [ ] **Step 3: Update sample `config.toml`**

Add commented examples or disabled examples that preserve current behavior:

```toml
[dns]
servers = ["udp://1.1.1.1:53"]
timeout = "5s"
failure = "keep"

# [sources.example.dns]
# enabled = true
# servers = ["https://dns.google/dns-query"]
# failure = "keep"
```

Do not enable DNS for existing sample sources unless the user wants it.

- [ ] **Step 4: Run full verification**

Run:

```bash
rtk uv run ruff check
rtk uv run ty check
rtk uv run pytest -q
```

Expected: all pass. If `rtk uv run ty check` is not the project command, use the existing project type-check command from `Makefile` or skip only with a note in the final report.

- [ ] **Step 5: Commit**

```bash
rtk git add README.md README_EN.md config.toml tests/test_cli_smoke.py
rtk git commit -m "docs: document dns and user agent access config"
```

## Final Verification Checklist

- [ ] `rtk uv run pytest tests/test_config.py -q` passes.
- [ ] `rtk uv run pytest tests/test_app.py -q` passes.
- [ ] `rtk uv run pytest tests/test_dns.py -q` passes.
- [ ] `rtk uv run pytest tests/test_refresher_dns.py -q` passes.
- [ ] `rtk uv run pytest -q` passes.
- [ ] `rtk uv run ruff check` passes.
- [ ] Type check command passes or skipped with a concrete reason.
- [ ] Manual config check succeeds with a config containing `[dns]`, `[sources.<name>.dns]`, and `[routes.<name>.access]`.
