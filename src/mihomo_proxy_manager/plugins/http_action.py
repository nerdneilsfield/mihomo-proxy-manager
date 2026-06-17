from __future__ import annotations

from dataclasses import dataclass

from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.models import PluginConfig
from mihomo_proxy_manager.security import redact_secret


@dataclass(frozen=True)
class PluginContext:
    source_name: str
    plugin: PluginConfig


@dataclass(frozen=True)
class PluginResult:
    ok: bool
    message: str | None = None


class HttpActionPlugin:
    def __init__(self, safe_http: SafeHttpClient) -> None:
        self.safe_http = safe_http

    async def run(self, context: PluginContext) -> PluginResult:
        plugin = context.plugin
        try:
            response = await self.safe_http.request(
                plugin.method,
                plugin.url,
                headers=plugin.headers,
                timeout=plugin.timeout.total_seconds(),
                allow_private_network=plugin.allow_private_network,
                body=plugin.body,
            )
            if response.status_code not in plugin.success_status:
                return PluginResult(False, f"unexpected status {response.status_code}")
            return PluginResult(True)
        except Exception as exc:
            return PluginResult(False, redact_secret(str(exc)))
