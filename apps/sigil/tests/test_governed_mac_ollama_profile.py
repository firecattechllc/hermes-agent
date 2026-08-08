from __future__ import annotations

import math

import pytest

from sigil.ai.gemma import GemmaTransportError, GemmaTransportFailure
from sigil.ai.ledger import DurableAIEvidenceLedger
from sigil.ai.mac_ollama import (
    EMBEDDING_MODEL,
    FALLBACK_MODEL,
    FAST_MODEL,
    PRIMARY_MODEL,
    MacOllamaConfigurationError,
    MacOllamaInspector,
    MacOllamaProfileConfig,
    MacOllamaRoleProvider,
    MacOllamaRouteRequest,
    OllamaEmbeddingProvider,
    OllamaTextRole,
    route_mac_ollama,
)
from sigil.ai.models import Capability, InputType, ProviderHealth
from sigil.ai.provider import ProviderFailureClass, ProviderInvocation

NOW = "2026-08-01T22:00:00Z"


class FakeOllama:
    def __init__(self, *, missing=(), mismatch=(), unavailable=False, timeout=False):
        self.missing = set(missing)
        self.mismatch = set(mismatch)
        self.unavailable = unavailable
        self.timeout = timeout
        self.calls = []

    def request(self, *, method, url, payload, timeout_seconds):
        self.calls.append((method, url, payload, timeout_seconds))
        if self.timeout:
            raise GemmaTransportError(GemmaTransportFailure.TIMEOUT)
        if self.unavailable:
            raise GemmaTransportError(GemmaTransportFailure.UNAVAILABLE)
        if url.endswith("/api/tags"):
            return {
                "models": [
                    {"name": name, "digest": f"sha256:{index:064x}", "size": 1000 + index}
                    for index, name in enumerate(
                        (PRIMARY_MODEL, FAST_MODEL, EMBEDDING_MODEL, FALLBACK_MODEL), 1
                    )
                    if name not in self.missing
                ]
            }
        if url.endswith("/api/show"):
            model = payload["model"]
            return {
                "model": "wrong/model:latest" if model in self.mismatch else model,
                "details": {
                    "family": "gemma3",
                    "parameter_size": "12.2B",
                    "quantization_level": "Q4_K_M",
                },
                "model_info": {"general.architecture": "gemma3"},
                "layers": [{"digest": "sha256:" + "a" * 64}],
            }
        if url.endswith("/api/chat"):
            return {"message": {"content": '{"status":"ok","authoritative":false}'}}
        if url.endswith("/api/embed"):
            return {"embeddings": [[3.0, 4.0] for _ in payload["input"]]}
        raise AssertionError(url)


def profile(**changes):
    values = {"enabled": True, "embedding_adapter": "ollama", "embedding_dimension": 2}
    values.update(changes)
    return MacOllamaProfileConfig(**values)


def invocation(model, capability=Capability.REASONING, payload=None, timeout_ms=1000):
    return ProviderInvocation(
        request_id="request-1",
        task_correlation_id="task-1",
        model_id=model,
        registry_revision="sha256:" + "b" * 64,
        capability=capability,
        input_payload=payload or {"prompt": "bounded"},
        timeout_ms=timeout_ms,
        started_at=NOW,
        ended_at=NOW,
    )


def providers(config, transport):
    return {
        role: MacOllamaRoleProvider(config, role, transport=transport) for role in OllamaTextRole
    }


def test_profile_is_disabled_by_default_and_rejects_non_loopback_or_unapproved_identity():
    value = MacOllamaProfileConfig()
    assert not value.enabled
    assert not value.fallback_enabled
    assert value.embedding_adapter == "sentence_transformers"
    with pytest.raises(MacOllamaConfigurationError):
        MacOllamaProfileConfig(endpoint="http://192.168.1.2:11434")
    with pytest.raises(MacOllamaConfigurationError):
        MacOllamaProfileConfig(primary_model="other:latest")


def test_inspection_reports_exact_manifest_metadata_unknown_upstream_and_boundaries():
    status = MacOllamaInspector(profile(), FakeOllama()).status()
    assert status["endpoint_classification"] == "loopback_http"
    assert status["roles"]["primary"]["identity_match"] is True
    manifest = status["roles"]["primary"]["manifest"]
    assert manifest["architecture"] == "gemma3"
    assert manifest["layer_digests"] == ("sha256:" + "a" * 64,)
    assert status["roles"]["primary"]["upstream_revision_evidence"] == "unknown"
    assert status["roles"]["primary"]["license_evidence"] == "unknown"
    assert status["paper_only"] is True
    assert all(
        status[key] is False
        for key in (
            "broker_submission",
            "execution_authorized",
            "approval_authority",
            "portfolio_mutation",
            "capital_authority",
            "policy_mutation",
            "credential_access",
            "arbitrary_shell",
            "arbitrary_filesystem",
            "governance_bypass",
        )
    )


def test_primary_and_fast_use_same_text_json_path_and_fast_is_text_only():
    transport = FakeOllama()
    configured = profile()
    primary = MacOllamaRoleProvider(
        configured, OllamaTextRole.PRIMARY_REASONING, transport=transport
    )
    fast = MacOllamaRoleProvider(
        configured, OllamaTextRole.FAST_TEXT_REASONING, transport=transport
    )
    assert primary.invoke(invocation(PRIMARY_MODEL)).succeeded
    assert fast.invoke(invocation(FAST_MODEL)).succeeded
    assert fast.registration().supported_input_types == frozenset(
        {InputType.TEXT, InputType.STRUCTURED_JSON}
    )
    assert Capability.MULTIMODAL_ANALYSIS not in fast.capabilities
    assert [call[2]["model"] for call in transport.calls if call[1].endswith("/api/chat")] == [
        PRIMARY_MODEL,
        FAST_MODEL,
    ]
    assert all(
        call[2]["keep_alive"] == 0 for call in transport.calls if call[1].endswith("/api/chat")
    )
    assert not any(call[1].endswith("/api/generate") for call in transport.calls)


def test_roles_keep_separate_health_and_missing_or_mismatched_models_fail_closed():
    configured = profile()
    primary = MacOllamaRoleProvider(
        configured, OllamaTextRole.PRIMARY_REASONING, transport=FakeOllama()
    )
    fast = MacOllamaRoleProvider(
        configured, OllamaTextRole.FAST_TEXT_REASONING, transport=FakeOllama(missing={FAST_MODEL})
    )
    assert primary.health_probe().health is ProviderHealth.HEALTHY
    assert fast.health_probe().health is ProviderHealth.UNAVAILABLE
    mismatch = MacOllamaRoleProvider(
        configured, OllamaTextRole.PRIMARY_REASONING, transport=FakeOllama(mismatch={PRIMARY_MODEL})
    )
    assert mismatch.health_probe().classification == "model_identity_mismatch"


def test_routing_is_deterministic_and_fallback_requires_all_three_admission_conditions(tmp_path):
    transport = FakeOllama()
    disabled = profile(fallback_enabled=False)
    role_providers = providers(disabled, transport)
    rejected = route_mac_ollama(
        MacOllamaRouteRequest("r1", OllamaTextRole.COMPATIBILITY_FALLBACK), disabled, role_providers
    )
    assert not rejected.admitted and rejected.reason == "request_fallback_permission_required"
    rejected = route_mac_ollama(
        MacOllamaRouteRequest("r2", OllamaTextRole.COMPATIBILITY_FALLBACK, True, "legacy schema"),
        disabled,
        role_providers,
    )
    assert not rejected.admitted and rejected.reason == "profile_fallback_disabled"
    enabled = profile(fallback_enabled=True)
    ledger = DurableAIEvidenceLedger(tmp_path)
    admitted = route_mac_ollama(
        MacOllamaRouteRequest("r3", OllamaTextRole.COMPATIBILITY_FALLBACK, True, "legacy schema"),
        enabled,
        providers(enabled, transport),
        ledger,
    )
    assert admitted.admitted and admitted.selected_model == FALLBACK_MODEL
    assert admitted.reason == "requested_role"
    assert admitted.evidence_digest.startswith("sha256:")
    persisted = ledger.read_records()
    assert len(persisted) == 1
    assert persisted[0].record_type.value == "fallback_decision"
    assert persisted[0].fallback is True
    assert persisted[0].paper_only is True
    assert persisted[0].broker_submission is False


def test_provider_outage_does_not_auto_activate_fallback():
    configured = profile(fallback_enabled=True)
    decision = route_mac_ollama(
        MacOllamaRouteRequest("r4", OllamaTextRole.PRIMARY_REASONING, True, "outage"),
        configured,
        providers(configured, FakeOllama(unavailable=True)),
    )
    assert not decision.admitted
    assert decision.selected_role is None
    assert decision.reason == "requested_role_unavailable"


def test_ollama_embedding_requires_explicit_adapter_and_validates_vectors_and_bounds():
    disabled = OllamaEmbeddingProvider(
        profile(embedding_adapter="sentence_transformers"), transport=FakeOllama()
    )
    assert (
        disabled.invoke(
            invocation(EMBEDDING_MODEL, Capability.EMBEDDINGS, {"texts": ["one"]})
        ).failure.classification
        is ProviderFailureClass.UNAVAILABLE
    )
    provider = OllamaEmbeddingProvider(profile(), transport=FakeOllama())
    result = provider.invoke(
        invocation(EMBEDDING_MODEL, Capability.EMBEDDINGS, {"texts": ["one", "two"]})
    )
    assert result.succeeded
    assert result.output["vectors"] == [[0.6, 0.8], [0.6, 0.8]]
    assert all(math.isclose(sum(v * v for v in vector), 1.0) for vector in result.output["vectors"])
    assert result.output["approval_authority"] is False
    too_many = ["x"] * (profile().embedding_max_batch_size + 1)
    assert (
        provider.invoke(
            invocation(EMBEDDING_MODEL, Capability.EMBEDDINGS, {"texts": too_many})
        ).failure.classification
        is ProviderFailureClass.MALFORMED_OUTPUT
    )


def test_embedding_missing_mismatch_unavailable_and_timeout_fail_closed():
    for transport, expected in (
        (FakeOllama(missing={EMBEDDING_MODEL}), ProviderFailureClass.MODEL_IDENTITY_MISMATCH),
        (FakeOllama(mismatch={EMBEDDING_MODEL}), ProviderFailureClass.MODEL_IDENTITY_MISMATCH),
        (FakeOllama(unavailable=True), ProviderFailureClass.UNAVAILABLE),
        (FakeOllama(timeout=True), ProviderFailureClass.TIMEOUT),
    ):
        result = OllamaEmbeddingProvider(profile(), transport=transport).invoke(
            invocation(EMBEDDING_MODEL, Capability.EMBEDDINGS, {"texts": ["one"]})
        )
        assert result.failure.classification is expected
