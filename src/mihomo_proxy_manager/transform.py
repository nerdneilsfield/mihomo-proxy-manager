from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy

from .models import FilterConfig, ProxyRecord, RenameConfig


def _matches_name(name: str, pattern: str | None) -> bool:
    return bool(pattern and re.search(pattern, name))


def _matches_type(proxy_type: str, types: tuple[str, ...]) -> bool:
    wanted = {item.lower() for item in types}
    return proxy_type.lower() in wanted


def _kept(record: ProxyRecord, config: FilterConfig) -> bool:
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
    return value.replace("{source}", record.source)


def apply_transform(
    records: list[ProxyRecord],
    *,
    filter_config: FilterConfig,
    rename_config: RenameConfig,
) -> list[ProxyRecord]:
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
    remaining_original = Counter(str(record.data.get("name", "")) for record in records)
    used: set[str] = set()
    output: list[ProxyRecord] = []
    for record in records:
        data = deepcopy(record.data)
        base = str(data.get("name", ""))
        remaining_original[base] -= 1
        candidate = base
        counter = 2
        while candidate in used or (candidate != base and remaining_original[candidate] > 0):
            candidate = f"{base} #{counter}"
            counter += 1
        data["name"] = candidate
        used.add(candidate)
        output.append(ProxyRecord(source=record.source, data=data))
    return output
