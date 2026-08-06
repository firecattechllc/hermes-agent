from __future__ import annotations

import pytest

from hermes_docs_worker.redaction import (
    assert_redacted,
    contains_secret,
    redact_mapping,
    redact_text,
)


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer sk-abcdefghijklmnopqrstuvwx",
        "api_key=abcdef1234567890",
        "HERMES_OMNIROUTE_AUTH_TOKEN=supersecrettokenvalue1234",
        "password: hunter2hunter2",
        "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBg\n-----END PRIVATE KEY-----",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "sk-abcdefghijklmnopqrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "https://example.com/x?token=abc123def456",
    ],
)
def test_redact_text_masks_known_secret_shapes(raw: str) -> None:
    redacted = redact_text(raw)
    assert "[REDACTED]" in redacted
    assert not contains_secret(redacted)


def test_redact_text_masks_private_ipv4_host_octets() -> None:
    redacted = redact_text("Titan endpoint is 192.168.1.42 on the LAN")
    assert "192.168.x.x" in redacted
    assert "192.168.1.42" not in redacted


def test_redact_text_leaves_loopback_alone() -> None:
    redacted = redact_text("bind host 127.0.0.1:8791")
    assert "127.0.0.1" in redacted


def test_contains_secret_true_for_marker_without_value() -> None:
    assert contains_secret("this contains a bearer token somewhere") is True


def test_contains_secret_false_for_clean_text() -> None:
    assert contains_secret("systemd unit is active and running") is False


def test_redact_mapping_masks_by_key_name_regardless_of_value_shape() -> None:
    redacted = redact_mapping({"HERMES_FREELLMAPI_API_KEY": "not-secret-shaped", "SAFE": "hello"})
    assert redacted["HERMES_FREELLMAPI_API_KEY"] == "[REDACTED]"
    assert redacted["SAFE"] == "hello"


def test_assert_redacted_raises_on_secret() -> None:
    with pytest.raises(ValueError):
        assert_redacted("token=abcdef123456", field_name="detail")


def test_assert_redacted_passes_through_clean_text() -> None:
    assert assert_redacted("all clear", field_name="detail") == "all clear"
