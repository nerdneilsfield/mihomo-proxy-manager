"""pytest 共享 fixtures。

Shared pytest fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest


CLASH_CONFIG_TEMPLATE_BODY = """\
port: 7890
mode: rule

proxies:
{{proxies}}

proxy-groups:
  - name: Proxy
    type: select
    proxies:
      - DIRECT
      {{proxy_names}}

rules:
  - MATCH,Proxy
"""


def write_clash_template(
    directory: Path,
    *,
    body: str = CLASH_CONFIG_TEMPLATE_BODY,
    name: str = "clash.tpl.yaml",
) -> Path:
    """Write the canonical Clash template body to ``directory`` for tests."""
    template = directory / name
    template.write_text(body, encoding="utf-8")
    return template


@pytest.fixture
def sample_proxy() -> dict[str, object]:
    """提供一个示例代理字典用于测试。

    Provide a sample proxy dictionary for testing.

    Returns:
        dict[str, object]: 包含 vmess 代理信息的字典 / A dict with vmess proxy info.
    """
    return {
        "name": "HK 01",
        "type": "vmess",
        "server": "example.com",
        "port": 443,
        "uuid": "00000000-0000-0000-0000-000000000000",
        "cipher": "auto",
    }


@pytest.fixture
def temp_config_path(tmp_path: Path) -> Path:
    """提供一个临时配置文件路径。

    Provide a temporary config file path.

    Args:
        tmp_path: pytest 提供的临时目录 / pytest-provided temporary directory.

    Returns:
        Path: 指向 config.toml 的路径 / Path to config.toml.
    """
    return tmp_path / "config.toml"
