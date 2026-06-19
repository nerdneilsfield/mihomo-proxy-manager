# Route Output Formats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct-subscription route outputs for Surfboard, Quantumult X, and v2rayN-compatible Xray URI clients.

**Architecture:** Extend route output config, extract shared render preparation, add format-specific renderers behind a registry, and let the Starlette app serve both main and companion endpoints. Surfboard and QX use `server.public_base_url` to embed stable absolute URLs; Xray URI does not need companion routes.

**Tech Stack:** Python 3.12+, dataclasses, Starlette responses, PyYAML for existing provider output, stdlib `json`, `base64`, `urllib.parse`, pytest.

---

## File Structure

- Modify `src/mihomo_proxy_manager/models.py`
  - Add output config fields and `ServerConfig.public_base_url`.
- Modify `src/mihomo_proxy_manager/config.py`
  - Parse new config fields.
  - Validate output key allowlist, new output values, `public_base_url`, and companion path collisions.
- Modify `src/mihomo_proxy_manager/render.py`
  - Keep `ProviderRenderer` public behavior.
  - Add render request/response types, shared record preparation, renderer registry, companion-path helpers, and three new renderers.
- Modify `src/mihomo_proxy_manager/app.py`
  - Use renderer registry.
  - Route companion endpoints.
  - Pass absolute public URLs into renderers.
  - Return renderer status/headers/media type and log warnings.
- Modify `tests/test_config.py`
  - Cover config parsing and validation.
- Modify `tests/test_render.py`
  - Cover renderer golden outputs and escaping behavior.
- Modify `tests/test_app.py`
  - Cover companion routes, access policy, media types, public URL embedding, and QX redirect.
- Modify `docs/route-formats.md`
  - Mark first implemented formats and exact modes after code is complete.
- Optional modify `README.md` and `README_EN.md`
  - Add short examples only after tests pass.

## Task 1: Config Model Fields

**Files:**
- Modify: `src/mihomo_proxy_manager/models.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config parse test**

Add this test to `tests/test_config.py`:

```python
def test_route_output_new_format_fields_are_parsed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[server]
public_base_url = "https://mpm.example.com/base"

[sources.a]
url = "https://example.com/sub"

[routes.surf]
path = "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
sources = ["a"]

[routes.surf.output]
format = "surfboard"
mode = "full-profile"
test_url = "http://www.gstatic.com/generate_204"
test_interval = 300
test_timeout = 4
test_tolerance = 50

[routes.qx]
path = "/p/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
sources = ["a"]

[routes.qx.output]
format = "quantumult-x"
mode = "server-remote"
import_link = true
import_response = "plain"
import_target = "universal-link"
resource_tag = "Phones"

[routes.v2rayn]
path = "/p/cccccccccccccccccccccccccccccccccccccccc"
sources = ["a"]

[routes.v2rayn.output]
format = "xray-uri"
encoding = "plain"
""",
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.server.public_base_url == "https://mpm.example.com/base"
    assert loaded.routes["surf"].output.format == "surfboard"
    assert loaded.routes["surf"].output.mode == "full-profile"
    assert loaded.routes["surf"].output.test_interval == 300
    assert loaded.routes["qx"].output.import_response == "plain"
    assert loaded.routes["qx"].output.import_target == "universal-link"
    assert loaded.routes["qx"].output.resource_tag == "Phones"
    assert loaded.routes["v2rayn"].output.encoding == "plain"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
rtk pytest tests/test_config.py::test_route_output_new_format_fields_are_parsed -q
```

Expected: fail because `public_base_url` and new output fields do not exist yet.

- [ ] **Step 3: Extend dataclasses**

In `src/mihomo_proxy_manager/models.py`, update `ServerConfig` and `RouteOutputConfig`:

```python
@dataclass(frozen=True)
class RouteOutputConfig:
    """路由输出配置，控制输出格式和元数据注释。

    Route output configuration controlling output format and meta comments.
    """

    format: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = "provider"
    include_meta_comments: bool = False
    mode: Literal["default", "full-profile", "server-remote"] = "default"
    encoding: Literal["base64", "plain"] = "base64"
    import_link: bool = True
    import_response: Literal["redirect", "plain"] = "redirect"
    import_target: Literal["app-scheme", "universal-link"] = "app-scheme"
    resource_tag: str | None = None
    test_url: str = "http://www.gstatic.com/generate_204"
    test_interval: int = 600
    test_timeout: int = 5
    test_tolerance: int = 100
```

Add to `ServerConfig`:

```python
public_base_url: str | None = None
```

`mode = "default"` is a sentinel. Validation and renderers must interpret it as:

- `provider`: existing provider behavior.
- `surfboard`: `full-profile`.
- `quantumult-x`: `server-remote`.
- `xray-uri`: no mode behavior.

- [ ] **Step 4: Parse new config fields**

In `src/mihomo_proxy_manager/config.py`, update `ServerConfig(...)` construction:

```python
public_base_url=server_raw.get("public_base_url"),
```

Before building `RouteOutputConfig`, validate output keys:

```python
allowed_output_keys = {
    "format",
    "include_meta_comments",
    "mode",
    "encoding",
    "import_link",
    "import_response",
    "import_target",
    "resource_tag",
    "test_url",
    "test_interval",
    "test_timeout",
    "test_tolerance",
}
unknown_output_keys = sorted(set(output_values) - allowed_output_keys)
if unknown_output_keys:
    raise ValueError(
        "\n".join(
            f"route {name!r} output key is unsupported: {key!r}"
            for key in unknown_output_keys
        )
    )
```

Update `RouteOutputConfig(...)` construction:

```python
output=RouteOutputConfig(
    format=output_values.get("format", "provider"),
    include_meta_comments=bool(
        output_values.get(
            "include_meta_comments", output.default_include_meta_comments
        )
    ),
    mode=output_values.get("mode", "default"),
    encoding=output_values.get("encoding", "base64"),
    import_link=bool(output_values.get("import_link", True)),
    import_response=output_values.get("import_response", "redirect"),
    import_target=output_values.get("import_target", "app-scheme"),
    resource_tag=output_values.get("resource_tag"),
    test_url=output_values.get(
        "test_url", "http://www.gstatic.com/generate_204"
    ),
    test_interval=int(output_values.get("test_interval", 600)),
    test_timeout=int(output_values.get("test_timeout", 5)),
    test_tolerance=int(output_values.get("test_tolerance", 100)),
),
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
rtk pytest tests/test_config.py::test_route_output_new_format_fields_are_parsed -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
rtk git add src/mihomo_proxy_manager/models.py src/mihomo_proxy_manager/config.py tests/test_config.py
rtk git commit -m "feat(config): add route output options"
```

## Task 2: Config Validation

**Files:**
- Modify: `src/mihomo_proxy_manager/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing validation tests**

Add these tests to `tests/test_config.py`:

```python
def _write_base_config(path: Path, output: str, *, server: str = "") -> None:
    path.write_text(
        f"""
[server]
{server}

[sources.a]
url = "https://example.com/sub"

[routes.r]
path = "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
sources = ["a"]

[routes.r.output]
{output}
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ('format = "bad"', "output format is unsupported"),
        ('format = "xray-uri"\nencoding = "bad"', "encoding is unsupported"),
        ('format = "quantumult-x"\nimport_response = "bad"', "import_response is unsupported"),
        ('format = "quantumult-x"\nimport_target = "bad"', "import_target is unsupported"),
        ('format = "surfboard"\ntest_url = "https://example.com"', "test_url must use http://"),
        ('format = "surfboard"\ntest_interval = 0', "test_interval must be between"),
        ('format = "surfboard"\ninclude_meta_comments = true', "include_meta_comments is only supported"),
    ],
)
def test_route_output_validation_rejects_invalid_values(
    tmp_path: Path, output: str, message: str
) -> None:
    config_path = tmp_path / "config.toml"
    _write_base_config(
        config_path,
        output,
        server='public_base_url = "https://mpm.example.com"',
    )

    with pytest.raises(ValueError, match=message):
        load_config(config_path)


@pytest.mark.parametrize(
    "public_base_url",
    [
        "mpm.example.com",
        "ftp://mpm.example.com",
        "https://",
        "https://mpm.example.com/",
        "https://mpm.example.com?a=1",
        "https://mpm.example.com#frag",
    ],
)
def test_public_base_url_validation(tmp_path: Path, public_base_url: str) -> None:
    config_path = tmp_path / "config.toml"
    _write_base_config(
        config_path,
        'format = "surfboard"',
        server=f'public_base_url = "{public_base_url}"',
    )

    with pytest.raises(ValueError, match="public_base_url"):
        load_config(config_path)


def test_surfboard_requires_public_base_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    _write_base_config(config_path, 'format = "surfboard"')

    with pytest.raises(ValueError, match="public_base_url is required"):
        load_config(config_path)


def test_qx_import_link_requires_public_base_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    _write_base_config(config_path, 'format = "quantumult-x"\nimport_link = true')

    with pytest.raises(ValueError, match="public_base_url is required"):
        load_config(config_path)


def test_unknown_route_output_key_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    _write_base_config(config_path, 'format = "provider"\nencodng = "plain"')

    with pytest.raises(ValueError, match="output key is unsupported"):
        load_config(config_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: new validation cases fail.

- [ ] **Step 3: Add validation helpers**

In `src/mihomo_proxy_manager/config.py`, import `urlsplit`:

```python
from urllib.parse import urlsplit
```

Add helper functions near other validation helpers:

```python
def _validate_public_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "server public_base_url must include http(s) scheme and host"
    if parsed.query or parsed.fragment:
        return "server public_base_url must not include query or fragment"
    if value.endswith("/"):
        return "server public_base_url must not end with '/'"
    return None


def _companion_paths(route: RouteConfig) -> tuple[str, ...]:
    if route.output.format == "surfboard":
        return (f"{route.path}-nodes",)
    if route.output.format == "quantumult-x" and route.output.import_link:
        return (f"{route.path}-import",)
    return ()
```

- [ ] **Step 4: Extend `LoadedConfig.validate()`**

Replace provider-only format validation with:

```python
supported_formats = {"provider", "surfboard", "quantumult-x", "xray-uri"}
if route.output.format not in supported_formats:
    errors.append(
        f"route {route.name!r} output format is unsupported: {route.output.format!r}"
    )
if route.output.format == "provider":
    if route.output.mode != "default":
        errors.append(f"route {route.name!r} provider mode is unsupported")
else:
    if route.output.include_meta_comments:
        errors.append(
            f"route {route.name!r} include_meta_comments is only supported for provider output"
        )
if route.output.format == "surfboard":
    effective_mode = (
        "full-profile" if route.output.mode == "default" else route.output.mode
    )
    if effective_mode != "full-profile":
        errors.append(f"route {route.name!r} surfboard mode is unsupported")
    if not route.output.test_url.startswith("http://"):
        errors.append(f"route {route.name!r} surfboard test_url must use http://")
    if not (1 <= route.output.test_interval <= 2_678_400):
        errors.append(f"route {route.name!r} test_interval must be between 1 and 2678400")
    if not (1 <= route.output.test_timeout <= 300):
        errors.append(f"route {route.name!r} test_timeout must be between 1 and 300")
    if not (0 <= route.output.test_tolerance <= 60_000):
        errors.append(f"route {route.name!r} test_tolerance must be between 0 and 60000")
    if not self.server.public_base_url:
        errors.append(f"route {route.name!r} public_base_url is required for surfboard output")
if route.output.format == "quantumult-x":
    effective_mode = (
        "server-remote" if route.output.mode == "default" else route.output.mode
    )
    if effective_mode != "server-remote":
        errors.append(f"route {route.name!r} quantumult-x mode is unsupported")
    if route.output.import_response not in {"redirect", "plain"}:
        errors.append(f"route {route.name!r} import_response is unsupported")
    if route.output.import_target not in {"app-scheme", "universal-link"}:
        errors.append(f"route {route.name!r} import_target is unsupported")
    if route.output.import_link and not self.server.public_base_url:
        errors.append(
            f"route {route.name!r} public_base_url is required for quantumult-x import links"
        )
if route.output.format == "xray-uri":
    if route.output.mode != "default":
        errors.append(f"route {route.name!r} xray-uri mode is unsupported")
    if route.output.encoding not in {"base64", "plain"}:
        errors.append(f"route {route.name!r} encoding is unsupported")
```

At start of path collision validation, add status API to paths:

```python
if self.server.status_path:
    api_path = f"{self.server.status_path.rstrip('/')}/api"
    if api_path in paths:
        errors.append("status api path collides with another path")
    paths[api_path] = "status_api_path"
```

After adding each route main path, add companion paths:

```python
for companion_path in _companion_paths(route):
    companion_key = f"{key} companion {companion_path!r}"
    if companion_path in paths:
        errors.append(f"path collision for {companion_key} with {paths[companion_path]}")
    paths[companion_path] = companion_key
```

Before route loop or after HTTP user-agent validation, add:

```python
public_base_url_error = _validate_public_base_url(self.server.public_base_url)
if public_base_url_error:
    errors.append(public_base_url_error)
```

- [ ] **Step 5: Add companion collision tests**

Add to `tests/test_config.py`:

```python
def test_companion_path_collision_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[server]
public_base_url = "https://mpm.example.com"

[sources.a]
url = "https://example.com/sub"

[routes.surf]
path = "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
sources = ["a"]

[routes.surf.output]
format = "surfboard"

[routes.other]
path = "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nodes"
sources = ["a"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="path collision"):
        load_config(config_path)
```

- [ ] **Step 6: Run tests**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/config.py tests/test_config.py
rtk git commit -m "feat(config): validate route output formats"
```

## Task 3: Shared Render Pipeline

**Files:**
- Modify: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write failing provider compatibility test**

First update the existing `route()` helper in `tests/test_render.py` so later renderer tests can pass custom output config:

```python
def route(
    include_meta_comments: bool = False,
    output: RouteOutputConfig | None = None,
) -> RouteConfig:
    return RouteConfig(
        name="phone",
        path="/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml",
        sources=("airport_a",),
        require_all_sources=False,
        output=output or RouteOutputConfig("provider", include_meta_comments),
        rename=RenameConfig(prefix="[phone] "),
        filter=FilterConfig(),
    )
```

Then add this test to `tests/test_render.py` if equivalent snapshot does not already exist:

```python
def test_provider_renderer_still_returns_bytes() -> None:
    renderer = ProviderRenderer(yaml_sort_keys=False)
    body = renderer.render_sync(
        route(),
        [ProxyRecord("airport_a", {"name": "HK", "type": "direct"})],
    )

    assert isinstance(body, bytes)
    assert b"proxies:" in body
```

- [ ] **Step 2: Run provider render tests**

Run:

```bash
rtk pytest tests/test_render.py -q
```

Expected: pass before refactor.

- [ ] **Step 3: Add render request/response and helper**

In `src/mihomo_proxy_manager/render.py`, add imports:

```python
import base64
import json
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import quote, urlencode
```

Add types near top:

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


class RouteRenderer(Protocol):
    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        raise NotImplementedError

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        raise NotImplementedError
```

Add helper:

```python
def prepare_render_records(route: RouteConfig, records: list[ProxyRecord]) -> list[ProxyRecord]:
    transformed = apply_transform(
        records, filter_config=route.filter, rename_config=route.rename
    )
    normalized = _normalize_render_records(transformed)
    return repair_duplicate_names(normalized)
```

- [ ] **Step 4: Keep `ProviderRenderer` compatible**

Change `ProviderRenderer.render_sync()` to use `prepare_render_records()` but keep its signature and `bytes` return:

```python
def render_sync(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
    repaired = prepare_render_records(route, records)
    proxies = [_quote_proxy_strings(dict(record.data)) for record in repaired]
    payload = {"proxies": proxies}
    body = yaml.dump(
        payload,
        Dumper=_MihomoProviderDumper,
        allow_unicode=True,
        sort_keys=self.yaml_sort_keys,
    ).encode("utf-8")
    if route.output.include_meta_comments:
        prefix = (
            f"# generated_at: {datetime.now(UTC).isoformat()}\n"
            f"# route: {route.name}\n"
            f"# sources: {len(route.sources)}\n"
            f"# nodes: {len(proxies)}\n"
        ).encode("utf-8")
        return prefix + body
    return body
```

Add an adapter for the registry. Do not add another `render_sync` overload to `ProviderRenderer`; Python would replace the existing method.

```python
class ProviderRouteRenderer:
    def __init__(self, provider: ProviderRenderer) -> None:
        self.provider = provider

    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        return ()

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        return RenderResponse(
            body=self.provider.render_sync(request.route, request.records),
            media_type="application/yaml; charset=utf-8",
        )
```

Keep `async def render()` returning bytes for existing tests:

```python
async def render(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
    return self.render_sync(route, records)
```

- [ ] **Step 5: Add registry stub**

Add:

```python
def build_renderer_registry(*, yaml_sort_keys: bool) -> dict[str, RouteRenderer]:
    provider = ProviderRenderer(yaml_sort_keys=yaml_sort_keys)
    return {"provider": ProviderRouteRenderer(provider)}
```

- [ ] **Step 6: Run render tests**

Run:

```bash
rtk pytest tests/test_render.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/render.py tests/test_render.py
rtk git commit -m "refactor(render): share route preparation"
```

## Task 4: Xray URI Renderer

**Files:**
- Modify: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write failing Xray renderer tests**

Add to `tests/test_render.py`:

```python
import base64

from mihomo_proxy_manager.render import RenderRequest, XrayUriRenderer

def test_xray_uri_renderer_outputs_base64_subscription() -> None:
    renderer = XrayUriRenderer()
    body = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="xray-uri")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS 01",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "tls": True,
                        "servername": "example.com",
                    },
                ),
            ],
            main_public_url="https://mpm.example.com/p/v2rayn",
            companion_public_urls={},
        )
    )

    decoded = base64.b64decode(body.body).decode("utf-8")
    assert "ss://" in decoded
    assert "vless://00000000-0000-0000-0000-000000000000@example.com:443" in decoded
    assert "#VLESS%2001" in decoded
    assert body.media_type == "text/plain; charset=utf-8"


def test_xray_uri_renderer_plain_encoding() -> None:
    renderer = XrayUriRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="xray-uri", encoding="plain")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "example.com",
                    },
                )
            ],
            main_public_url="https://mpm.example.com/p/v2rayn",
            companion_public_urls={},
        )
    )

    text = response.body.decode("utf-8")
    assert text.startswith("trojan://secret@example.com:443?")
    assert "sni=example.com" in text
    assert text.endswith("#Trojan%2001")


def test_xray_uri_renderer_escapes_names_and_brackets_ipv6() -> None:
    renderer = XrayUriRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="xray-uri", encoding="plain")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "名字 #%,\"/",
                        "type": "trojan",
                        "server": "2001:db8::1",
                        "port": 443,
                        "password": "p#%,\"/",
                        "sni": "example.com",
                    },
                )
            ],
            main_public_url="https://mpm.example.com/p/v2rayn",
            companion_public_urls={},
        )
    )

    text = response.body.decode("utf-8")
    assert "trojan://p%23%25%2C%22%2F@[2001:db8::1]:443" in text
    assert "#%E5%90%8D%E5%AD%97%20%23%25%2C%22%2F" in text


def test_xray_uri_renderer_skips_unsupported_security_fields() -> None:
    renderer = XrayUriRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="xray-uri", encoding="plain")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Reality 01",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "reality-opts": {"public-key": "abc"},
                    },
                )
            ],
            main_public_url="https://mpm.example.com/p/v2rayn",
            companion_public_urls={},
        )
    )

    assert response.status_code == 422
    assert response.warnings
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest tests/test_render.py::test_xray_uri_renderer_outputs_base64_subscription tests/test_render.py::test_xray_uri_renderer_plain_encoding -q
```

Expected: fail because `XrayUriRenderer` does not exist.

- [ ] **Step 3: Implement URI helpers**

In `src/mihomo_proxy_manager/render.py`, add:

```python
def _string(value: object) -> str:
    return str(value) if value is not None else ""


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


def _node_name(data: Mapping[str, object]) -> str:
    return quote(_string(data.get("name")), safe="")


def _hostport(data: Mapping[str, object]) -> tuple[str, int]:
    return _string(data.get("server")), int(data.get("port", 0))
```

Add renderer:

```python
class XrayUriRenderer:
    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        return ()

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        records = prepare_render_records(request.route, request.records)
        lines: list[str] = []
        warnings: list[str] = []
        for record in records:
            line = self._render_record(record.data)
            if line is None:
                warnings.append(
                    f"route {request.route.name!r} skipped unsupported xray-uri node "
                    f"{record.data.get('name')!r} type={record.data.get('type')!r}"
                )
                continue
            lines.append(line)
        if not lines:
            return RenderResponse(
                body=b"no supported nodes for xray-uri output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )
        payload = "\n".join(lines) + "\n"
        if request.route.output.encoding == "base64":
            body = base64.b64encode(payload.encode("utf-8"))
        else:
            body = payload.encode("utf-8")
        return RenderResponse(
            body=body,
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )

    def _render_record(self, data: Mapping[str, object]) -> str | None:
        proxy_type = data.get("type")
        if proxy_type == "ss":
            return self._render_ss(data)
        if proxy_type == "vmess":
            return self._render_vmess(data)
        if proxy_type == "vless":
            return self._render_vless(data)
        if proxy_type == "trojan":
            return self._render_trojan(data)
        return None
```

Implement `_render_ss`, `_render_vmess`, `_render_vless`, `_render_trojan` with the exact field mappings from `docs/superpowers/specs/2026-06-20-route-output-formats-design.md`. Use `quote(..., safe="")` for userinfo and fragment. Use `urlencode(query)` for query params. Bracket IPv6 hosts in URI authority. If a node contains unsupported security-critical fields such as `reality-opts`, `ech-opts`, unsupported certificate pinning, or unsupported `flow`, skip the node with a warning.

- [ ] **Step 4: Register renderer**

Update `build_renderer_registry()`:

```python
return {
    "provider": ProviderRouteRenderer(provider),
    "xray-uri": XrayUriRenderer(),
}
```

- [ ] **Step 5: Run render tests**

Run:

```bash
rtk pytest tests/test_render.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
rtk git add src/mihomo_proxy_manager/render.py tests/test_render.py
rtk git commit -m "feat(render): add xray uri output"
```

## Task 5: App Renderer Registry And Companion Routing

**Files:**
- Modify: `src/mihomo_proxy_manager/app.py`
- Modify: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing app tests for xray route**

Add to `tests/test_app.py`:

```python
def app_config_with_route_output(
    tmp_path: Path,
    output: RouteOutputConfig,
    *,
    public_base_url: str | None = None,
    allowed_user_agents: tuple[str, ...] = (),
) -> AppConfig:
    path = config_file(tmp_path)
    config = load_config(path)
    route = config.routes["phone"]
    access = route.access
    if allowed_user_agents:
        access = RouteAccessConfig(user_agent=allowed_user_agents)
    server = replace(config.server, public_base_url=public_base_url)
    routes = {
        "phone": replace(route, output=output, access=access),
    }
    return replace(config, server=server, routes=routes)


def source_cache_with_nodes(*nodes: ProxyRecord) -> SourceCache:
    now = datetime.now(UTC)
    return SourceCache(
        source="airport_a",
        schema_version=1,
        last_attempt_at=now,
        last_success_at=now,
        etag=None,
        last_modified=None,
        node_count=len(nodes),
        warnings=(),
        last_error=None,
        proxies=tuple(nodes),
    )


def test_route_serves_xray_uri_output(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="xray-uri", encoding="plain"),
    )
    cache_store = JsonSourceCacheStore(config.cache)
    asyncio.run(
        cache_store.set(
            "airport_a",
            source_cache_with_nodes(
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "TR 01",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                    },
                )
            )
        )
    )
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("trojan://")
```

If imports are missing, add them at the top of `tests/test_app.py`:

```python
from dataclasses import replace
from urllib.parse import quote

from mihomo_proxy_manager.models import AppConfig, RouteAccessConfig, RouteOutputConfig
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
rtk pytest tests/test_app.py::test_route_serves_xray_uri_output -q
```

Expected: fail because app always uses `ProviderRenderer`.

- [ ] **Step 3: Update app route table**

In `src/mihomo_proxy_manager/app.py`, replace:

```python
renderer = ProviderRenderer(yaml_sort_keys=config.output.yaml_sort_keys)
route_by_path = {route.path: route for route in config.routes.values()}
```

with:

```python
renderers = build_renderer_registry(yaml_sort_keys=config.output.yaml_sort_keys)
route_by_path: dict[str, tuple[RouteConfig, str | None]] = {}
for route in config.routes.values():
    route_by_path[route.path] = (route, None)
    renderer = renderers[route.output.format]
    for companion_path in renderer.companion_paths(route):
        companion = companion_path.removeprefix(f"{route.path}-")
        route_by_path[companion_path] = (route, companion)
```

Import `build_renderer_registry`, `RenderRequest`, and `RenderResponse`.
Also import `redact_secret` from `mihomo_proxy_manager.security`.

- [ ] **Step 4: Update provider handler lookup**

Change:

```python
route = route_by_path.get(request.url.path)
```

to:

```python
route_match = route_by_path.get(request.url.path)
if route_match is None:
    logger.debug("provider 404: path={path}", path=request.url.path)
    return PlainTextResponse("not found", status_code=404)
route, companion = route_match
```

- [ ] **Step 5: Build public URLs and call renderer**

Add helper inside `create_app()`:

```python
def _public_url(path: str) -> str:
    if config.server.public_base_url:
        return f"{config.server.public_base_url}{path}"
    return path
```

Replace final render call:

```python
renderer = renderers[route.output.format]
companion_urls = {
    path.removeprefix(f"{route.path}-"): _public_url(path)
    for path in renderer.companion_paths(route)
}
response = renderer.render_sync(
    RenderRequest(
        route=route,
        records=records,
        main_public_url=_public_url(route.path),
        companion_public_urls=companion_urls,
        companion=companion,
    )
)
for warning in response.warnings:
    logger.warning("route render warning: {warning}", warning=redact_secret(warning))
return Response(
    response.body,
    status_code=response.status_code,
    media_type=response.media_type,
    headers=dict(response.headers),
)
```

Update log message to use `len(response.body)`.

- [ ] **Step 6: Run app tests**

Run:

```bash
rtk pytest tests/test_app.py -q
```

Expected: pass or only unrelated existing failures.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/app.py tests/test_app.py
rtk git commit -m "feat(app): dispatch route renderers"
```

## Task 6: Quantumult X Renderer

**Files:**
- Modify: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_render.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing QX renderer tests**

Add to `tests/test_render.py`:

```python
import json
from urllib.parse import unquote

from mihomo_proxy_manager.render import QuantumultXRenderer, RenderRequest

def test_quantumult_x_renderer_outputs_server_lines() -> None:
    renderer = QuantumultXRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="quantumult-x")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                ),
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Trojan 01",
                        "type": "trojan",
                        "server": "example.com",
                        "port": 443,
                        "password": "secret",
                        "sni": "example.com",
                        "skip-cert-verify": False,
                    },
                ),
            ],
            main_public_url="https://mpm.example.com/p/qx",
            companion_public_urls={"import": "https://mpm.example.com/p/qx-import"},
        )
    )

    text = response.body.decode("utf-8")
    assert "shadowsocks=example.com:443, method=chacha20-ietf-poly1305" in text
    assert "trojan=example.com:443, password=secret" in text
    assert "tls-verification=true" in text
```

Add import endpoint test to `tests/test_render.py`:

```python
def test_quantumult_x_import_redirect_response() -> None:
    renderer = QuantumultXRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(
                output=RouteOutputConfig(
                    format="quantumult-x",
                    import_response="redirect",
                    import_target="app-scheme",
                    resource_tag="MPM",
                )
            ),
            records=[],
            main_public_url="https://mpm.example.com/p/qx",
            companion_public_urls={"import": "https://mpm.example.com/p/qx-import"},
            companion="import",
        )
    )

    assert response.status_code == 302
    location = dict(response.headers)["Location"]
    assert location.startswith("quantumult-x:///add-resource?remote-resource=")
    encoded = location.split("remote-resource=", 1)[1]
    decoded = json.loads(unquote(encoded))
    assert decoded == {
        "server_remote": [
            "https://mpm.example.com/p/qx, tag=MPM, update-interval=86400, enabled=true"
        ]
    }


def test_quantumult_x_import_plain_universal_link_response() -> None:
    renderer = QuantumultXRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(
                output=RouteOutputConfig(
                    format="quantumult-x",
                    import_response="plain",
                    import_target="universal-link",
                    resource_tag="MPM",
                )
            ),
            records=[],
            main_public_url="https://mpm.example.com/p/qx",
            companion_public_urls={"import": "https://mpm.example.com/p/qx-import"},
            companion="import",
        )
    )

    assert response.status_code == 200
    text = response.body.decode("utf-8")
    assert text.startswith("https://quantumult.app/x/open-app/add-resource?")
    assert "remote-resource=" in text


def test_quantumult_x_renderer_skips_unsupported_reality_node() -> None:
    renderer = QuantumultXRenderer()
    response = renderer.render_sync(
        RenderRequest(
            route=route(output=RouteOutputConfig(format="quantumult-x")),
            records=[
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS Reality",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "reality-opts": {"public-key": "abc"},
                        "flow": "xtls-rprx-vision",
                    },
                )
            ],
            main_public_url="https://mpm.example.com/p/qx",
            companion_public_urls={"import": "https://mpm.example.com/p/qx-import"},
        )
    )

    assert response.status_code == 422
    assert response.warnings
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest tests/test_render.py::test_quantumult_x_renderer_outputs_server_lines tests/test_render.py::test_quantumult_x_import_redirect_response -q
```

Expected: fail because renderer does not exist.

- [ ] **Step 3: Implement `QuantumultXRenderer`**

In `src/mihomo_proxy_manager/render.py`, add:

```python
class QuantumultXRenderer:
    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        if route.output.import_link:
            return (f"{route.path}-import",)
        return ()

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        if request.companion == "import":
            return self._render_import(request)
        records = prepare_render_records(request.route, request.records)
        lines: list[str] = []
        warnings: list[str] = []
        for record in records:
            line = self._render_record(record.data)
            if line is None:
                warnings.append(
                    f"route {request.route.name!r} skipped unsupported quantumult-x node "
                    f"{record.data.get('name')!r} type={record.data.get('type')!r}"
                )
                continue
            lines.append(line)
        if not lines:
            return RenderResponse(
                body=b"no supported nodes for quantumult-x output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )
        return RenderResponse(
            body=("\n".join(lines) + "\n").encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )
```

Implement `_render_import()`:

```python
def _render_import(self, request: RenderRequest) -> RenderResponse:
    tag = request.route.output.resource_tag or request.route.name
    remote = f"{request.main_public_url}, tag={tag}, update-interval=86400, enabled=true"
    encoded = quote(json.dumps({"server_remote": [remote]}, ensure_ascii=False), safe="")
    if request.route.output.import_target == "universal-link":
        target = f"https://quantumult.app/x/open-app/add-resource?remote-resource={encoded}"
    else:
        target = f"quantumult-x:///add-resource?remote-resource={encoded}"
    if request.route.output.import_response == "redirect":
        return RenderResponse(
            body=b"",
            media_type="text/plain; charset=utf-8",
            status_code=302,
            headers=(("Location", target),),
        )
    return RenderResponse(
        body=(target + "\n").encode("utf-8"),
        media_type="text/plain; charset=utf-8",
    )
```

Implement `_render_record()` for `ss`, `vmess`, `vless`, and `trojan`. Follow mappings in the design spec. Skip Reality, ECH, unsupported certificate pinning, unknown transports, and unsupported flows unless exact source fields are mapped.

- [ ] **Step 4: Register renderer**

Update `build_renderer_registry()`:

```python
"quantumult-x": QuantumultXRenderer(),
```

- [ ] **Step 5: Add app tests for QX companion**

Add to `tests/test_app.py`:

```python
def test_qx_import_endpoint_redirects_with_public_base_url(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="quantumult-x", resource_tag="MPM"),
        public_base_url="https://mpm.example.com",
    )
    cache_store = JsonSourceCacheStore(config.cache)
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app, follow_redirects=False) as client:
        response = client.get(f"{config.routes['phone'].path}-import")

    assert response.status_code == 302
    assert response.headers["location"].startswith("quantumult-x:///add-resource?")
    assert quote("https://mpm.example.com", safe="") in response.headers["location"]
```

Add test for disabled import:

```python
def test_qx_import_endpoint_not_registered_when_disabled(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="quantumult-x", import_link=False),
    )
    cache_store = JsonSourceCacheStore(config.cache)
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(f"{config.routes['phone'].path}-import")

    assert response.status_code == 404
```

- [ ] **Step 6: Run tests**

Run:

```bash
rtk pytest tests/test_render.py tests/test_app.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/render.py src/mihomo_proxy_manager/app.py tests/test_render.py tests/test_app.py
rtk git commit -m "feat(render): add quantumult x output"
```

## Task 7: Surfboard Renderer

**Files:**
- Modify: `src/mihomo_proxy_manager/render.py`
- Test: `tests/test_render.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write failing Surfboard renderer tests**

Add to `tests/test_render.py`:

```python
from mihomo_proxy_manager.render import RenderRequest, SurfboardRenderer

def surfboard_request(companion: str | None = None) -> RenderRequest:
    return RenderRequest(
        route=route(output=RouteOutputConfig(format="surfboard")),
        records=[
            ProxyRecord(
                "airport_a",
                {
                    "name": "SS 01",
                    "type": "ss",
                    "server": "example.com",
                    "port": 443,
                    "cipher": "chacha20-ietf-poly1305",
                    "password": "password",
                },
            ),
            ProxyRecord(
                "airport_a",
                {
                    "name": "VMess 01",
                    "type": "vmess",
                    "server": "example.com",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "cipher": "auto",
                    "tls": True,
                    "network": "ws",
                    "ws-opts": {"path": "/ws", "headers": {"Host": "example.com"}},
                    "servername": "example.com",
                },
            ),
        ],
        main_public_url="https://mpm.example.com/p/surfboard",
        companion_public_urls={"nodes": "https://mpm.example.com/p/surfboard-nodes"},
        companion=companion,
    )


def test_surfboard_full_profile_contains_main_auto_proxy_groups() -> None:
    response = SurfboardRenderer().render_sync(surfboard_request())
    text = response.body.decode("utf-8")

    assert "[General]" in text
    assert "[Proxy]" in text
    assert "[Proxy Group]" in text
    assert "Main = select, Auto, Proxy, DIRECT" in text
    assert "Auto = url-test, SS 01, VMess 01, policy-path=https://mpm.example.com/p/surfboard-nodes" in text
    assert "Proxy = select, SS 01, VMess 01, policy-path=https://mpm.example.com/p/surfboard-nodes" in text
    assert "FINAL,Main" in text


def test_surfboard_nodes_companion_omits_section_header() -> None:
    response = SurfboardRenderer().render_sync(surfboard_request(companion="nodes"))
    text = response.body.decode("utf-8")

    assert "[Proxy]" not in text
    assert text.startswith("SS 01 = ss, example.com, 443")
    assert "VMess 01 = vmess, example.com, 443" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest tests/test_render.py::test_surfboard_full_profile_contains_main_auto_proxy_groups tests/test_render.py::test_surfboard_nodes_companion_omits_section_header -q
```

Expected: fail because renderer does not exist.

- [ ] **Step 3: Implement `SurfboardRenderer`**

In `src/mihomo_proxy_manager/render.py`, add:

```python
class SurfboardRenderer:
    def companion_paths(self, route: RouteConfig) -> tuple[str, ...]:
        return (f"{route.path}-nodes",)

    def render_sync(self, request: RenderRequest) -> RenderResponse:
        records = prepare_render_records(request.route, request.records)
        lines: list[str] = []
        warnings: list[str] = []
        for record in records:
            line = self._render_record(record.data)
            if line is None:
                warnings.append(
                    f"route {request.route.name!r} skipped unsupported surfboard node "
                    f"{record.data.get('name')!r} type={record.data.get('type')!r}"
                )
                continue
            lines.append(line)
        if not lines:
            return RenderResponse(
                body=b"no supported nodes for surfboard output",
                media_type="text/plain; charset=utf-8",
                status_code=422,
                warnings=tuple(warnings),
            )
        if request.companion == "nodes":
            return RenderResponse(
                body=("\n".join(lines) + "\n").encode("utf-8"),
                media_type="text/plain; charset=utf-8",
                warnings=tuple(warnings),
            )
        names = [line.split(" = ", 1)[0] for line in lines]
        nodes_url = request.companion_public_urls["nodes"]
        profile = self._full_profile(request.route, lines, names, nodes_url)
        return RenderResponse(
            body=profile.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            warnings=tuple(warnings),
        )
```

Implement `_full_profile()`:

```python
def _full_profile(
    self, route: RouteConfig, lines: list[str], names: list[str], nodes_url: str
) -> str:
    joined_names = ", ".join(names)
    return "\n".join(
        [
            "[General]",
            f"proxy-test-url = {route.output.test_url}",
            f"test-timeout = {route.output.test_timeout}",
            "",
            "[Proxy]",
            *lines,
            "",
            "[Proxy Group]",
            "Main = select, Auto, Proxy, DIRECT",
            (
                f"Auto = url-test, {joined_names}, policy-path={nodes_url}, "
                f"policy-regex-filter=.*, url={route.output.test_url}, "
                f"interval={route.output.test_interval}, "
                f"tolerance={route.output.test_tolerance}, "
                f"timeout={route.output.test_timeout}"
            ),
            f"Proxy = select, {joined_names}, policy-path={nodes_url}, policy-regex-filter=.*",
            "",
            "[Rule]",
            "FINAL,Main",
            "",
        ]
    )
```

Implement `_render_record()` for `ss`, `vmess`, and `trojan`. Skip `vless` and `hysteria2` for phase one.

- [ ] **Step 4: Register renderer**

Update `build_renderer_registry()`:

```python
"surfboard": SurfboardRenderer(),
```

- [ ] **Step 5: Add app tests for Surfboard companion and access**

Add to `tests/test_app.py`:

```python
def test_surfboard_profile_embeds_public_nodes_url(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    cache_store = JsonSourceCacheStore(config.cache)
    asyncio.run(
        cache_store.set(
            "airport_a",
            source_cache_with_nodes(
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                )
            )
        )
    )
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 200
    assert "policy-path=https://mpm.example.com" in response.text
    assert "FINAL,Main" in response.text


def test_surfboard_nodes_companion_uses_same_access_policy(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
        allowed_user_agents=("mihomo/1.19.5",),
    )
    cache_store = JsonSourceCacheStore(config.cache)
    asyncio.run(
        cache_store.set(
            "airport_a",
            source_cache_with_nodes(
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "SS 01",
                        "type": "ss",
                        "server": "example.com",
                        "port": 443,
                        "cipher": "chacha20-ietf-poly1305",
                        "password": "password",
                    },
                )
            )
        )
    )
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        forbidden = client.get(f"{config.routes['phone'].path}-nodes")
        allowed = client.get(
            f"{config.routes['phone'].path}-nodes",
            headers={"user-agent": "mihomo/1.19.5"},
        )

    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert "[Proxy]" not in allowed.text
```

These tests reuse `app_config_with_route_output()` and `source_cache_with_nodes()` from Task 5.

- [ ] **Step 6: Run tests**

Run:

```bash
rtk pytest tests/test_render.py tests/test_app.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
rtk git add src/mihomo_proxy_manager/render.py tests/test_render.py tests/test_app.py
rtk git commit -m "feat(render): add surfboard output"
```

## Task 8: End-To-End Error Cases And Docs

**Files:**
- Modify: `tests/test_render.py`
- Modify: `tests/test_app.py`
- Modify: `docs/route-formats.md`
- Optional modify: `README.md`, `README_EN.md`

- [ ] **Step 1: Add all-skipped 422 tests**

Add to `tests/test_app.py`:

```python
def test_all_skipped_nodes_return_422(tmp_path: Path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    cache_store = JsonSourceCacheStore(config.cache)
    asyncio.run(
        cache_store.set(
            "airport_a",
            source_cache_with_nodes(
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "VLESS 01",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                    },
                )
            )
        )
    )
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(config.routes["phone"].path)

    assert response.status_code == 422
    assert "no supported nodes" in response.text
```

- [ ] **Step 2: Add warning redaction test**

Add a test that serves a route with an unsupported node containing `password = "secret-value"` and asserts captured logs do not contain `secret-value`.

Use pytest `caplog`:

```python
def test_render_warnings_redact_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    cache_store = JsonSourceCacheStore(config.cache)
    asyncio.run(
        cache_store.set(
            "airport_a",
            source_cache_with_nodes(
                ProxyRecord(
                    "airport_a",
                    {
                        "name": "Bad 01",
                        "type": "vless",
                        "server": "example.com",
                        "port": 443,
                        "uuid": "00000000-0000-0000-0000-000000000000",
                        "password": "secret-value",
                    },
                )
            )
        )
    )
    app = create_app(config, cache_store=cache_store, refresher=None, scheduler=None)

    with caplog.at_level("WARNING"):
        with TestClient(app) as client:
            response = client.get(config.routes["phone"].path)

    assert response.status_code == 422
    assert "secret-value" not in caplog.text
```

- [ ] **Step 3: Run full test suite**

Run:

```bash
rtk pytest -q
```

Expected: pass.

- [ ] **Step 4: Update docs**

In `docs/route-formats.md`, update the support matrix:

- Mark `xray-uri` as implemented with default `base64`.
- Mark `quantumult-x` as implemented for `server-remote` and import endpoint.
- Mark `surfboard` as implemented for `full-profile` and `-nodes`.
- Leave sing-box and Loon as future work.

Add short config examples:

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

- [ ] **Step 5: Run docs/static checks**

Run:

```bash
rtk git diff --check
rtk pytest -q
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
rtk git add tests/test_app.py tests/test_render.py docs/route-formats.md README.md README_EN.md
rtk git commit -m "docs: document direct route subscriptions"
```

## Final Verification

- [ ] Run full tests:

```bash
rtk pytest -q
```

- [ ] Run static diff check:

```bash
rtk git diff --check
```

- [ ] Inspect final status:

```bash
rtk git status --short
```

Expected:

- Tests pass.
- Diff check passes.
- Working tree is clean after final commit.

## Implementation Notes

- Keep existing `ProviderRenderer.render_sync(route, records) -> bytes` behavior for compatibility with current tests.
- Do not include warnings in client payloads; some clients treat comments as invalid syntax.
- Do not log raw rendered subscription lines because they may contain passwords.
- Treat Reality/ECH/unsupported certificate pinning as skip-with-warning for QX/Surfboard/Xray URI unless exact mapping exists.
- Xray URI output may include VLESS flow and Reality later, but phase one should prefer correctness over broad claims.
- Surfboard phase one supports `ss`, `vmess`, and `trojan`; QX and Xray URI support `ss`, `vmess`, `vless`, and `trojan`.
