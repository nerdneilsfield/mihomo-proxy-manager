from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_proxy() -> dict[str, object]:
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
    return tmp_path / "config.toml"
