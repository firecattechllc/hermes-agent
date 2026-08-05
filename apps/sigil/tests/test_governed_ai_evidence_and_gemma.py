from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai import (
    AIEvidenceConflictError,
    AIEvidenceCorruptionError,
    AIEvidenceRecordType,
    Capability,
    CostClass,
    DurableAIEvidenceLedger,
    ExecutionLocation,
    GemmaConfigurationError,
    GemmaTransportError,
    GemmaTransportFailure,
    GovernedAIEvidenceRecord,
    GovernedModelRegistry,
    GovernedModelRouter,
    GovernedModelWorkRequest,
    InputType,
    LocalGemmaConfig,
    LocalGemmaProvider,
    ModelRegistration,
    PrivacyTier,
    ProviderFailureClass,
    ProviderHealth,
    ProviderIdentity,
    ProviderInvocation,
    Responsibility,
    RoutingFailureClass,
    RoutingRequest,
    TrustTier,
    append_routing_decision,
)

REGISTRY_REVISION = "sha256:" + "a" * 64


class FakeTransport:
    def __init__(self, *, post: object | Exception | None = None, model: str = "gemma3:4b") -> None:
        self.post = post
        self.model = model
        self.calls: list[tuple[str, str, object]] = []

    def request(self, *, method, url, payload, timeout_seconds):
        self.calls.append((method, url, payload))
        if method == "GET":
            if isinstance(self.post, GemmaTransportError) and self.post.classification in {
                GemmaTransportFailure.UNAVAILABLE,
                GemmaTransportFailure.TIMEOUT,
            }:
                raise self.post
            return {"models": [{"name": self.model}]}
        if isinstance(self.post, Exception):
            raise self.post
        return self.post or {"message": {"content": '{"answer":"governed"}'}}


def config(*, enabled: bool = True) -> LocalGemmaConfig:
    return LocalGemmaConfig(
        enabled=enabled,
        endpoint="http://127.0.0.1:11434" if enabled else None,
        model_id="gemma3:4b" if enabled else None,
        model_version="gemma3-4b-v1",
        request_timeout_ms=1_000,
    )


def invocation(
    *,
    request_id: str = "request-001",
    model_id: str = "gemma3:4b",
    capability: Capability = Capability.REASONING,
) -> ProviderInvocation:
    return ProviderInvocation(
        request_id=request_id,
        task_correlation_id="task-001",
        model_id=model_id,
        registry_revision=REGISTRY_REVISION,
        capability=capability,
        input_payload={"evidence_digest": "sha256:" + "b" * 64},
        timeout_ms=1_000,
        started_at="2026-08-01T15:00:00Z",
        ended_at="2026-08-01T15:00:01Z",
    )


def ledger(tmp_path: Path) -> DurableAIEvidenceLedger:
    return DurableAIEvidenceLedger(tmp_path.resolve())


def record(identity: str = "ai-evidence-" + "1" * 64) -> GovernedAIEvidenceRecord:
    return GovernedAIEvidenceRecord(
        evidence_identity=identity,
        record_type=AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED,
        request_id="request-001",
        task_correlation_id="task-001",
        provider_id="local-gemma-ollama",
        model_id="gemma3:4b",
        model_version="gemma3-4b-v1",
        registry_revision=REGISTRY_REVISION,
        capability=Capability.REASONING,
        execution_location=ExecutionLocation.LOCAL,
        routing_status="selected",
        fallback=False,
        started_at="2026-08-01T15:00:00Z",
        ended_at="2026-08-01T15:00:01Z",
        succeeded=True,
        failure_classification=None,
        input_digest="sha256:" + "c" * 64,
        output_digest="sha256:" + "d" * 64,
        provider_metadata=(("adapter", "ollama-compatible-v1"),),
    )


def test_durable_successful_invocation_survives_restart(tmp_path: Path) -> None:
    first = ledger(tmp_path)
    result = LocalGemmaProvider(config(), transport=FakeTransport(), ledger=first).invoke(
        invocation()
    )

    records = ledger(tmp_path).read_records()
    assert result.succeeded
    assert [item.record_type for item in records] == [
        AIEvidenceRecordType.PROVIDER_INVOCATION_ATTEMPT,
        AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED,
    ]
    assert records[1].output_digest == result.evidence.output_digest
    assert records[1].previous_record_hash == records[0].entry_hash
    assert records[1].broker_submission is False
    assert records[1].paper_only is True


def test_durable_failed_invocation_records_timeout(tmp_path: Path) -> None:
    transport = FakeTransport(post=GemmaTransportError(GemmaTransportFailure.TIMEOUT))
    result = LocalGemmaProvider(config(), transport=transport, ledger=ledger(tmp_path)).invoke(
        invocation()
    )

    records = ledger(tmp_path).read_records()
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.TIMEOUT
    assert records[-1].failure_classification == ProviderFailureClass.TIMEOUT.value
    assert records[-1].record_type == AIEvidenceRecordType.PROVIDER_RESULT_FAILED


def test_duplicate_evidence_identity_is_rejected(tmp_path: Path) -> None:
    evidence = ledger(tmp_path)
    evidence.append(record())
    with pytest.raises(AIEvidenceConflictError, match="duplicate"):
        evidence.append(record())


def test_malformed_complete_record_fails_closed(tmp_path: Path) -> None:
    evidence = ledger(tmp_path)
    evidence.path.write_text('{"not":"a governed record"}\n', encoding="utf-8")
    with pytest.raises(AIEvidenceCorruptionError, match="invalid"):
        evidence.read_records()


def test_provider_health_rejection_is_durable(tmp_path: Path) -> None:
    transport = FakeTransport(post=GemmaTransportError(GemmaTransportFailure.UNAVAILABLE))
    result = LocalGemmaProvider(config(), transport=transport, ledger=ledger(tmp_path)).invoke(
        invocation()
    )

    assert result.failure is not None
    assert ledger(tmp_path).read_records()[-1].record_type == (
        AIEvidenceRecordType.PROVIDER_HEALTH_REJECTED
    )


def test_malformed_output_rejection_is_durable(tmp_path: Path) -> None:
    transport = FakeTransport(post={"message": {"content": "not-json"}})
    result = LocalGemmaProvider(config(), transport=transport, ledger=ledger(tmp_path)).invoke(
        invocation()
    )

    committed = ledger(tmp_path).read_records()[-1]
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.MALFORMED_OUTPUT
    assert committed.record_type == AIEvidenceRecordType.PROVIDER_RESULT_FAILED
    assert committed.failure_classification == ProviderFailureClass.MALFORMED_OUTPUT.value
    assert committed.output_digest is None


def test_truncated_tail_is_removed_without_losing_valid_history(tmp_path: Path) -> None:
    evidence = ledger(tmp_path)
    committed = evidence.append(record())
    with evidence.path.open("ab") as output:
        output.write(b'{"truncated":')

    recovered = ledger(tmp_path).read_records()
    assert recovered == (committed,)
    assert evidence.path.read_bytes().endswith(b"\n")


def test_unsupported_schema_and_out_of_order_sequence_fail_closed(tmp_path: Path) -> None:
    evidence = ledger(tmp_path)
    evidence.append(record())
    payload = json.loads(evidence.path.read_text(encoding="utf-8"))
    payload["ledger_version"] = 2
    evidence.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(AIEvidenceCorruptionError, match="unsupported"):
        evidence.read_records()

    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    second = ledger(fresh_root)
    second.append(record("ai-evidence-" + "2" * 64))
    payload = json.loads(second.path.read_text(encoding="utf-8"))
    payload["ledger_sequence"] = 2
    second.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(AIEvidenceCorruptionError, match="sequence"):
        second.read_records()


def test_credential_bearing_metadata_is_rejected() -> None:
    with pytest.raises(ValueError, match="credential-bearing"):
        replace(record(), provider_metadata=(("authorization", "redacted"),))


@pytest.mark.parametrize(
    ("transport", "classification"),
    [
        (
            FakeTransport(post=GemmaTransportError(GemmaTransportFailure.UNAVAILABLE)),
            ProviderFailureClass.UNAVAILABLE,
        ),
        (
            FakeTransport(post=GemmaTransportError(GemmaTransportFailure.TIMEOUT)),
            ProviderFailureClass.TIMEOUT,
        ),
        (
            FakeTransport(post={"message": {"content": "not-json"}}),
            ProviderFailureClass.MALFORMED_OUTPUT,
        ),
    ],
)
def test_local_gemma_provider_failure_modes(transport, classification) -> None:
    result = LocalGemmaProvider(config(), transport=transport).invoke(invocation())
    assert result.failure is not None
    assert result.failure.classification == classification
    assert result.output is None
    assert result.broker_submission is False


def test_model_and_capability_mismatch_fail_before_generation() -> None:
    transport = FakeTransport()
    provider = LocalGemmaProvider(config(), transport=transport)

    model_failure = provider.invoke(invocation(model_id="gemma2:2b"))
    capability_failure = provider.invoke(invocation(capability=Capability.EMBEDDINGS))

    assert model_failure.failure is not None
    assert model_failure.failure.classification == ProviderFailureClass.MODEL_IDENTITY_MISMATCH
    assert capability_failure.failure is not None
    assert capability_failure.failure.classification == ProviderFailureClass.CAPABILITY_MISMATCH
    assert transport.calls == []


def test_disabled_provider_and_absent_configuration_are_startup_safe(tmp_path: Path) -> None:
    provider = LocalGemmaProvider(
        LocalGemmaConfig.from_environment({}),
        transport=FakeTransport(),
        ledger=ledger(tmp_path),
    )

    assert provider.health_probe().classification == "provider_disabled"
    result = provider.invoke(invocation(model_id="gemma-unconfigured"))
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.UNAVAILABLE
    assert provider.registration().enabled is False
    assert provider.registration().health == ProviderHealth.UNAVAILABLE
    assert result.evidence.broker_submission is False


def test_missing_model_and_corrupt_ledger_do_not_break_provider_startup(tmp_path: Path) -> None:
    evidence = ledger(tmp_path)
    evidence.path.write_text('{"corrupt":true}\n', encoding="utf-8")
    provider = LocalGemmaProvider(
        config(),
        transport=FakeTransport(model="different-gemma"),
        ledger=evidence,
    )

    health = provider.health_probe()
    assert health.health == ProviderHealth.UNAVAILABLE
    assert health.classification == "model_unavailable"
    assert provider.registration().health == ProviderHealth.UNAVAILABLE
    with pytest.raises(AIEvidenceCorruptionError):
        evidence.read_records()


def test_endpoint_configuration_rejects_embedded_credentials() -> None:
    with pytest.raises(GemmaConfigurationError, match="credential-free"):
        LocalGemmaConfig(
            enabled=True,
            endpoint="http://user:secret@127.0.0.1:11434",
            model_id="gemma3:4b",
        )


def routing_request(*, fallback: bool = True) -> RoutingRequest:
    return RoutingRequest(
        request_id="routing-request",
        task_correlation_id="routing-task",
        evidence_correlation_id="routing-evidence",
        responsibility=Responsibility.ANALYSIS,
        required_capabilities=frozenset({Capability.REASONING}),
        preferred_model_family="gemma",
        privacy_requirement=PrivacyTier.LOCAL_ONLY,
        maximum_cost_class=CostClass.STANDARD,
        execution_location_preference=(ExecutionLocation.LOCAL, ExecutionLocation.FLEET),
        minimum_trust_tier=TrustTier.RESTRICTED,
        timeout_ms=1_000,
        fallback_allowed=fallback,
    )


def fallback_model() -> ModelRegistration:
    return ModelRegistration(
        model_id="fleet-specialist",
        provider_id="fleet-runtime",
        family="specialist",
        version="1.0.0",
        capabilities=frozenset({Capability.REASONING}),
        execution_location=ExecutionLocation.FLEET,
        context_limit=8_192,
        supported_input_types=frozenset({InputType.TEXT}),
        structured_output=True,
        cost_class=CostClass.LOW,
        trust_tier=TrustTier.TRUSTED,
        privacy_tier=PrivacyTier.GOVERNED_REMOTE,
        health=ProviderHealth.HEALTHY,
        enabled=True,
        allowed_responsibilities=frozenset({Responsibility.ANALYSIS}),
    )


def test_local_gemma_is_preferred_when_healthy() -> None:
    provider = LocalGemmaProvider(config(), transport=FakeTransport())
    assert provider.health_probe().health == ProviderHealth.HEALTHY
    registry = GovernedModelRegistry(
        providers=(provider.identity,),
        models=(provider.registration(),),
    )
    decision = GovernedModelRouter(registry).route(
        routing_request(), decision_timestamp="2026-08-01T15:00:00Z"
    )
    assert decision.selected_model_id == "gemma3:4b"


def test_safe_fallback_and_disabled_fallback_behavior() -> None:
    disabled = LocalGemmaProvider(config(enabled=False))
    specialist = fallback_model()
    registry = GovernedModelRegistry(
        providers=(
            disabled.identity,
            ProviderIdentity("fleet-runtime", ExecutionLocation.FLEET),
        ),
        models=(disabled.registration(), specialist),
    )

    allowed = GovernedModelRouter(registry).route(
        replace(
            routing_request(),
            privacy_requirement=PrivacyTier.GOVERNED_REMOTE,
        ),
        decision_timestamp="2026-08-01T15:00:00Z",
    )
    blocked = GovernedModelRouter(registry).route(
        replace(
            routing_request(fallback=False),
            privacy_requirement=PrivacyTier.GOVERNED_REMOTE,
        ),
        decision_timestamp="2026-08-01T15:00:00Z",
    )
    assert allowed.selected_model_id == "fleet-specialist"
    assert allowed.fallback is True
    assert blocked.failure_class == RoutingFailureClass.PREFERRED_ROUTE_UNAVAILABLE


def test_routing_and_fallback_decisions_are_durable(tmp_path: Path) -> None:
    provider = LocalGemmaProvider(config(), transport=FakeTransport())
    provider.health_probe()
    local_registry = GovernedModelRegistry(
        providers=(provider.identity,), models=(provider.registration(),)
    )
    local_request = routing_request()
    local_decision = GovernedModelRouter(local_registry).route(
        local_request, decision_timestamp="2026-08-01T15:00:00Z"
    )
    evidence = ledger(tmp_path)
    append_routing_decision(
        evidence,
        request=local_request,
        decision=local_decision,
        model_version=provider.model_version,
        execution_location=ExecutionLocation.LOCAL,
    )

    disabled = LocalGemmaProvider(config(enabled=False))
    specialist = fallback_model()
    fallback_registry = GovernedModelRegistry(
        providers=(
            disabled.identity,
            ProviderIdentity("fleet-runtime", ExecutionLocation.FLEET),
        ),
        models=(disabled.registration(), specialist),
    )
    fallback_request = replace(
        routing_request(),
        request_id="fallback-request",
        privacy_requirement=PrivacyTier.GOVERNED_REMOTE,
    )
    fallback_decision = GovernedModelRouter(fallback_registry).route(
        fallback_request, decision_timestamp="2026-08-01T15:00:01Z"
    )
    append_routing_decision(
        evidence,
        request=fallback_request,
        decision=fallback_decision,
        model_version=specialist.version,
        execution_location=ExecutionLocation.FLEET,
    )

    assert [item.record_type for item in evidence.read_records()] == [
        AIEvidenceRecordType.ROUTING_DECISION,
        AIEvidenceRecordType.FALLBACK_DECISION,
    ]


def test_hermes_handoff_is_digest_only_and_advisory() -> None:
    work = GovernedModelWorkRequest(
        request_id="hermes-request",
        task_correlation_id="hermes-task",
        evidence_correlation_id="hermes-evidence",
        capability=Capability.REASONING,
        responsibility=Responsibility.ANALYSIS,
        privacy_requirement=PrivacyTier.LOCAL_ONLY,
        evidence_context=("sha256:" + "f" * 64,),
        expected_output_contract="sigil.ai.output.v1",
    )
    route = work.routing_request()
    assert route.preferred_model_family == "gemma"
    assert route.allowed_provider_ids is None
    admitted = replace(work, allowed_provider_ids=frozenset({"hermes-claude"}))
    assert admitted.routing_request().allowed_provider_ids == frozenset({"hermes-claude"})
    assert work.paper_only is True
    assert work.broker_submission is False

    with pytest.raises(ValueError, match="digest references"):
        replace(work, evidence_context=("raw-prompt",))
    with pytest.raises(ValueError, match="cannot be empty"):
        replace(work, allowed_provider_ids=frozenset())
    with pytest.raises(ValueError, match="stable lowercase"):
        replace(work, allowed_provider_ids=frozenset({"Invalid Provider"}))


def test_adapter_has_no_portfolio_approval_or_execution_authority(tmp_path: Path) -> None:
    provider = LocalGemmaProvider(config(), transport=FakeTransport(), ledger=ledger(tmp_path))
    result = provider.invoke(invocation())
    assert result.paper_only is True
    assert result.broker_submission is False
    assert not hasattr(provider, "approve_proposal")
    assert not hasattr(provider, "authorize_capital")
    assert not hasattr(provider, "submit_broker_order")
    assert {item.name for item in tmp_path.iterdir()} == {"governed-ai-evidence-v1"}
