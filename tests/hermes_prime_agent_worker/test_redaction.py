from __future__ import annotations

from hermes_prime_agent_worker.redaction import (
    assert_redacted,
    contains_secret,
    redact_text,
)


def test_redacts_bearer_token():
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    redacted = redact_text(text)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_openai_style_key():
    text = "use sk-abcdefghijklmnopqrstuvwxyz for auth"
    redacted = redact_text(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted


def test_leaves_ordinary_text_untouched():
    text = "inspect the repository and report file counts"
    assert redact_text(text) == text


def test_contains_secret_true_for_api_key_assignment():
    assert contains_secret("api_key=abcdefghijklmnop")


def test_contains_secret_false_for_ordinary_text():
    assert not contains_secret("this is a normal sentence")


def test_assert_redacted_raises_on_secret():
    import pytest

    with pytest.raises(ValueError):
        assert_redacted("token: abcdefghijklmnop12345")


def test_assert_redacted_passes_on_clean_text():
    assert_redacted("nothing sensitive here") is None
