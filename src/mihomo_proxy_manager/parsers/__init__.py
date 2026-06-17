"""订阅格式检测和分发解析器，支持 YAML、share-links 和 base64 编码。

Subscription format detection and dispatch parser supporting YAML, share-links, and base64 encoding.
"""

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
    """解析错误，在订阅解析失败时抛出。

    Parse error raised when subscription parsing fails.
    """

    pass


@dataclass(frozen=True)
class ParseResult:
    """解析结果，包含代理记录列表和警告列表。

    Parse result containing a list of proxy records and warnings.
    """

    records: list[ProxyRecord]
    warnings: list[str]


def _decode_text(body: bytes) -> str:
    """将字节数据解码为 UTF-8 字符串（含 BOM 处理）。

    Decode bytes to UTF-8 string (with BOM handling).

    Args:
        body: 字节数据 / Byte data.

    Returns:
        解码后的字符串 / Decoded string.
    """
    return body.decode("utf-8-sig")


def _try_base64_text(body: bytes) -> str:
    """尝试以 Base64 解码字节数据为文本。

    Attempt to decode bytes as Base64-encoded text.

    Args:
        body: 字节数据 / Byte data.

    Returns:
        解码后的字符串 / Decoded string.

    Raises:
        binascii.Error: 如果 Base64 解码失败 / If Base64 decoding fails.
        ValueError: 如果 Base64 解码失败 / If Base64 decoding fails.
        UnicodeDecodeError: 如果解码后的数据不是有效 UTF-8 / If decoded data is not valid UTF-8.
    """
    raw = body.strip()
    padding = b"=" * (-len(raw) % 4)
    padded = raw + padding
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8-sig")
    except (binascii.Error, ValueError):
        return base64.b64decode(padded).decode("utf-8-sig")


def _finalize(
    records: list[ProxyRecord],
    warnings: list[str],
    *,
    parse_error: Literal["skip", "fail"],
) -> ParseResult:
    """根据 parse_error 策略最终确定解析结果。

    Finalize the parse result according to the parse_error strategy.

    Args:
        records: 解析出的代理记录 / Parsed proxy records.
        warnings: 解析警告列表 / List of parse warnings.
        parse_error: 解析错误处理策略 / Parse error handling strategy.

    Returns:
        最终解析结果 / Finalized parse result.

    Raises:
        ParseError: 如果策略为 "fail" 且有警告，或没有可用代理 / If strategy is "fail" with warnings, or no usable proxies.
    """
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
    """解析订阅内容，自动检测格式或按指定格式解析。

    Parse subscription content, auto-detecting format or using the specified format.

    Args:
        body: 订阅内容的原始字节 / Raw bytes of subscription content.
        source: 订阅源名称 / Source name.
        fmt: 解析格式（auto/yaml/share-links） / Parse format (auto/yaml/share-links).
        parse_error: 解析错误处理策略 / Parse error handling strategy.

    Returns:
        解析结果 / Parse result.

    Raises:
        ParseError: 如果所有解析方式都失败 / If all parsing methods fail.
    """
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
