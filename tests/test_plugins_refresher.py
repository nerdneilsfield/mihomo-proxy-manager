import httpx
import pytest

from mihomo_proxy_manager.models import HttpConfig, PluginConfig
from mihomo_proxy_manager.fetcher import SafeHttpClient
from mihomo_proxy_manager.plugins.http_action import HttpActionPlugin, PluginContext


@pytest.mark.asyncio
async def test_http_action_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plugin = HttpActionPlugin(SafeHttpClient(client, HttpConfig(__import__("datetime").timedelta(seconds=30), "ua", 1024, 3)))
    config = PluginConfig(
        name="turn_on",
        type="http_action",
        method="POST",
        url="https://93.184.216.34/switch",
        headers={},
        success_status=(204,),
        timeout=__import__("datetime").timedelta(seconds=10),
        allow_private_network=False,
    )

    result = await plugin.run(PluginContext(source_name="airport_a", plugin=config))

    assert result.ok
