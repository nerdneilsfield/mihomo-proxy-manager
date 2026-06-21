# Auto Route Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `format = "auto"` routes that choose provider, Surfboard, Quantumult X, or xray URI output from query selectors, companion suffixes, User-Agent, or `auto_default`.

**Architecture:** Keep fixed-format routes unchanged. Add a small target-selection module for aliases, query parsing, UA detection, canonical targets, and companion compatibility; `app.create_app()` uses it only for auto routes after access checks. Config validation registers auto companion paths and rejects invalid auto modes, missing `public_base_url`, and companion collisions before runtime.

**Tech Stack:** Python dataclasses and `Literal`, Starlette `QueryParams`/`TestClient`, pytest, ty typecheck, existing route renderers in `src/mihomo_proxy_manager/render.py`.

---

## File Structure

- Modify `src/mihomo_proxy_manager/models.py`: add `"auto"` to route output format literal and `auto_default` with implemented fixed target formats only.
- Modify `src/mihomo_proxy_manager/config.py`: parse `auto_default`, allow `auto` output keys, validate auto mode/default/public URL, and include auto companion paths in collision checks.
- Create `src/mihomo_proxy_manager/route_targets.py`: central target alias, query selector, UA, companion, and canonical URL logic.
- Modify `src/mihomo_proxy_manager/app.py`: register auto main/companion paths without looking up `renderers["auto"]`; enforce access before target validation; build effective per-request route/output and canonical public URLs.
- Modify tests:
  - `tests/test_config.py`: config parsing/validation/collision.
  - `tests/test_route_targets.py`: pure selector and UA logic.
  - `tests/test_app.py`: end-to-end route behavior, access ordering, canonical embedded URLs.
  - `tests/test_render.py`: only if a `RenderRequest` field is added; new fields must have defaults.
- Modify docs/examples:
  - `README.md`
  - `README_EN.md`
  - `docs/route-formats.md`
  - `examples/config.toml`

## Task 1: Config And Model Support

**Files:**
- Modify: `src/mihomo_proxy_manager/models.py`
- Modify: `src/mihomo_proxy_manager/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add failing config tests**

Append these tests near existing route-output tests in `tests/test_config.py`:

```python
def test_auto_route_output_fields_are_parsed(temp_config_path: Path) -> None:
    config = load_config(
        _write_base_config(
            temp_config_path,
            '''
format = "auto"
auto_default = "xray-uri"
include_meta_comments = true
encoding = "plain"
import_link = true
resource_tag = "Phones"
test_url = "http://www.gstatic.com/generate_204"
test_interval = 300
test_timeout = 4
test_tolerance = 50
''',
        )
    )

    output = config.routes["phone"].output
    assert output.format == "auto"
    assert output.auto_default == "xray-uri"
    assert output.include_meta_comments is True
    assert output.encoding == "plain"
    assert output.import_link is True
    assert output.resource_tag == "Phones"
    assert output.test_interval == 300
    assert output.test_timeout == 4
    assert output.test_tolerance == 50


@pytest.mark.parametrize(
    ("output", "message"),
    (
        ('format = "auto"\nauto_default = "auto"', "auto_default is unsupported"),
        ('format = "auto"\nauto_default = "sing-box"', "auto_default is unsupported"),
        ('format = "auto"\nmode = "full-profile"', "auto output mode must be default"),
        ('format = "auto"\nmode = "server-remote"', "auto output mode must be default"),
    ),
)
def test_auto_route_output_validation_rejects_invalid_values(
    temp_config_path: Path, output: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_write_base_config(temp_config_path, output))


def test_auto_route_requires_public_base_url(temp_config_path: Path) -> None:
    with pytest.raises(ValueError, match="public_base_url is required for auto output"):
        load_config(
            _write_base_config(
                temp_config_path,
                'format = "auto"',
                server="",
            )
        )


def test_auto_route_import_companion_not_registered_when_disabled(
    temp_config_path: Path,
) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.auto]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a"]

[routes.auto.output]
format = "auto"
import_link = false

[routes.normal_import]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL-import"
sources = ["airport_a"]
"""

    config = load_config(write_config(temp_config_path, body))

    assert config.routes["auto"].output.format == "auto"
    assert config.routes["normal_import"].path.endswith("-import")


def test_auto_route_companion_path_collision_is_rejected(
    temp_config_path: Path,
) -> None:
    body = """
[server]
public_base_url = "https://mpm.example.com/base"
status_path = "/s/X6HfeBRQz6xqk9S4dTV7gQwL2nP8aYcM"

[sources.airport_a]
url = "https://example.com/sub"

[routes.auto]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a"]

[routes.auto.output]
format = "auto"

[routes.nodes]
path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL-nodes"
sources = ["airport_a"]
"""

    with pytest.raises(ValueError, match="path collision"):
        load_config(write_config(temp_config_path, body))
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: failures mention unsupported output key `auto_default`, unsupported route output format `"auto"`, or missing `auto_default` attribute.

- [ ] **Step 3: Update model**

Change `RouteOutputConfig` in `src/mihomo_proxy_manager/models.py`:

```python
format: Literal["provider", "surfboard", "quantumult-x", "xray-uri", "auto"] = (
    "provider"
)
auto_default: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = (
    "provider"
)
```

Keep all existing fields and defaults unchanged.

- [ ] **Step 4: Update config parsing and companion paths**

In `load_config()`, add `"auto_default"` to `allowed_route_output_keys`, pass it into `RouteOutputConfig`, and keep `include_meta_comments` inheritance for `provider` only unless explicitly set:

```python
"auto_default",
```

```python
auto_default=output_values.get("auto_default", "provider"),
```

Update `_companion_paths()`:

```python
def _companion_paths(route: RouteConfig) -> tuple[str, ...]:
    if route.output.format == "surfboard":
        return (f"{route.path}-nodes",)
    if route.output.format == "quantumult-x" and route.output.import_link:
        return (f"{route.path}-import",)
    if route.output.format == "auto":
        paths = [f"{route.path}-nodes"]
        if route.output.import_link:
            paths.append(f"{route.path}-import")
        return tuple(paths)
    return ()
```

- [ ] **Step 5: Update validation**

In `LoadedConfig.validate()`, include `"auto"` in supported route formats. Add this branch before fixed-format branches:

```python
if route.output.format == "auto":
    if route.output.mode != "default":
        errors.append(f"route {route.name!r} auto output mode must be default")
    if route.output.auto_default not in {
        "provider",
        "surfboard",
        "quantumult-x",
        "xray-uri",
    }:
        errors.append(
            f"route {route.name!r} auto_default is unsupported: "
            f"{route.output.auto_default!r}"
        )
    if route.output.import_response not in {"redirect", "plain"}:
        errors.append(
            f"route {route.name!r} quantumult-x import_response is unsupported: "
            f"{route.output.import_response!r}"
        )
    if route.output.import_target not in {"app-scheme", "universal-link"}:
        errors.append(
            f"route {route.name!r} quantumult-x import_target is unsupported: "
            f"{route.output.import_target!r}"
        )
    if not route.output.test_url.startswith("http://"):
        errors.append(f"route {route.name!r} surfboard test_url must use http://")
    if not 1 <= route.output.test_interval <= 2_678_400:
        errors.append(
            f"route {route.name!r} surfboard test_interval must be between 1 and 2678400"
        )
    if not 1 <= route.output.test_timeout <= 300:
        errors.append(
            f"route {route.name!r} surfboard test_timeout must be between 1 and 300"
        )
    if not 0 <= route.output.test_tolerance <= 60_000:
        errors.append(
            f"route {route.name!r} surfboard test_tolerance must be between 0 and 60000"
        )
    if route.output.encoding not in {"base64", "plain"}:
        errors.append(
            f"route {route.name!r} xray-uri encoding is unsupported: "
            f"{route.output.encoding!r}"
        )
    if not self.server.public_base_url:
        errors.append(f"route {route.name!r} public_base_url is required for auto output")
    continue
```

Fixed `provider`, `surfboard`, `quantumult-x`, and `xray-uri` validation remains semantically unchanged.

- [ ] **Step 6: Run config tests**

Run:

```bash
rtk pytest tests/test_config.py -q
```

Expected: all config tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
rtk git add src/mihomo_proxy_manager/models.py src/mihomo_proxy_manager/config.py tests/test_config.py
rtk git commit -m "feat(config): accept auto route output"
```

## Task 2: Target Resolver Module

**Files:**
- Create: `src/mihomo_proxy_manager/route_targets.py`
- Create: `tests/test_route_targets.py`

- [ ] **Step 1: Add failing resolver tests**

Create `tests/test_route_targets.py`:

```python
from mihomo_proxy_manager.route_targets import (
    COMPANION_TARGETS,
    QuerySelection,
    canonical_target_for_format,
    has_future_user_agent_signal,
    normalize_target_alias,
    resolve_query_selection,
    resolve_user_agent_format,
)


def test_resolve_query_selection_uses_first_present_key_and_first_value() -> None:
    selection = resolve_query_selection(
        {
            "target": ["surfboard", "quanx"],
            "format": ["v2rayn"],
        }
    )

    assert selection == QuerySelection(format="surfboard", explicit=True)


def test_blank_query_selector_suppresses_lower_priority_keys() -> None:
    selection = resolve_query_selection(
        {
            "target": [""],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_whitespace_query_selector_suppresses_lower_priority_keys() -> None:
    selection = resolve_query_selection(
        {
            "target": ["   "],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_auto_query_selector_means_no_explicit_target() -> None:
    selection = resolve_query_selection(
        {
            "target": ["auto"],
            "format": ["quanx"],
        }
    )

    assert selection == QuerySelection(format=None, explicit=False)


def test_query_aliases_are_trimmed_case_insensitive_and_underscore_normalized() -> None:
    assert resolve_query_selection({"target": [" Quantumult_X "]}).format == "quantumult-x"
    assert resolve_query_selection({"target": ["clash-meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["clash.meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["provider"]}).format == "provider"
    assert resolve_query_selection({"target": ["mihomo"]}).format == "provider"
    assert resolve_query_selection({"target": ["meta"]}).format == "provider"
    assert resolve_query_selection({"target": ["v2rayN"]}).format == "xray-uri"


def test_alias_normalization_uses_already_decoded_http_parser_value() -> None:
    assert normalize_target_alias("clash_meta") == "clash-meta"
    assert normalize_target_alias("clash.meta") == "clash.meta"
    assert normalize_target_alias("clash%252Dmeta") == "clash%252dmeta"


def test_query_alias_resolver_does_not_double_decode_http_parser_values() -> None:
    from starlette.datastructures import QueryParams

    once_decoded = QueryParams("target=clash%2Dmeta").getlist("target")
    double_encoded = QueryParams("target=clash%252Dmeta").getlist("target")

    assert resolve_query_selection({"target": once_decoded}).format == "provider"
    selection = resolve_query_selection({"target": double_encoded})
    assert selection.format is None
    assert selection.explicit is True
    assert selection.unsupported == "clash%2dmeta"


def test_reserved_future_query_target_is_explicit_but_unimplemented() -> None:
    selection = resolve_query_selection({"target": ["singbox"]})

    assert selection.format == "sing-box"
    assert selection.explicit is True


def test_unknown_query_target_is_explicit_unknown() -> None:
    selection = resolve_query_selection({"target": ["not-a-client"]})

    assert selection.format is None
    assert selection.explicit is True
    assert selection.unsupported == "not-a-client"


def test_user_agent_matching_is_case_insensitive() -> None:
    assert resolve_user_agent_format("quantumult x/1.0") == "quantumult-x"
    assert resolve_user_agent_format("surfboard/2.0") == "surfboard"
    assert resolve_user_agent_format("V2RAYN/6.0") == "xray-uri"
    assert resolve_user_agent_format("flclash/1.0") == "provider"


def test_specific_xray_user_agent_beats_broad_provider_signal() -> None:
    assert resolve_user_agent_format("v2rayN meta") == "xray-uri"


def test_future_user_agent_signal_does_not_mask_implemented_signal() -> None:
    assert resolve_user_agent_format("sing-box Clash") == "provider"


def test_only_future_user_agent_signal_returns_none() -> None:
    assert resolve_user_agent_format("sing-box/1.0") is None
    assert has_future_user_agent_signal("sing-box/1.0") is True
    assert has_future_user_agent_signal("unknown-client/1.0") is False


def test_canonical_targets() -> None:
    assert canonical_target_for_format("provider") == "clash"
    assert canonical_target_for_format("surfboard") == "surfboard"
    assert canonical_target_for_format("quantumult-x") == "quanx"
    assert canonical_target_for_format("xray-uri") == "v2rayn"


def test_companion_targets() -> None:
    assert COMPANION_TARGETS == {"nodes": "surfboard", "import": "quantumult-x"}
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
rtk pytest tests/test_route_targets.py -q
```

Expected: import error for `mihomo_proxy_manager.route_targets`.

- [ ] **Step 3: Implement resolver module**

Create `src/mihomo_proxy_manager/route_targets.py`:

```python
"""Route output target selection for auto routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
SELECTOR_KEYS = ("target", "format", "flag", "client")
IMPLEMENTED_FORMATS = {"provider", "surfboard", "quantumult-x", "xray-uri"}
FUTURE_FORMATS = {"sing-box", "loon"}
COMPANION_TARGETS = {"nodes": "surfboard", "import": "quantumult-x"}

TARGET_ALIASES = {
    "provider": "provider",
    "clash": "provider",
    "mihomo": "provider",
    "clash-meta": "provider",
    "clash.meta": "provider",
    "meta": "provider",
    "xray-uri": "xray-uri",
    "xray": "xray-uri",
    "v2ray": "xray-uri",
    "v2rayn": "xray-uri",
    "v2rayng": "xray-uri",
    "general": "xray-uri",
    "quantumult-x": "quantumult-x",
    "quanx": "quantumult-x",
    "qx": "quantumult-x",
    "quantumult x": "quantumult-x",
    "surfboard": "surfboard",
    "sing-box": "sing-box",
    "singbox": "sing-box",
    "sfa": "sing-box",
    "sfi": "sing-box",
    "sfm": "sing-box",
    "hiddify": "sing-box",
    "loon": "loon",
}

CANONICAL_TARGETS = {
    "provider": "clash",
    "surfboard": "surfboard",
    "quantumult-x": "quanx",
    "xray-uri": "v2rayn",
}

UA_SIGNALS = (
    ("quantumult%20x", "quantumult-x"),
    ("quantumult x", "quantumult-x"),
    ("quantumult-x", "quantumult-x"),
    ("surfboard", "surfboard"),
    ("v2rayn", "xray-uri"),
    ("v2rayng", "xray-uri"),
    ("v2ray", "xray-uri"),
    ("clash", "provider"),
    ("mihomo", "provider"),
    ("flclash", "provider"),
    ("clash-verge", "provider"),
    ("meta", "provider"),
)

FUTURE_UA_SIGNALS = (
    ("sing-box", "sing-box"),
    ("singbox", "sing-box"),
    ("hiddify", "sing-box"),
    ("sfa", "sing-box"),
    ("sfi", "sing-box"),
    ("sfm", "sing-box"),
    ("loon", "loon"),
)


@dataclass(frozen=True)
class QuerySelection:
    format: str | None
    explicit: bool
    unsupported: str | None = None


def normalize_target_alias(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def resolve_query_selection(
    values_by_key: Mapping[str, Sequence[str]],
) -> QuerySelection:
    for key in SELECTOR_KEYS:
        values = values_by_key.get(key)
        if not values:
            continue
        raw_value = values[0]
        alias = normalize_target_alias(raw_value)
        if alias == "" or alias == "auto":
            return QuerySelection(format=None, explicit=False)
        target = TARGET_ALIASES.get(alias)
        if target is None:
            return QuerySelection(format=None, explicit=True, unsupported=alias)
        return QuerySelection(format=target, explicit=True)
    return QuerySelection(format=None, explicit=False)


def resolve_user_agent_format(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    lowered = user_agent.lower()
    for signal, output_format in UA_SIGNALS:
        if signal in lowered:
            return output_format
    return None


def has_future_user_agent_signal(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    lowered = user_agent.lower()
    return any(signal in lowered for signal, _ in FUTURE_UA_SIGNALS)


def canonical_target_for_format(output_format: str) -> str:
    return CANONICAL_TARGETS[output_format]
```

- [ ] **Step 4: Run resolver tests**

Run:

```bash
rtk pytest tests/test_route_targets.py -q
```

Expected: all resolver tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
rtk git add src/mihomo_proxy_manager/route_targets.py tests/test_route_targets.py
rtk git commit -m "feat(route): add auto target resolver"
```

## Task 3: Auto Route App Behavior

**Files:**
- Modify: `src/mihomo_proxy_manager/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Add failing app tests**

Add helpers near `app_config_with_route_output()` in `tests/test_app.py`:

Add this import in the `tests/test_app.py` import section:

```python
from typing import Literal
```

Then add helpers near `app_config_with_route_output()`:

```python

def auto_app_config(
    tmp_path,
    *,
    auto_default: Literal["provider", "surfboard", "quantumult-x", "xray-uri"] = (
        "provider"
    ),
    import_link: bool = True,
    allowed_user_agents: tuple[str, ...] = (),
) -> AppConfig:
    return app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(
            format="auto",
            auto_default=auto_default,
            encoding="plain",
            import_link=import_link,
            resource_tag="MPM",
        ),
        public_base_url="https://mpm.example.com",
        allowed_user_agents=allowed_user_agents,
    )


def ss_node(name: str = "SS 01") -> ProxyRecord:
    return ProxyRecord(
        "airport_a",
        {
            "name": name,
            "type": "ss",
            "server": "example.com",
            "port": 443,
            "cipher": "chacha20-ietf-poly1305",
            "password": "password",
        },
    )
```

Add these tests after existing route-output dispatch tests:

```python
@pytest.mark.asyncio
async def test_auto_route_query_targets_each_renderer(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        surfboard = client.get(f"{path}?target=surfboard")
        qx = client.get(f"{path}?format=quanx")
        provider = client.get(f"{path}?flag=meta")
        xray = client.get(f"{path}?client=v2rayn")

    assert surfboard.status_code == 200
    assert "[Proxy]" in surfboard.text
    assert qx.status_code == 200
    assert qx.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in qx.text
    assert provider.status_code == 200
    assert provider.headers["content-type"].startswith("application/yaml")
    assert "proxies:" in provider.text
    assert xray.status_code == 200
    assert xray.headers["content-type"].startswith("text/plain")
    assert xray.text.startswith("ss://")


@pytest.mark.asyncio
async def test_auto_route_query_priority_and_blank_selector(tmp_path) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        target_wins = client.get(f"{path}?target=surfboard&format=quanx")
        format_wins = client.get(f"{path}?format=quanx&flag=meta&client=v2rayn")
        flag_wins = client.get(f"{path}?flag=meta&client=v2rayn")
        blank_suppresses_format = client.get(f"{path}?target=&format=quanx")
        whitespace_suppresses_format = client.get(f"{path}?target=%20%20&format=quanx")

    assert "[Proxy]" in target_wins.text
    assert format_wins.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in format_wins.text
    assert "proxies:" in flag_wins.text
    assert "proxies:" in blank_suppresses_format.text
    assert "proxies:" in whitespace_suppresses_format.text


@pytest.mark.asyncio
async def test_auto_route_user_agent_selection_and_case_insensitivity(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        qx = client.get(path, headers={"User-Agent": "quantumult x/1.0"})
        xray = client.get(path, headers={"User-Agent": "V2RAYN meta"})
        provider = client.get(path, headers={"User-Agent": "sing-box Clash"})

    assert qx.text.startswith("shadowsocks=example.com:443,")
    assert "tag=SS 01" in qx.text
    assert xray.text.startswith("ss://")
    assert "proxies:" in provider.text


@pytest.mark.asyncio
async def test_auto_route_companion_suffix_beats_user_agent_when_query_auto(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        nodes = client.get(
            f"{path}-nodes?target=auto",
            headers={"User-Agent": "FlClash/1.0"},
        )
        qx_import = client.get(
            f"{path}-import?target=auto",
            headers={"User-Agent": "FlClash/1.0"},
            follow_redirects=False,
        )

    assert nodes.status_code == 200
    assert nodes.text.startswith("SS 01 = ss,")
    assert qx_import.status_code == 302
    assert "target%3Dquanx" in qx_import.headers["location"]


@pytest.mark.asyncio
async def test_auto_route_future_user_agent_only_logs_warning_and_uses_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    warnings: list[str] = []

    def capture_warning(message: str, *args: object, **kwargs: object) -> None:
        if kwargs:
            warnings.append(message.format(**kwargs))
        elif args:
            warnings.append(message.format(*args))
        else:
            warnings.append(message)

    monkeypatch.setattr("mihomo_proxy_manager.app.logger.warning", capture_warning)

    with TestClient(app) as client:
        response = client.get(
            config.routes["phone"].path,
            headers={"User-Agent": "sing-box/1.0"},
        )

    assert response.status_code == 200
    assert "proxies:" in response.text
    assert len(warnings) == 1
    assert "future User-Agent target" in warnings[0]
    assert "sing-box/1.0" in warnings[0]


@pytest.mark.asyncio
async def test_auto_route_main_target_auto_uses_user_agent_then_default(tmp_path) -> None:
    config = auto_app_config(tmp_path, auto_default="provider")
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        ua_selected = client.get(
            f"{path}?target=auto",
            headers={"User-Agent": "Surfboard/2.0"},
        )
        default_selected = client.get(f"{path}?target=auto")

    assert "[Proxy]" in ua_selected.text
    assert "proxies:" in default_selected.text


@pytest.mark.asyncio
async def test_auto_route_canonical_urls_for_incoming_selector_keys(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        by_target = client.get(f"{path}?target=surfboard")
        by_format = client.get(f"{path}?format=surfboard")
        by_flag = client.get(f"{path}?flag=surfboard")
        by_client = client.get(f"{path}?client=surfboard")

    for response in (by_target, by_format, by_flag, by_client):
        assert response.status_code == 200
        assert f"{path}-nodes?target=surfboard" in response.text


@pytest.mark.asyncio
async def test_auto_route_import_disabled_leaves_import_path_404(tmp_path) -> None:
    config = auto_app_config(tmp_path, import_link=False)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(f"{config.routes['phone'].path}-import")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fixed_provider_and_surfboard_ignore_auto_selectors_and_user_agent(
    tmp_path,
) -> None:
    provider_config = app_config_with_route_output(tmp_path, RouteOutputConfig())
    surfboard_config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )

    for config, expected in (
        (provider_config, "proxies:"),
        (surfboard_config, "[Proxy]"),
    ):
        store = JsonSourceCacheStore(config.cache)
        await store.set("airport_a", source_cache_with_nodes(ss_node()))
        app = create_app(config, cache_store=store, refresher=None, scheduler=None)

        with TestClient(app) as client:
            response = client.get(
                f"{config.routes['phone'].path}?target=quanx&format=v2rayn",
                headers={"User-Agent": "Quantumult X/1.0"},
            )

        assert response.status_code == 200
        assert expected in response.text


@pytest.mark.asyncio
async def test_fixed_surfboard_embedded_urls_stay_queryless(tmp_path) -> None:
    config = app_config_with_route_output(
        tmp_path,
        RouteOutputConfig(format="surfboard"),
        public_base_url="https://mpm.example.com",
    )
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)

    with TestClient(app) as client:
        response = client.get(
            f"{config.routes['phone'].path}?target=quanx",
            headers={"User-Agent": "Quantumult X/1.0"},
        )

    assert response.status_code == 200
    assert f"{config.routes['phone'].path}-nodes" in response.text
    assert "target=" not in response.text


@pytest.mark.asyncio
async def test_auto_route_rejects_unsupported_and_incompatible_targets(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        unknown = client.get(f"{path}?target=not-a-client")
        future = client.get(f"{path}?target=singbox")
        incompatible = client.get(f"{path}-nodes?target=quanx")

    assert unknown.status_code == 400
    assert unknown.text == "unsupported target"
    assert future.status_code == 400
    assert future.text == "unsupported target"
    assert incompatible.status_code == 400
    assert incompatible.text == "target does not support companion"


@pytest.mark.asyncio
async def test_auto_route_does_not_double_decode_query_target(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        once_decoded = client.get(f"{path}?target=clash%2Dmeta")
        double_encoded = client.get(f"{path}?target=clash%252Dmeta")

    assert once_decoded.status_code == 200
    assert "proxies:" in once_decoded.text
    assert double_encoded.status_code == 400
    assert double_encoded.text == "unsupported target"


def test_auto_route_access_runs_before_target_validation_and_cache_read(tmp_path) -> None:
    config = auto_app_config(tmp_path, allowed_user_agents=("mihomo/*",))
    app = create_app(
        config,
        cache_store=ExplodingCacheStore(),
        refresher=FakeRefresher(),
        scheduler=None,
    )
    path = config.routes["phone"].path

    with TestClient(app) as client:
        bad_target = client.get(
            f"{path}?target=not-a-client",
            headers={"User-Agent": "blocked/1.0"},
        )
        bad_companion = client.get(
            f"{path}-nodes?target=quanx",
            headers={"User-Agent": "blocked/1.0"},
        )

    assert bad_target.status_code == 403
    assert bad_companion.status_code == 403


@pytest.mark.asyncio
async def test_auto_route_embeds_canonical_public_urls(tmp_path) -> None:
    config = auto_app_config(tmp_path)
    store = JsonSourceCacheStore(config.cache)
    await store.set("airport_a", source_cache_with_nodes(ss_node()))
    app = create_app(config, cache_store=store, refresher=None, scheduler=None)
    path = config.routes["phone"].path

    with TestClient(app) as client:
        surfboard = client.get(f"{path}?format=surfboard")
        qx_import = client.get(f"{path}-import?target=auto", follow_redirects=False)

    assert "policy-path=https://mpm.example.com" in surfboard.text
    assert f"{path}-nodes?target=surfboard" in surfboard.text
    assert qx_import.status_code == 302
    assert "target%3Dquanx" in qx_import.headers["location"]
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
rtk pytest tests/test_app.py -q
```

Expected: `KeyError: 'auto'` from `renderers[route.output.format]` or route behavior assertions fail.

- [ ] **Step 3: Update app imports**

In `src/mihomo_proxy_manager/app.py`, add:

```python
from dataclasses import replace

from .route_targets import (
    COMPANION_TARGETS,
    IMPLEMENTED_FORMATS,
    canonical_target_for_format,
    has_future_user_agent_signal,
    resolve_query_selection,
    resolve_user_agent_format,
)
```

If `replace` is already imported, reuse the existing import.

- [ ] **Step 4: Add small helper functions inside `create_app()`**

Add helpers after `_public_url()`:

```python
    def _public_url_with_target(path: str, output_format: str) -> str:
        separator = "&" if "?" in path else "?"
        target = canonical_target_for_format(output_format)
        return f"{_public_url(path)}{separator}target={target}"

    def _query_values(request, key: str) -> list[str]:
        return request.query_params.getlist(key)

    def _query_selection(request):
        return resolve_query_selection(
            {
                "target": _query_values(request, "target"),
                "format": _query_values(request, "format"),
                "flag": _query_values(request, "flag"),
                "client": _query_values(request, "client"),
            }
        )

    def _effective_output_format(route, companion: str | None, request):
        if route.output.format != "auto":
            return route.output.format, None

        selection = _query_selection(request)
        if selection.explicit:
            if selection.format not in IMPLEMENTED_FORMATS:
                return None, "unsupported target"
            if companion and COMPANION_TARGETS.get(companion) != selection.format:
                return None, "target does not support companion"
            return selection.format, None

        if companion:
            implied = COMPANION_TARGETS.get(companion)
            if implied is None:
                return None, "target does not support companion"
            return implied, None

        ua_format = resolve_user_agent_format(request.headers.get("user-agent"))
        if ua_format is None and has_future_user_agent_signal(
            request.headers.get("user-agent")
        ):
            logger.warning(
                "auto route future User-Agent target ignored: route={route} "
                "user_agent={user_agent} fallback={fallback}",
                route=route.name,
                user_agent=sanitize_user_agent(request.headers.get("user-agent")),
                fallback=route.output.auto_default,
            )
        return ua_format or route.output.auto_default, None

    def _render_route_for_format(route, output_format: str):
        return replace(route, output=replace(route.output, format=output_format))

    def _main_public_url(route, output_format: str) -> str:
        if route.output.format == "auto":
            return _public_url_with_target(route.path, output_format)
        return _public_url(route.path)

    def _companion_public_urls(route) -> dict[str, str]:
        if route.output.format == "auto":
            urls = {"nodes": _public_url_with_target(f"{route.path}-nodes", "surfboard")}
            if route.output.import_link:
                urls["import"] = _public_url_with_target(route.path, "quantumult-x")
            return urls
        return companion_public_urls_by_route.get(route.name, {})
```

During implementation, keep helper return types explicit if `ty` needs them.

- [ ] **Step 5: Register auto paths without renderer lookup**

Change route registration loop in `create_app()`:

```python
        if route.output.format == "auto":
            companion_paths = []
            route_companion_urls = {
                "nodes": _public_url_with_target(f"{route.path}-nodes", "surfboard")
            }
            companion_paths.append((f"{route.path}-nodes", "nodes"))
            if route.output.import_link:
                companion_paths.append((f"{route.path}-import", "import"))
                route_companion_urls["import"] = _public_url_with_target(
                    route.path, "quantumult-x"
                )
        else:
            route_companion_urls = {}
            renderer = renderers[route.output.format]
            companion_paths = []
            for companion_path in renderer.companion_paths(route):
                prefix = f"{route.path}-"
                companion = (
                    companion_path[len(prefix) :]
                    if companion_path.startswith(prefix)
                    else companion_path
                )
                companion_paths.append((companion_path, companion))
                route_companion_urls[companion] = _public_url(companion_path)

        for companion_path, companion in companion_paths:
            route_by_path[companion_path] = (route, companion)
        companion_public_urls_by_route[route.name] = route_companion_urls
```

Keep `route_by_path[route.path] = (route, None)` before this block.

- [ ] **Step 6: Resolve target after access and before cache fetch**

In the request handler, keep the existing access check before reading cache. Immediately after access passes, add:

```python
        output_format, target_error = _effective_output_format(route, companion, request)
        if target_error is not None or output_format is None:
            return PlainTextResponse(target_error or "unsupported target", status_code=400)
```

At render time, use:

```python
        render_route = _render_route_for_format(route, output_format)
        renderer = renderers[output_format]
        response = renderer.render(
            RenderRequest(
                render_route,
                records,
                request_base_url=str(request.base_url),
                main_public_url=_main_public_url(route, output_format),
                companion_public_urls=_companion_public_urls(route),
                companion=companion,
            )
        )
```

Do not mutate `route.output`.

- [ ] **Step 7: Run app tests**

Run:

```bash
rtk pytest tests/test_app.py -q
```

Expected: all app tests pass.

- [ ] **Step 8: Run typecheck on touched app path**

Run:

```bash
rtk make typecheck
```

Expected: no `ty` diagnostics. If `ty` rejects helper inference, add explicit annotations using `RouteConfig`, `Request`, and `Literal` compatible locals.

- [ ] **Step 9: Commit**

Run:

```bash
rtk git add src/mihomo_proxy_manager/app.py tests/test_app.py
rtk git commit -m "feat(app): resolve auto route targets"
```

## Task 4: Documentation And Examples

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/route-formats.md`
- Modify: `examples/config.toml`

- [ ] **Step 1: Update examples config**

In `examples/config.toml`, add a real auto route with `public_base_url` already present:

```toml
[routes.auto]
path = "/p/auto-CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL"
sources = ["airport_a", "airport_b"]

[routes.auto.output]
format = "auto"
auto_default = "provider"
import_link = true
resource_tag = "MPM Auto"
encoding = "base64"
```

Keep existing fixed v2rayN/QX/Surfboard routes so users can compare fixed vs auto behavior.

- [ ] **Step 2: Update Chinese README**

In `README.md`, update the route output section to include:

```markdown
### 单 URL 自动订阅

`format = "auto"` 只对该 route 开启自动格式选择。固定格式 route 仍忽略
`target`、`format`、`flag`、`client` 查询参数和 `User-Agent`。

```toml
[routes.auto.output]
format = "auto"
auto_default = "provider"
```

同一路径可供不同客户端直接订阅：

```text
https://mpm.example.com/p/token?target=clash
https://mpm.example.com/p/token?target=surfboard
https://mpm.example.com/p/token?target=quanx
https://mpm.example.com/p/token?target=v2rayn
```

查询优先级为 `target > format > flag > client`，整体选择顺序为
`explicit query target > companion suffix > User-Agent > auto_default`。
`target=auto` 或空值表示没有显式查询目标；此时 `-nodes` 仍选 Surfboard，
`-import` 仍选 Quantumult X。

`format = "auto"` 必须配置 `server.public_base_url`，因为 Surfboard 和
Quantumult X 需要嵌入绝对订阅 URL。
```

- [ ] **Step 3: Update English README**

In `README_EN.md`, add the matching English section:

```markdown
### One URL For Multiple Clients

`format = "auto"` enables per-request format selection only for that route.
Fixed-format routes still ignore `target`, `format`, `flag`, `client`, and
`User-Agent`.

```toml
[routes.auto.output]
format = "auto"
auto_default = "provider"
```

Clients can subscribe to the same route with different query targets:

```text
https://mpm.example.com/p/token?target=clash
https://mpm.example.com/p/token?target=surfboard
https://mpm.example.com/p/token?target=quanx
https://mpm.example.com/p/token?target=v2rayn
```

Query selector priority is `target > format > flag > client`; overall target
resolution is `explicit query target > companion suffix > User-Agent >
auto_default`. `target=auto` or a blank value means no explicit query target,
so `-nodes` still selects Surfboard and `-import` still selects Quantumult X.

Every auto route requires `server.public_base_url` because Surfboard and
Quantumult X embed absolute subscription URLs.
```

- [ ] **Step 4: Update route format reference**

In `docs/route-formats.md`, update the implemented output table and config reference with:

```markdown
| Auto per-request subscription | Implemented route output | `auto` | Query/UA-selected `provider`, `surfboard`, `quantumult-x`, or `xray-uri` | Query selectors: `target`, `format`, `flag`, `client`; fixed routes are unchanged. |
```

Add a subsection:

```markdown
## Auto Route Target Selection

`format = "auto"` is opt-in per route. It supports implemented targets:

| Target aliases | Effective output |
| --- | --- |
| `clash`, `clash-meta`, `clash.meta`, `provider`, `mihomo`, `meta` | `provider` |
| `surfboard` | `surfboard` |
| `quanx`, `qx`, `quantumult-x`, `quantumult x` | `quantumult-x` |
| `v2rayn`, `v2rayng`, `v2ray`, `xray`, `xray-uri`, `general` | `xray-uri` |

Reserved future aliases such as `singbox`, `sing-box`, `sfa`, `sfi`, `sfm`,
`hiddify`, and `loon` return `400 unsupported target` until renderers exist.

Alias matching receives the value after the HTTP parser's normal one-time percent
decoding; the resolver must not decode again. It trims leading and trailing
whitespace, is case-insensitive, normalizes `_` to `-`, and keeps `.` significant
(`clash.meta` remains distinct from `clash-meta` before alias lookup).

Selection order:

```text
explicit query target > companion suffix > User-Agent > auto_default
```

Canonical embedded URLs always use `?target=surfboard`, `?target=quanx`,
`?target=v2rayn`, or `?target=clash` so imported client resources do not depend
on the fetching client's `User-Agent`.
```

- [ ] **Step 5: Run docs checks**

Run:

```bash
rtk grep -n "format = \"auto\"" README.md README_EN.md docs/route-formats.md examples/config.toml
rtk grep -n "target=surfboard" README.md README_EN.md docs/route-formats.md
rtk make check
```

Expected: each file has the new auto-route section or example, and `make check`
passes including the examples config validation. If `make check` is unavailable,
run `rtk uv run mpm check -c examples/config.toml` and record the output.

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add README.md README_EN.md docs/route-formats.md examples/config.toml
rtk git commit -m "docs(route): document auto subscriptions"
```

## Task 5: Final Verification

**Files:**
- No source edits unless verification finds a defect.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
rtk pytest tests/test_config.py -q
rtk pytest tests/test_route_targets.py -q
rtk pytest tests/test_app.py -q
rtk pytest tests/test_render.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run typecheck and lint**

Run:

```bash
rtk make typecheck
rtk make lint
```

Expected: `uv run ty check` exits 0 with no diagnostics, and lint exits 0.

- [ ] **Step 3: Run full test suite**

Run:

```bash
rtk pytest -q
```

Expected: full suite passes.

- [ ] **Step 4: Review git status and diff**

Run:

```bash
rtk git status --short
rtk git log --oneline -5
rtk git diff --check -- docs/superpowers/plans/2026-06-21-auto-route-target.md
rtk grep -n "[T]BD\\|[T]ODO\\|[i]mplement later\\|[f]ill in details\\|[t]ype: ignore" docs/superpowers/plans/2026-06-21-auto-route-target.md
```

Expected: clean working tree after task commits; recent commits include config,
resolver, app, and docs commits. `git diff --check` prints no whitespace errors.
The placeholder grep prints no matches; if it finds a real placeholder or
the forbidden type-ignore marker, remove it instead of reporting completion.

- [ ] **Step 5: Final review gate**

Dispatch two reviewers:

- Spec compliance reviewer: compare implementation against `docs/superpowers/specs/2026-06-20-auto-route-target-design.md`.
- Code quality reviewer: inspect branch diff since `d30dbe5 docs(route): tighten auto target spec`.

Fix any Critical or Important findings before reporting completion.

## Self-Review

- Spec coverage: model/config, query priority, blank/auto handling, alias matching, UA matching, future targets, companion suffix priority, access ordering, canonical embedded URLs, docs/examples, typecheck are all mapped to tasks.
- Placeholder scan: no deferred implementation markers or open-ended "add appropriate handling"; each implementation step names exact behavior and tests.
- Type consistency: plan uses existing `RouteOutputConfig`, `RenderRequest`, renderer registry, and Starlette `request.query_params.getlist()` shapes. New resolver uses plain `Mapping[str, Sequence[str]]` so app and tests can call it without Starlette dependency.
