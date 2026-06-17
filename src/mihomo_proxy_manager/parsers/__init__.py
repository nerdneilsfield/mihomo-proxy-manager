from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

from mihomo_proxy_manager.models import ProxyRecord

from .share_links import parse_share_links_text
from .yaml import parse_yaml_subscription


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParseResult:
    records: list[ProxyRecord]
    warnings: list[str]


def _decode_text(body: bytes) -> str:
    return body.decode("utf-8-sig")


def _try_base64_text(body: bytes) -> str:
    raw = body.strip()
    padding = b"=" * (-len(raw) % 4)
    return base64.b64decode(raw + padding).decode("utf-8-sig")


def _finalize(records: list[ProxyRecord], warnings: list[str], *, parse_error: Literal["skip", "fail"]) -> ParseResult:
    if parse_error == "fail" and warnings:
        raise ParseError("; ".join(warnings))
    if not records:
        raise ParseError("; ".join(warnings) if warnings else "no usable proxies")
    return ParseResult(records=records, warnings=warnings)


def parse_subscription(
    body: bytes,
    *,
    source: str,
    fmt: Literal["auto", "yaml", "share-links"],
    parse_error: Literal["skip", "fail"],
) -> ParseResult:
    if fmt in {"auto", "yaml"}:
        try:
            records, warnings = parse_yaml_subscription(body, source=source)
            return _finalize(records, warnings, parse_error=parse_error)
        except Exception:
            if fmt == "yaml":
                raise

    if fmt in {"auto", "share-links"}:
        records, warnings = parse_share_links_text(_decode_text(body), source=source)
        if records or fmt == "share-links":
            return _finalize(records, warnings, parse_error=parse_error)

    if fmt == "auto":
        records, warnings = parse_share_links_text(_try_base64_text(body), source=source)
        return _finalize(records, warnings, parse_error=parse_error)

    raise ParseError("unsupported subscription format")
