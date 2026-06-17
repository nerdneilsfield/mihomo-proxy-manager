from __future__ import annotations

from datetime import UTC, datetime

import yaml

from .models import ProxyRecord, RouteConfig
from .transform import apply_transform, repair_duplicate_names


class ProviderRenderer:
    def __init__(self, *, yaml_sort_keys: bool) -> None:
        self.yaml_sort_keys = yaml_sort_keys

    def render_sync(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        transformed = apply_transform(records, filter_config=route.filter, rename_config=route.rename)
        repaired = repair_duplicate_names(transformed)
        proxies = [dict(record.data) for record in repaired]
        payload = {"proxies": proxies}
        body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=self.yaml_sort_keys).encode("utf-8")
        if route.output.include_meta_comments:
            prefix = (
                f"# generated_at: {datetime.now(UTC).isoformat()}\n"
                f"# route: {route.name}\n"
                f"# nodes: {len(proxies)}\n"
            ).encode("utf-8")
            return prefix + body
        return body

    async def render(self, route: RouteConfig, records: list[ProxyRecord]) -> bytes:
        return self.render_sync(route, records)
