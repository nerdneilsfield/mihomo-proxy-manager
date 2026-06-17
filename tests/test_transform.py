"""代理过滤和重命名转换测试。

Proxy filtering and renaming transform tests.
"""

import re

import pytest

from mihomo_proxy_manager.models import FilterConfig, ProxyRecord, RenameConfig
from mihomo_proxy_manager.transform import apply_transform, repair_duplicate_names


def records() -> list[ProxyRecord]:
    """返回一组测试用的代理记录。

    Returns a set of test proxy records.

    Returns:
        包含三个代理记录的列表，分别来自 airport_a，类型为 vmess、ss、http。
        A list of three proxy records from airport_a with types vmess, ss, http.
    """
    return [
        ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"}),
        ProxyRecord("airport_a", {"name": "JP 01", "type": "ss"}),
        ProxyRecord("airport_a", {"name": "官网", "type": "http"}),
    ]


def test_filters_by_name_and_type() -> None:
    """测试按名称和类型过滤代理。

    Test filtering proxies by name and type.

    include 匹配 "HK" 或 "JP"，exclude 排除 "官网"，exclude_types 排除 http 类型。
    include matches "HK" or "JP", exclude removes "官网", exclude_types drops http.
    """
    result = apply_transform(
        records(),
        filter_config=FilterConfig(include="HK|JP", exclude="官网", exclude_types=("http",)),
        rename_config=RenameConfig(),
    )

    assert [item.data["name"] for item in result] == ["HK 01", "JP 01"]


def test_renames_with_source_template() -> None:
    """测试使用 {source} 模板重命名代理。

    Test renaming proxies with the {source} template.

    前缀和后缀分别插入代理名称前后，source 字段应保留原始来源标识。
    Prefix and suffix are inserted around the proxy name; the source field should retain the original identifier.
    """
    result = apply_transform(
        [ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"})],
        filter_config=FilterConfig(),
        rename_config=RenameConfig(prefix="[{source}] ", suffix=" | auto"),
    )

    assert result[0].data["name"] == "[airport_a] HK 01 | auto"
    assert result[0].source == "airport_a"


def test_duplicate_name_repair_is_iterative() -> None:
    """测试重复名称修复是迭代的。

    Test that duplicate name repair is iterative.

    当已存在 "HK #2" 时，第二个 "HK" 应被重命名为 "HK #3" 而非 "HK #2"。
    When "HK #2" already exists, the second "HK" should be renamed to "HK #3" instead of "HK #2".
    """
    result = repair_duplicate_names(
        [
            ProxyRecord("a", {"name": "HK", "type": "vmess"}),
            ProxyRecord("b", {"name": "HK", "type": "vmess"}),
            ProxyRecord("c", {"name": "HK #2", "type": "vmess"}),
        ]
    )

    assert [item.data["name"] for item in result] == ["HK", "HK #3", "HK #2"]


def test_empty_input_returns_empty() -> None:
    """测试空输入返回空列表。

    Test that empty input returns an empty list.
    """
    result = apply_transform(
        [],
        filter_config=FilterConfig(),
        rename_config=RenameConfig(),
    )
    assert result == []


def test_all_records_excluded_returns_empty() -> None:
    """测试所有记录被排除后返回空列表。

    Test that excluding all records returns an empty list.

    先按类型只保留 ss，再按正则排除所有名称，两种场景均应返回空。
    First keep only ss by type, then exclude all names by regex; both scenarios should return empty.
    """
    result = apply_transform(
        records(),
        filter_config=FilterConfig(include_types=("ss",)),
        rename_config=RenameConfig(),
    )
    assert [item.data["type"] for item in result] == ["ss"]

    result = apply_transform(
        records(),
        filter_config=FilterConfig(exclude=".*"),
        rename_config=RenameConfig(),
    )
    assert result == []


def test_invalid_regex_raises() -> None:
    """测试无效正则表达式抛出 re.error。

    Test that an invalid regex pattern raises re.error.
    """
    with pytest.raises(re.error):
        apply_transform(
            records(),
            filter_config=FilterConfig(include="["),
            rename_config=RenameConfig(),
        )
