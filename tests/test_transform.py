from mihomo_proxy_manager.models import FilterConfig, ProxyRecord, RenameConfig
from mihomo_proxy_manager.transform import apply_transform, repair_duplicate_names


def records() -> list[ProxyRecord]:
    return [
        ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"}),
        ProxyRecord("airport_a", {"name": "JP 01", "type": "ss"}),
        ProxyRecord("airport_a", {"name": "官网", "type": "http"}),
    ]


def test_filters_by_name_and_type() -> None:
    result = apply_transform(
        records(),
        filter_config=FilterConfig(include="HK|JP", exclude="官网", exclude_types=("http",)),
        rename_config=RenameConfig(),
    )

    assert [item.data["name"] for item in result] == ["HK 01", "JP 01"]


def test_renames_with_source_template() -> None:
    result = apply_transform(
        [ProxyRecord("airport_a", {"name": "HK 01", "type": "vmess"})],
        filter_config=FilterConfig(),
        rename_config=RenameConfig(prefix="[{source}] ", suffix=" | auto"),
    )

    assert result[0].data["name"] == "[airport_a] HK 01 | auto"
    assert result[0].source == "airport_a"


def test_duplicate_name_repair_is_iterative() -> None:
    result = repair_duplicate_names(
        [
            ProxyRecord("a", {"name": "HK", "type": "vmess"}),
            ProxyRecord("b", {"name": "HK", "type": "vmess"}),
            ProxyRecord("c", {"name": "HK #2", "type": "vmess"}),
        ]
    )

    assert [item.data["name"] for item in result] == ["HK", "HK #3", "HK #2"]
