"""Route output target selection for auto routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

SELECTOR_KEYS = ("target", "format", "flag", "client")
IMPLEMENTED_FORMATS = {"provider", "surfboard", "quantumult-x", "xray-uri"}
FUTURE_FORMATS = {"sing-box", "loon"}
COMPANION_TARGETS = {"nodes": "surfboard", "import": "quantumult-x"}

TARGET_ALIASES = {
    "provider": "provider",
    "clash": "provider",
    "mihomo": "provider",
    "clash-meta": "provider",
    "clash.meta": "provider",
    "meta": "provider",
    "xray-uri": "xray-uri",
    "xray": "xray-uri",
    "v2ray": "xray-uri",
    "v2rayn": "xray-uri",
    "v2rayng": "xray-uri",
    "general": "xray-uri",
    "quantumult-x": "quantumult-x",
    "quanx": "quantumult-x",
    "qx": "quantumult-x",
    "quantumult x": "quantumult-x",
    "surfboard": "surfboard",
    "sing-box": "sing-box",
    "singbox": "sing-box",
    "sfa": "sing-box",
    "sfi": "sing-box",
    "sfm": "sing-box",
    "hiddify": "sing-box",
    "loon": "loon",
}

CANONICAL_TARGETS = {
    "provider": "clash",
    "surfboard": "surfboard",
    "quantumult-x": "quanx",
    "xray-uri": "v2rayn",
}

UA_SIGNALS = (
    ("quantumult%20x", "quantumult-x"),
    ("quantumult x", "quantumult-x"),
    ("quantumult-x", "quantumult-x"),
    ("surfboard", "surfboard"),
    ("v2rayn", "xray-uri"),
    ("v2rayng", "xray-uri"),
    ("v2ray", "xray-uri"),
    ("clash", "provider"),
    ("mihomo", "provider"),
    ("flclash", "provider"),
    ("clash-verge", "provider"),
    ("meta", "provider"),
)

FUTURE_UA_SIGNALS = (
    ("sing-box", "sing-box"),
    ("singbox", "sing-box"),
    ("hiddify", "sing-box"),
    ("sfa", "sing-box"),
    ("sfi", "sing-box"),
    ("sfm", "sing-box"),
    ("loon", "loon"),
)


@dataclass(frozen=True)
class QuerySelection:
    format: str | None
    explicit: bool
    unsupported: str | None = None


def normalize_target_alias(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def resolve_query_selection(
    values_by_key: Mapping[str, Sequence[str]],
) -> QuerySelection:
    for key in SELECTOR_KEYS:
        values = values_by_key.get(key)
        if not values:
            continue
        alias = normalize_target_alias(values[0])
        if alias == "" or alias == "auto":
            return QuerySelection(format=None, explicit=False)
        target = TARGET_ALIASES.get(alias)
        if target is None:
            return QuerySelection(format=None, explicit=True, unsupported=alias)
        return QuerySelection(format=target, explicit=True)
    return QuerySelection(format=None, explicit=False)


def resolve_user_agent_format(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    lowered = user_agent.lower()
    for signal, output_format in UA_SIGNALS:
        if signal in lowered:
            return output_format
    return None


def has_future_user_agent_signal(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    lowered = user_agent.lower()
    return any(signal in lowered for signal, _ in FUTURE_UA_SIGNALS)


def canonical_target_for_format(output_format: str) -> str:
    return CANONICAL_TARGETS[output_format]
