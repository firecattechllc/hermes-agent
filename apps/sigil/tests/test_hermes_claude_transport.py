from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from sigil.ai import (
    ClaudeTransportError,
    ClaudeTransportFailure,
    HermesClaudeTransport,
)


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def install_fake_hermes_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    normalized_content: str | None = "Safe response",
    tool_calls=None,
    finish_reason: str = "stop",
    usage=None,
    create_error: Exception | None = None,
):
    client = FakeClient()
    captured = {}

    adapter = ModuleType("agent.anthropic_adapter")

    def is_oauth_token(_credential: str) -> bool:
        return False

    def build_client(credential, timeout=None):
        captured["credential"] = credential
        captured["timeout"] = timeout
        return client

    def build_kwargs(**kwargs):
        captured["kwargs"] = kwargs
        return {"model": kwargs["model"], "messages": kwargs["messages"]}

    def create_message(_client, api_kwargs, **kwargs):
        captured["api_kwargs"] = api_kwargs
        captured["create_kwargs"] = kwargs
        if create_error is not None:
            raise create_error
        return SimpleNamespace(
            usage=usage
            or SimpleNamespace(
                input_tokens=10,
                output_tokens=4,
                total_tokens=14,
            )
        )

    adapter._is_oauth_token = is_oauth_token
    adapter.build_anthropic_client = build_client
    adapter.build_anthropic_kwargs = build_kwargs
    adapter.create_anthropic_message = create_message

    transports = ModuleType("agent.transports")

    class FakeNormalizer:
        def normalize_response(self, _response, **kwargs):
            captured["normalize_kwargs"] = kwargs
            return SimpleNamespace(
                content=normalized_content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )

    def get_transport(name):
        captured["transport_name"] = name
        return FakeNormalizer()

    transports.get_transport = get_transport

    monkeypatch.setitem(sys.modules, "agent.anthropic_adapter", adapter)
    monkeypatch.setitem(sys.modules, "agent.transports", transports)

    return client, captured


def test_transport_invokes_existing_hermes_anthropic_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, captured = install_fake_hermes_modules(monkeypatch)
    secret = "secret-never-returned"

    transport = HermesClaudeTransport(
        credential_resolver=lambda: secret,
    )

    result = transport.invoke(
        model="claude-sonnet-4-6",
        prompt="Analyze this.",
        timeout_ms=12_000,
        max_output_tokens=2_048,
    )

    assert result.content == "Safe response"
    assert result.finish_reason == "stop"
    assert result.input_tokens == 10
    assert result.output_tokens == 4
    assert result.total_tokens == 14

    assert captured["credential"] == secret
    assert captured["timeout"] == 12.0
    assert captured["kwargs"]["tools"] is None
    assert captured["kwargs"]["tool_choice"] == "none"
    assert captured["kwargs"]["max_tokens"] == 2_048
    assert captured["transport_name"] == "anthropic_messages"
    assert client.closed is True

    assert secret not in repr(result)


def test_transport_rejects_missing_credentials() -> None:
    transport = HermesClaudeTransport(
        credential_resolver=lambda: None,
    )

    with pytest.raises(ClaudeTransportError) as error:
        transport.invoke(
            model="claude-sonnet-4-6",
            prompt="Analyze this.",
            timeout_ms=10_000,
            max_output_tokens=1_024,
        )

    assert error.value.classification == ClaudeTransportFailure.UNAVAILABLE


def test_transport_rejects_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = install_fake_hermes_modules(
        monkeypatch,
        tool_calls=[SimpleNamespace(name="dangerous_tool")],
    )

    transport = HermesClaudeTransport(
        credential_resolver=lambda: "credential",
    )

    with pytest.raises(ClaudeTransportError) as error:
        transport.invoke(
            model="claude-sonnet-4-6",
            prompt="Analyze this.",
            timeout_ms=10_000,
            max_output_tokens=1_024,
        )

    assert error.value.classification == ClaudeTransportFailure.MALFORMED
    assert client.closed is True


@pytest.mark.parametrize("content", [None, "", "   "])
def test_transport_rejects_empty_content(
    monkeypatch: pytest.MonkeyPatch,
    content,
) -> None:
    install_fake_hermes_modules(
        monkeypatch,
        normalized_content=content,
    )

    transport = HermesClaudeTransport(
        credential_resolver=lambda: "credential",
    )

    with pytest.raises(ClaudeTransportError) as error:
        transport.invoke(
            model="claude-sonnet-4-6",
            prompt="Analyze this.",
            timeout_ms=10_000,
            max_output_tokens=1_024,
        )

    assert error.value.classification == ClaudeTransportFailure.MALFORMED


def test_transport_maps_timeout_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = install_fake_hermes_modules(
        monkeypatch,
        create_error=TimeoutError("provider timeout"),
    )

    transport = HermesClaudeTransport(
        credential_resolver=lambda: "credential",
    )

    with pytest.raises(ClaudeTransportError) as error:
        transport.invoke(
            model="claude-sonnet-4-6",
            prompt="Analyze this.",
            timeout_ms=10_000,
            max_output_tokens=1_024,
        )

    assert error.value.classification == ClaudeTransportFailure.TIMEOUT
    assert client.closed is True


def test_transport_rejects_invalid_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_hermes_modules(
        monkeypatch,
        usage=SimpleNamespace(
            input_tokens=-1,
            output_tokens=2,
            total_tokens=1,
        ),
    )

    transport = HermesClaudeTransport(
        credential_resolver=lambda: "credential",
    )

    with pytest.raises(ClaudeTransportError) as error:
        transport.invoke(
            model="claude-sonnet-4-6",
            prompt="Analyze this.",
            timeout_ms=10_000,
            max_output_tokens=1_024,
        )

    assert error.value.classification == ClaudeTransportFailure.MALFORMED
