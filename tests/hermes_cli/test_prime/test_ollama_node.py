from __future__ import annotations

import pytest

from hermes_cli.prime.ollama_node import (
    OllamaGenerateOutcome,
    OllamaNodeConfig,
    OllamaNodeConfigurationError,
    OllamaNodeInspector,
    OllamaNodeProviderAdapter,
    OllamaNodeTransportError,
    OllamaOutputStore,
)


class FakeTransport:
    def __init__(self, *, models=(), generate_response=None, raise_on_generate=None, raise_on_tags=None):
        self._models = models
        self._generate_response = generate_response
        self._raise_on_generate = raise_on_generate
        self._raise_on_tags = raise_on_tags
        self.generate_calls = []

    def get(self, url, *, timeout_seconds):
        if self._raise_on_tags is not None:
            raise self._raise_on_tags
        return {"models": [{"name": name} for name in self._models]}

    def post(self, url, payload, *, timeout_seconds):
        self.generate_calls.append(payload)
        if self._raise_on_generate is not None:
            raise self._raise_on_generate
        return self._generate_response


def _config(**overrides) -> OllamaNodeConfig:
    fields = dict(
        natural_key="titan",
        endpoint="http://titan.tailnet.internal:11434",
        model_aliases={"lightweight": "hermes-llama3.2:3b-64k"},
        timeout_ms=5_000,
    )
    fields.update(overrides)
    return OllamaNodeConfig(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"natural_key": ""},
        {"endpoint": "not-a-url"},
        {"endpoint": "ftp://titan.tailnet.internal"},
        {"endpoint": "http://user:pass@titan.tailnet.internal"},
        {"model_aliases": {"": "some-model"}},
        {"model_aliases": {"lightweight": ""}},
        {"timeout_ms": 10},
    ],
)
def test_malformed_config_raises_at_construction(overrides) -> None:
    with pytest.raises(OllamaNodeConfigurationError):
        _config(**overrides)


def test_resolve_model_rejects_blank_alias() -> None:
    config = _config()
    with pytest.raises(OllamaNodeConfigurationError):
        config.resolve_model("")
    with pytest.raises(OllamaNodeConfigurationError):
        config.resolve_model(None)


def test_resolve_model_rejects_unconfigured_alias() -> None:
    config = _config()
    with pytest.raises(OllamaNodeConfigurationError):
        config.resolve_model("higher_capability")


def test_resolve_model_returns_configured_model() -> None:
    config = _config()
    assert config.resolve_model("lightweight") == "hermes-llama3.2:3b-64k"


def test_list_models_returns_empty_on_transport_error() -> None:
    transport = FakeTransport(raise_on_tags=OllamaNodeTransportError("down", retryable=True))
    inspector = OllamaNodeInspector(_config(), transport=transport)
    assert inspector.list_models() == ()


def test_list_models_reports_installed_models() -> None:
    transport = FakeTransport(models=("hermes-llama3.2:3b-64k", "finbert-local"))
    inspector = OllamaNodeInspector(_config(), transport=transport)
    assert inspector.list_models() == ("finbert-local", "hermes-llama3.2:3b-64k")
    assert inspector.is_model_available("hermes-llama3.2:3b-64k") is True
    assert inspector.is_model_available("nonexistent-model") is False


def test_generate_rejects_unconfigured_alias_without_network_call() -> None:
    transport = FakeTransport(models=("hermes-llama3.2:3b-64k",))
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="nonexistent-alias", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.retryable is False
    assert transport.generate_calls == []  # never reached the network


def test_generate_rejects_blank_alias_without_network_call() -> None:
    transport = FakeTransport(models=("hermes-llama3.2:3b-64k",))
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert transport.generate_calls == []


def test_generate_rejects_model_not_installed() -> None:
    transport = FakeTransport(models=("some-other-model",))
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.retryable is False
    assert transport.generate_calls == []


def test_generate_succeeds_when_model_installed_and_healthy() -> None:
    transport = FakeTransport(
        models=("hermes-llama3.2:3b-64k",),
        generate_response={"response": "hello from titan"},
    )
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is True
    assert outcome.output_text == "hello from titan"
    assert transport.generate_calls[0]["model"] == "hermes-llama3.2:3b-64k"


def test_generate_handles_unavailable_endpoint() -> None:
    transport = FakeTransport(
        models=("hermes-llama3.2:3b-64k",),
        raise_on_generate=OllamaNodeTransportError("connection refused", retryable=True),
    )
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.retryable is True


def test_generate_handles_malformed_response() -> None:
    transport = FakeTransport(models=("hermes-llama3.2:3b-64k",), generate_response={"unexpected": "shape"})
    adapter = OllamaNodeProviderAdapter(_config(), transport=transport)
    outcome = adapter.generate(alias="lightweight", input_text="hi", timeout_seconds=5)
    assert outcome.succeeded is False
    assert outcome.retryable is False


def test_output_store_round_trips_by_reference() -> None:
    store = OllamaOutputStore()
    reference = store.store("titan", "hello world")
    assert reference.startswith("output://ollama-node/titan/")
    assert store.retrieve(reference) == "hello world"
    assert store.retrieve("output://ollama-node/titan/nonexistent") is None


def test_status_reports_configured_and_installed_state() -> None:
    transport = FakeTransport(models=("hermes-llama3.2:3b-64k",))
    inspector = OllamaNodeInspector(_config(), transport=transport)
    status = inspector.status()
    assert status["configured_aliases"]["lightweight"]["installed"] is True
