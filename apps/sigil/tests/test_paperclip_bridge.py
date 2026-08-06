from __future__ import annotations

from sigil.desktop_bridge.paperclip_bridge import (
    paperclip_status,
    resolve_paperclip_credential,
)
from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS, handle_request


def test_paperclip_status_is_registered() -> None:
    assert "paperclip_status" in SUPPORTED_COMMANDS


def test_resolve_credential_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
    monkeypatch.delenv("SIGIL_PAPERCLIP_API_KEY", raising=False)

    assert resolve_paperclip_credential() is None


def test_resolve_credential_prefers_paperclip_native_env_var(monkeypatch) -> None:
    monkeypatch.setenv("PAPERCLIP_API_KEY", "native-token")
    monkeypatch.setenv("SIGIL_PAPERCLIP_API_KEY", "sigil-token")

    credential = resolve_paperclip_credential()

    assert credential is not None
    assert credential.token == "native-token"


def test_status_reports_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SIGIL_PAPERCLIP_ENABLED", raising=False)

    result = paperclip_status()

    assert result["configured"] is False
    assert result["connected"] is False
    assert "disabled" in result["reason"]


def test_status_reports_missing_credential_when_enabled_but_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_PAPERCLIP_ENABLED", "true")
    monkeypatch.delenv("PAPERCLIP_API_KEY", raising=False)
    monkeypatch.delenv("SIGIL_PAPERCLIP_API_KEY", raising=False)

    result = paperclip_status()

    assert result["configured"] is False
    assert result["connected"] is False
    assert "credential" in result["reason"]


def test_status_reports_connection_failure_without_crashing(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_PAPERCLIP_ENABLED", "true")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "some-token")
    monkeypatch.setenv("SIGIL_PAPERCLIP_BASE_URL", "http://127.0.0.1:1")  # nothing listens here

    result = paperclip_status()

    assert result["configured"] is True
    assert result["connected"] is False
    assert "reason" in result


def test_status_never_leaks_the_credential_token(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_PAPERCLIP_ENABLED", "true")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "super-secret-token-xyz")
    monkeypatch.setenv("SIGIL_PAPERCLIP_BASE_URL", "http://127.0.0.1:1")

    result = paperclip_status()

    assert "super-secret-token-xyz" not in str(result)


def test_handle_request_dispatches_paperclip_status(monkeypatch) -> None:
    monkeypatch.delenv("SIGIL_PAPERCLIP_ENABLED", raising=False)

    response = handle_request({"command": "paperclip_status"})

    assert response["ok"] is True
    assert "configured" in response["result"]
