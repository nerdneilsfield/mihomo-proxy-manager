"""代理记录的过滤（按名称/类型）和重命名（前缀/后缀）转换。

Filtering (by name/type) and renaming (prefix/suffix) transforms for proxy records.
"""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy

from .models import FilterConfig, ProxyRecord, RenameConfig


def _matches_name(name: str, pattern: str | None) -> bool:
    """检查代理名称是否匹配正则模式。

    Check if a proxy name matches a regex pattern.

    Args:
        name: 代理名称 / Proxy name.
        pattern: 正则表达式模式 / Regex pattern.

    Returns:
        如果匹配返回 True / True if matches.
    """
    return bool(pattern and re.search(pattern, name))


def _matches_type(proxy_type: str, types: tuple[str, ...]) -> bool:
    """检查代理类型是否在指定类型列表中。

    Check if a proxy type is in the specified type list.

    Args:
        proxy_type: 代理类型 / Proxy type.
        types: 类型元组 / Tuple of types.

    Returns:
        如果匹配返回 True / True if matches.
    """
    wanted = {item.lower() for item in types}
    return proxy_type.lower() in wanted


def _kept(record: ProxyRecord, config: FilterConfig) -> bool:
    """根据过滤配置判断代理记录是否保留。

    Determine whether a proxy record should be kept based on filter config.

    Args:
        record: 代理记录 / Proxy record.
        config: 过滤配置 / Filter configuration.

    Returns:
        如果保留返回 True / True if kept.
    """
    name = str(record.data.get("name", ""))
    proxy_type = str(record.data.get("type", ""))
    if config.include and not _matches_name(name, config.include):
        return False
    if config.exclude and _matches_name(name, config.exclude):
        return False
    if config.include_types and not _matches_type(proxy_type, config.include_types):
        return False
    if config.exclude_types and _matches_type(proxy_type, config.exclude_types):
        return False
    return True


def _render_template(value: str, record: ProxyRecord) -> str:
    """渲染模板字符串，替换 {source} 占位符。

    Render a template string, replacing the {source} placeholder.

    Args:
        value: 模板字符串 / Template string.
        record: 代理记录 / Proxy record.

    Returns:
        渲染后的字符串 / Rendered string.
    """
    return value.replace("{source}", record.source)


def apply_transform(
    records: list[ProxyRecord],
    *,
    filter_config: FilterConfig,
    rename_config: RenameConfig,
) -> list[ProxyRecord]:
    """对代理记录应用过滤和重命名转换。

    Apply filtering and renaming transforms to proxy records.

    Args:
        records: 代理记录列表 / List of proxy records.
        filter_config: 过滤配置 / Filter configuration.
        rename_config: 重命名配置 / Rename configuration.

    Returns:
        转换后的代理记录列表 / Transformed list of proxy records.
    """
    output: list[ProxyRecord] = []
    for record in records:
        if not _kept(record, filter_config):
            continue
        data = deepcopy(record.data)
        old_name = str(data.get("name", ""))
        prefix = _render_template(rename_config.prefix, record)
        suffix = _render_template(rename_config.suffix, record)
        data["name"] = f"{prefix}{old_name}{suffix}"
        output.append(ProxyRecord(source=record.source, data=data))
    return output


def repair_duplicate_names(records: list[ProxyRecord]) -> list[ProxyRecord]:
    """修复重复的代理名称，通过添加编号后缀消除冲突。

    Repair duplicate proxy names by appending numeric suffixes to resolve conflicts.

    Args:
        records: 代理记录列表 / List of proxy records.

    Returns:
        名称唯一化后的代理记录列表 / Proxy records with deduplicated names.
    """
    remaining_original = Counter(str(record.data.get("name", "")) for record in records)
    used: set[str] = set()
    output: list[ProxyRecord] = []
    for record in records:
        data = deepcopy(record.data)
        base = str(data.get("name", ""))
        remaining_original[base] -= 1
        candidate = base
        counter = 2
        while candidate in used or (
            candidate != base and remaining_original[candidate] > 0
        ):
            candidate = f"{base} #{counter}"
            counter += 1
        data["name"] = candidate
        used.add(candidate)
        output.append(ProxyRecord(source=record.source, data=data))
    return output
