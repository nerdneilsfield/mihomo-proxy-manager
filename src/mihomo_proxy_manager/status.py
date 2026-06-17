from __future__ import annotations

from .cache import SourceCacheStore
from .security import redact_secret


async def build_status(
    cache_store: SourceCacheStore,
    source_names: list[str],
    *,
    extra_secrets: list[str] | None = None,
) -> dict[str, object]:
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
