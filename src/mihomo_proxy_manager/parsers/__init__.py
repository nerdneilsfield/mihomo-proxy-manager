from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Literal

from yaml import YAMLError

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
    padded = raw + padding
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8-sig")
    except (binascii.Error, ValueError):
        return base64.b64decode(padded).decode("utf-8-sig")


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
    yaml_error: Exception | None = None
    if fmt in {"auto", "yaml"}:
        try:
            records, warnings = parse_yaml_subscription(body, source=source)
        except (UnicodeDecodeError, YAMLError, ValueError) as exc:
            # YAML decoding/structural failures allow fallback when format is auto.
            if fmt == "yaml":
                raise ParseError(f"failed to parse YAML subscription: {exc}") from exc
            yaml_error = exc
        else:
            # A structurally valid YAML subscription is considered the final format;
            # parse_error="fail" validation warnings must propagate instead of falling back.
            return _finalize(records, warnings, parse_error=parse_error)

    share_links_text: str | None = None
    share_links_warnings: list[str] = []
    if fmt in {"auto", "share-links"}:
        try:
            share_links_text = _decode_text(body)
        except UnicodeDecodeError as exc:
            if fmt == "share-links":
                raise ParseError(f"body is not valid UTF-8: {exc}") from exc
        if share_links_text is not None:
            records, warnings = parse_share_links_text(share_links_text, source=source)
            if records or fmt == "share-links":
                return _finalize(records, warnings, parse_error=parse_error)
            share_links_warnings = warnings

    if fmt == "auto":
        try:
            text = _try_base64_text(body)
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            parts = ["body is not valid base64-encoded share-links"]
            if share_links_warnings:
                parts.append("plain-text share-links did not produce usable records")
            if yaml_error is not None:
                parts.append(f"YAML was not a valid subscription: {yaml_error}")
            raise ParseError("; ".join(parts)) from exc
        records, warnings = parse_share_links_text(text, source=source)
        if records or warnings:
            return _finalize(records, warnings, parse_error=parse_error)
        parts = ["base64-decoded share-links did not produce usable records"]
        if share_links_warnings:
            parts.append("plain-text share-links did not produce usable records")
        if yaml_error is not None:
            parts.append(f"YAML was not a valid subscription: {yaml_error}")
        raise ParseError("; ".join(parts))

    raise ParseError("unsupported subscription format")
