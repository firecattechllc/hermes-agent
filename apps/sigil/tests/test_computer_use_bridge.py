from __future__ import annotations

from unittest.mock import patch

from sigil.desktop_bridge.computer_use_bridge import computer_use_visibility
from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS, handle_request


def test_computer_use_visibility_is_registered() -> None:
    assert "computer_use_visibility" in SUPPORTED_COMMANDS


def test_computer_use_visibility_returns_real_status() -> None:
    result = computer_use_visibility()

    assert result["capability_gated"] is True
    assert result["execution_requires_approval"] is True
    assert isinstance(result["available"], bool)


def test_computer_use_visibility_degrades_on_probe_failure() -> None:
    with patch(
        "tools.computer_use.permissions.computer_use_status",
        side_effect=RuntimeError("driver unreachable"),
    ):
        result = computer_use_visibility()

    assert result["available"] is False
    assert "driver unreachable" in result["reason"]
    assert result["driver_ready"] is False
    assert result["execution_requires_approval"] is True


def test_computer_use_visibility_never_returns_credentials() -> None:
    result = computer_use_visibility()

    serialized = str(result).lower()
    for forbidden in ("api_key", "access_token", "password", "secret"):
        assert forbidden not in serialized


def test_handle_request_dispatches_computer_use_visibility() -> None:
    response = handle_request({"command": "computer_use_visibility"})

    assert response["ok"] is True
    assert "capability_gated" in response["result"]
