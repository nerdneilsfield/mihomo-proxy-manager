"""Route access-control helpers."""

from __future__ import annotations

import fnmatch
import re

from .models import RouteAccessConfig

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


def user_agent_allowed(config: RouteAccessConfig, user_agent: str | None) -> bool:
    if not config.user_agent:
        return True
    if not user_agent:
        return False
    return any(fnmatch.fnmatchcase(user_agent, pattern) for pattern in config.user_agent)


def sanitize_user_agent(value: str | None, *, limit: int = 200) -> str:
    if not value:
        return "<missing>"
    sanitized = _CONTROL_CHARS_RE.sub(" ", value).strip()
    if len(sanitized) > limit:
        return sanitized[:limit] + "...<truncated>"
    return sanitized
