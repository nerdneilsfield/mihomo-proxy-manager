"""HTTP Action 插件，在抓取订阅前执行外部 HTTP 请求。

HTTP Action plugin that executes external HTTP requests before fetching subscriptions.
"""

from __future__ import annotations

from dataclasses import dataclass

from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.models import PluginConfig
from mihomo_proxy_manager.security import redact_secret


@dataclass(frozen=True)
class PluginContext:
    """插件上下文，包含来源名称和插件配置。

    Plugin context containing source name and plugin configuration.
    """

    source_name: str
    plugin: PluginConfig


@dataclass(frozen=True)
class PluginResult:
    """插件执行结果，包含成功状态和可选消息。

    Plugin execution result containing success status and optional message.
    """

    ok: bool
    message: str | None = None


class HttpActionPlugin:
    """HTTP Action 插件，在抓取订阅前执行外部 HTTP 请求。

    HTTP Action plugin that executes external HTTP requests before fetching subscriptions.
    """

    def __init__(self, safe_http: SafeHttpClient) -> None:
        """初始化 HttpActionPlugin。

        Initialize HttpActionPlugin.

        Args:
            safe_http: 安全的 HTTP 客户端实例 / Safe HTTP client instance.
        """
        self.safe_http = safe_http

    async def run(self, context: PluginContext) -> PluginResult:
        """执行插件，发送 HTTP 请求并检查响应状态码。

        Execute the plugin, send an HTTP request and check the response status code.

        Args:
            context: 插件上下文 / Plugin context.

        Returns:
            插件执行结果 / Plugin execution result.
        """
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
