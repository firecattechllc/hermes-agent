from __future__ import annotations

import pytest

from sigil.desktop_bridge.hermes_webui_bridge import (
    hermes_webui_deep_link,
    hermes_webui_status,
)
from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS, handle_request
from sigil.hermes_webui_adapter import HermesWebUIValidationError


def test_hermes_webui_commands_are_registered() -> None:
    assert "hermes_webui_status" in SUPPORTED_COMMANDS
    assert "hermes_webui_deep_link" in SUPPORTED_COMMANDS


def test_hermes_webui_status_reports_disabled_by_default() -> None:
    result = hermes_webui_status()

    assert result["schema_version"] == 1
    node_ids = {entry["node_id"] for entry in result["targets"]}
    assert node_ids == {"hermes-titan", "hermes-mac"}
    for entry in result["targets"]:
        assert entry["state"] == "disabled"
        assert entry["enabled"] is False


def test_hermes_webui_deep_link_rejects_disabled_target() -> None:
    with pytest.raises(HermesWebUIValidationError, match="disabled"):
        hermes_webui_deep_link("hermes-titan", "/chat")


def test_hermes_webui_deep_link_rejects_unknown_node() -> None:
    with pytest.raises(HermesWebUIValidationError, match="unknown"):
        hermes_webui_deep_link("not-a-real-node", "/chat")


def test_handle_request_dispatches_hermes_webui_status() -> None:
    response = handle_request({"command": "hermes_webui_status"})

    assert response["ok"] is True
    assert len(response["result"]["targets"]) == 2


def test_handle_request_hermes_webui_deep_link_fails_closed_on_bad_payload() -> None:
    response = handle_request({"command": "hermes_webui_deep_link", "payload": {}})

    assert response["ok"] is False
