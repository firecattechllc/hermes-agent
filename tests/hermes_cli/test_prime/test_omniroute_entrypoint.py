from __future__ import annotations

from hermes_cli.prime.omniroute_config import TitanRoutingConfig
from hermes_cli.prime.omniroute_entrypoint import build_upstreams
from hermes_cli.prime.omniroute_upstreams import TitanOllamaUpstreamAdapter


def _env(**overrides) -> dict:
    base = {
        "HERMES_OMNIROUTE_AUTH_TOKEN": "a" * 20,
        "HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES": "embedding,lightweight,large",
        "HERMES_OMNIROUTE_ALIAS_ROUTES": (
            "embedding=titan_ollama@embeddinggemma:latest,"
            "lightweight=titan_ollama@hermes-llama3.2:3b-64k,"
            "large=freellmapi@gpt-4o-mini"
        ),
    }
    base.update(overrides)
    return base


class _FakeTransport:
    """Records the exact ``alias`` OllamaNodeProviderAdapter resolves against."""

    def __init__(self) -> None:
        self.get_calls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float) -> object:
        self.get_calls.append(url)
        return {"models": [{"name": "embeddinggemma:latest"}, {"name": "hermes-llama3.2:3b-64k"}]}

    def post(self, url: str, payload: dict, *, timeout_seconds: float) -> object:
        return {"response": f"ok:{payload['model']}"}


def test_build_upstreams_titan_ollama_model_aliases_round_trip_through_adapter() -> None:
    """Regression test for the alias/model key mismatch between
    build_upstreams()'s OllamaNodeConfig.model_aliases and
    TitanOllamaUpstreamAdapter.generate(), which forwards the already-
    resolved concrete model tag as the "alias" to re-resolve. Before the
    fix, model_aliases was keyed by the governed alias name (e.g.
    "lightweight"), so every titan_ollama dispatch failed closed with
    "no admitted model is configured for alias '<concrete tag>'" no matter
    which alias or model was requested.
    """
    config = TitanRoutingConfig.from_env(_env())
    upstreams = build_upstreams(config)

    adapter = upstreams["titan_ollama"]
    assert isinstance(adapter, TitanOllamaUpstreamAdapter)

    transport = _FakeTransport()
    adapter._underlying.transport = transport
    adapter._underlying.inspector.transport = transport

    resolution = config.resolve_alias_detailed("lightweight")
    assert resolution.permitted is True

    outcome = adapter.generate(
        model=resolution.model, input_text="ping", timeout_seconds=5.0
    )

    assert outcome.succeeded is True, outcome.error
    assert outcome.output_text == f"ok:{resolution.model}"
