import pytest

from mihomo_proxy_manager.security import SecurityError, assert_safe_url, has_path_entropy, redact_secret


def test_rejects_private_network_url() -> None:
    with pytest.raises(SecurityError):
        assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=False)


def test_allows_private_network_when_opted_in() -> None:
    assert_safe_url("http://127.0.0.1:8080/sub", allow_private_network=True)


def test_rejects_unsupported_scheme() -> None:
    with pytest.raises(SecurityError):
        assert_safe_url("ftp://example.com/sub", allow_private_network=False)


def test_hidden_path_entropy() -> None:
    assert has_path_entropy("/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml", min_bits=128)
    assert not has_path_entropy("/p/short.yaml", min_bits=128)


def test_redact_secret() -> None:
    text = "GET /p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml https://x.test/sub?token=secret Authorization=Bearer abc"
    redacted = redact_secret(text, extra_secrets=["/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"])

    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in redacted
    assert "token=secret" not in redacted
    assert "Bearer abc" not in redacted
