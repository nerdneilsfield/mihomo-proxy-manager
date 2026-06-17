"""构建 JSON 格式的状态响应，包含每个源的刷新状态和节点信息。

Build JSON status response with per-source refresh state and node info.
"""

from __future__ import annotations

from .cache import SourceCacheStore
from .security import redact_secret


async def build_status(
    cache_store: SourceCacheStore,
    source_names: list[str],
    *,
    extra_secrets: list[str] | None = None,
) -> dict[str, object]:
    """构建所有订阅源的状态字典。

    Build a status dictionary for all sources.

    Args:
        cache_store: 缓存存储实例 / Cache store instance.
        source_names: 订阅源名称列表 / List of source names.
        extra_secrets: 额外的敏感字符串列表，用于脱敏错误信息 / Additional sensitive strings for error redaction.

    Returns:
        包含 sources 列表的状态字典 / Status dict containing a sources list.
    """
    sources = []
    for name in source_names:
        status = await cache_store.status(name)
        sources.append(
            {
                "source": status.source,
                "last_attempt_at": status.last_attempt_at.isoformat() if status.last_attempt_at else None,
                "last_success_at": status.last_success_at.isoformat() if status.last_success_at else None,
                "node_count": status.node_count,
                "last_error": redact_secret(status.last_error, extra_secrets=extra_secrets) if status.last_error else None,
                "refreshing": status.refreshing,
            }
        )
    return {"sources": sources}
