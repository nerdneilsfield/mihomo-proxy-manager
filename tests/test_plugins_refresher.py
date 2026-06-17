import httpx
import pytest

from typing import cast

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


@pytest.mark.asyncio
async def test_http_action_redacts_secrets_in_error_message() -> None:
    class RaisingSafeHttp:
        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            raise ValueError("request to https://example.com/switch?token=secret failed")

    plugin = HttpActionPlugin(cast(SafeHttpClient, RaisingSafeHttp()))
    config = PluginConfig(
        name="turn_on",
        type="http_action",
        method="POST",
        url="https://example.com/switch?token=secret",
        headers={},
        success_status=(204,),
        timeout=__import__("datetime").timedelta(seconds=10),
        allow_private_network=False,
    )

    result = await plugin.run(PluginContext(source_name="airport_a", plugin=config))

    assert not result.ok
    assert result.message is not None
    assert "token=secret" not in result.message
    assert "token=***" in result.message
