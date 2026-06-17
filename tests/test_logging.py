from mihomo_proxy_manager.logging import _redact_record


def test_log_record_redaction_covers_message_and_extra() -> None:
    secret_path = "/p/CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL.yaml"
    record = {
        "message": f"GET {secret_path} https://x.test/sub?token=secret Authorization=Bearer abc",
        "extra": {"url": "https://x.test/sub?token=secret", "path": secret_path},
    }

    _redact_record(record, [secret_path])

    rendered = str(record)
    assert "CsYWr0BGzGQQmwq2X5eG5Qn8Kp4zR7vL" not in rendered
    assert "token=secret" not in rendered
    assert "Bearer abc" not in rendered
