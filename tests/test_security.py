"""URL 安全、路径熵和脱敏功能测试。

URL safety, path entropy, and redaction function tests.
"""

import pytest

from mihomo_proxy_manager.security import (
    SecurityError,
    assert_safe_url,
    has_path_entropy,
    redact_secret,
)


def test_rejects_private_network_url() -> None:
    """测试拒绝私有网络 URL / Test rejecting private network URLs."""
    with pytest.raises(SecurityError):
        assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=False)


def test_allows_private_network_when_opted_in() -> None:
    """测试允许私有网络 URL（主动选择时）/ Test allowing private network URLs when opted in."""
    assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=True)


def test_rejects_unsupported_scheme() -> None:
    """测试拒绝不支持的协议 / Test rejecting unsupported URL schemes."""
    with pytest.raises(SecurityError):
        assert_safe_url("ftp://example.com/sub", allow_private_network=False)


def test_hidden_path_entropy() -> None:
    """测试路径熵检测 / Test path entropy detection for hidden paths."""
    assert has_path_entropy("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", min_bits=128)
    assert not has_path_entropy("/p/short.yaml", min_bits=128)


def test_redact_secret() -> None:
    """测试脱敏函数，遮盖 URL 路径、token 和 Authorization 中的敏感信息。

    Test the redact function to mask sensitive information in URL paths, tokens, and Authorization headers.
    """
    text = "GET /p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml https://x.test/sub?token=secret Authorization=Bearer abc"
    redacted = redact_secret(
        text, extra_secrets=["/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"]
    )

    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in redacted
    assert "token=secret" not in redacted
    assert "Bearer abc" not in redacted


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization=Bearer abc", "Authorization=***"),
        ("Authorization: Bearer abc", "Authorization: ***"),
        ("Authorization=Basic abc123", "Authorization=***"),
        ("Authorization=abc123 extra", "Authorization=*** extra"),
        ("Authorization=Bearer abc extra", "Authorization=*** extra"),
        (
            "X=before Authorization=Bearer abc Y=after",
            "X=before Authorization=*** Y=after",
        ),
    ],
)
def test_redact_secret_authorization(text: str, expected: str) -> None:
    """测试各种 Authorization 格式的脱敏 / Test redaction of various Authorization formats."""
    assert redact_secret(text) == expected


def test_redact_secret_does_not_leave_bearer_tail() -> None:
    """测试脱敏后不会残留 Bearer 尾巴 / Test that redaction does not leave a Bearer tail."""
    redacted = redact_secret("Authorization=Bearer abc")
    assert "Bearer " not in redacted
    assert " ***" not in redacted


@pytest.mark.parametrize(
    "hostname",
    [
        "http://localhost/foo",
        "http://localhost.localdomain/foo",
        "http://metadata.google.internal/foo",
        "http://my-service.local/foo",
    ],
)
def test_rejects_blocked_hostnames_without_dns(hostname: str) -> None:
    """测试在不进行 DNS 解析时拒绝被封锁的主机名 / Test rejecting blocked hostnames without DNS resolution."""
    with pytest.raises(SecurityError):
        assert_safe_url(hostname, allow_private_network=False, resolve_dns=False)


def test_allows_public_hostname_without_dns() -> None:
    """测试在不进行 DNS 解析时允许公共主机名 / Test allowing public hostnames without DNS resolution."""
    assert_safe_url(
        "https://example.com/foo", allow_private_network=False, resolve_dns=False
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/foo",
        "http://0x7f000001/foo",
        "http://017700000001/foo",
        "http://127.1/foo",
        "http://0000000000000000000000000000000001/foo",
        "http://127.0.0.1./foo",
        "http://10.1/foo",
        "http://192.168.1/foo",
        "http://172.16.0.1/foo",
        "http://172.31.255.255/foo",
        "http://[::1]/foo",
        "http://[fc00::1]/foo",
        "http://[fe80::1]/foo",
    ],
)
def test_rejects_noncanonical_private_ip_literals_without_dns(url: str) -> None:
    """测试拒绝非规范格式的私有 IP 字面量（不进行 DNS 解析）。

    Test rejecting non-canonical private IP literals without DNS resolution.
    """
    with pytest.raises(SecurityError):
        assert_safe_url(url, allow_private_network=False, resolve_dns=False)


def test_allows_public_noncanonical_ip_literals_without_dns() -> None:
    """测试允许公共 IP 的非规范格式字面量（不进行 DNS 解析）。

    Test allowing non-canonical public IP literals without DNS resolution.
    """
    # 134744072 == 8.8.8.8
    assert_safe_url(
        "http://134744072/foo", allow_private_network=False, resolve_dns=False
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://0x7f.0.0.1/foo",
        "http://0177.0.0.1/foo",
        "http://0x7f.1/foo",
        "http://0177.1/foo",
    ],
)
def test_rejects_hex_octal_private_ip_literals_without_dns(url: str) -> None:
    """测试拒绝十六进制/八进制格式的私有 IP 字面量（不进行 DNS 解析）。

    Test rejecting hex/octal private IP literals without DNS resolution.
    """
    with pytest.raises(SecurityError):
        assert_safe_url(url, allow_private_network=False, resolve_dns=False)


def test_allows_public_hex_octal_ip_literals_without_dns() -> None:
    """测试允许公共 IP 的十六进制/八进制格式字面量（不进行 DNS 解析）。

    Test allowing public hex/octal IP literals without DNS resolution.
    """
    assert_safe_url(
        "http://0x8.0x8.0x8.0x8/foo", allow_private_network=False, resolve_dns=False
    )


def test_redact_secret_standalone_bearer() -> None:
    """测试脱敏独立出现的 Bearer token / Test redaction of standalone Bearer tokens."""
    assert redact_secret("log line with Bearer abc123") == "log line with Bearer ***"
    assert (
        redact_secret("Bearer first and Bearer second") == "Bearer *** and Bearer ***"
    )
    assert "Bearer secret" not in redact_secret("some Bearer secret here")


@pytest.mark.parametrize(
    "url",
    [
        "http://224.0.0.1/foo",
        "http://239.255.255.255/foo",
        "http://240.0.0.1/foo",
        "http://255.255.255.255/foo",
        "http://0.0.0.0/foo",
        "http://[::]/foo",
        "http://[ff02::1]/foo",
    ],
)
def test_rejects_multicast_reserved_and_unspecified_addresses(url: str) -> None:
    """测试拒绝多播、保留和未指定地址 / Test rejecting multicast, reserved, and unspecified addresses."""
    with pytest.raises(SecurityError):
        assert_safe_url(url, allow_private_network=False, resolve_dns=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://0/foo",
        "http://0./foo",
        "http://[::]/foo",
    ],
)
def test_rejects_zero_literal_unspecified_addresses(url: str) -> None:
    """测试拒绝零字面量和未指定地址 / Test rejecting zero literal and unspecified addresses."""
    with pytest.raises(SecurityError):
        assert_safe_url(url, allow_private_network=False, resolve_dns=False)
