from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigil.ai import (
    AnalysisArtifactConflictError,
    AnalysisArtifactCorruptionError,
    AnalysisFailureClass,
    AnalysisValidationError,
    Capability,
    CostClass,
    DurableAIEvidenceLedger,
    DurableAnalysisArtifactStore,
    ExecutionLocation,
    GenericAnalysisPayload,
    GovernedAnalysisRequest,
    GovernedAnalysisService,
    GovernedModelRegistry,
    GovernedModelWorkRequest,
    GovernedOutputSchema,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderFailure,
    ProviderFailureClass,
    ProviderHealth,
    ProviderIdentity,
    ProviderResult,
    Responsibility,
    RoutingFailureClass,
    TrustTier,
    build_analysis_artifact,
    build_invocation_evidence,
    validate_generic_analysis,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-01T16:00:00Z"
LATER = "2026-08-01T16:00:01Z"


def valid_output(reference: str = DIGEST_B) -> dict[str, object]:
    return {
        "summary": "Evidence-backed advisory analysis.",
        "findings": ["Finding one"],
        "risks": ["Evidence may become stale"],
        "evidence_references": [reference],
        "limitations": ["Paper-only analysis"],
        "confidence": 0.75,
    }


class FakeProvider:
    input_contract = "application/json;schema=sigil.ai.input.v1"
    output_contract = "application/json;schema=sigil.ai.output.v1"
    request_timeout_ms = 1_000
    model_family = "gemma"

    def __init__(
        self,
        *,
        provider_id: str = "local-runtime",
        model_id: str = "gemma-analysis",
        model_version: str = "1.0.0",
        output: object | None = None,
        failure: ProviderFailureClass | None = None,
        capabilities: frozenset[Capability] = frozenset({Capability.REASONING}),
        location: ExecutionLocation = ExecutionLocation.LOCAL,
    ) -> None:
        self.identity = ProviderIdentity(provider_id, location)
        self.model_id = model_id
        self.model_version = model_version
        self.output = valid_output() if output is None else output
        self.failure_class = failure
        self.capabilities = capabilities
        self.calls = 0

    def invoke(self, invocation) -> ProviderResult:
        self.calls += 1
        failure = (
            None
            if self.failure_class is None
            else ProviderFailure(self.failure_class, "Provider failed safely.", True)
        )
        output = self.output if failure is None else None
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=self.identity.execution_location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=output,
            provider_metadata=(("adapter", "fake-v1"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)


def registration(
    provider: FakeProvider,
    *,
    health: ProviderHealth = ProviderHealth.HEALTHY,
    privacy: PrivacyTier = PrivacyTier.LOCAL_ONLY,
    trust: TrustTier = TrustTier.TRUSTED,
    enabled: bool = True,
    family: str = "gemma",
) -> ModelRegistration:
    return ModelRegistration(
        model_id=provider.model_id,
        provider_id=provider.identity.provider_id,
        family=family,
        version=provider.model_version,
        capabilities=provider.capabilities,
        execution_location=provider.identity.execution_location,
        context_limit=8_192,
        supported_input_types=frozenset({InputType.STRUCTURED_JSON}),
        structured_output=True,
        cost_class=CostClass.FREE,
        trust_tier=trust,
        privacy_tier=privacy,
        health=health,
        enabled=enabled,
        allowed_responsibilities=frozenset({Responsibility.RESEARCH_ANALYSIS}),
    )


def request(**changes) -> GovernedAnalysisRequest:
    values = {
        "request_id": "analysis-request",
        "task_correlation_id": "analysis-task",
        "requested_capability": Capability.REASONING,
        "responsibility": Responsibility.RESEARCH_ANALYSIS,
        "privacy_requirement": PrivacyTier.LOCAL_ONLY,
        "maximum_cost_class": CostClass.STANDARD,
        "minimum_trust_tier": TrustTier.RESTRICTED,
        "execution_location_preference": (ExecutionLocation.LOCAL, ExecutionLocation.FLEET),
        "fallback_permission": True,
        "timeout_ms": 1_000,
        "input_digest": DIGEST_A,
        "evidence_context_digests": (DIGEST_B,),
        "expected_output_schema": GovernedOutputSchema.GENERIC_ANALYSIS_V1,
        "requested_at": NOW,
    }
    values.update(changes)
    return GovernedAnalysisRequest(**values)


def service(
    tmp_path: Path,
    provider: FakeProvider,
    *,
    model: ModelRegistration | None = None,
    enabled: bool = True,
    extra_providers: tuple[ProviderIdentity, ...] = (),
    extra_models: tuple[ModelRegistration, ...] = (),
) -> GovernedAnalysisService:
    selected = model or registration(provider)
    registry = GovernedModelRegistry(
        providers=(provider.identity, *extra_providers),
        models=(selected, *extra_models),
    )
    return GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=DurableAIEvidenceLedger(tmp_path.resolve()),
        artifact_store=DurableAnalysisArtifactStore(tmp_path.resolve()),
        enabled=enabled,
    )


def test_successful_analysis_creates_durable_artifact_and_evidence(tmp_path: Path) -> None:
    provider = FakeProvider()
    analysis = service(tmp_path, provider)
    response = analysis.analyze(request(), completed_at=LATER)

    assert response.succeeded
    assert response.artifact is not None
    assert response.artifact.structured_payload.summary.startswith("Evidence-backed")
    assert response.artifact.paper_only is True
    assert response.artifact.execution_authorized is False
    assert response.artifact.broker_submission is False
    assert response.artifact.portfolio_mutation is False
    assert response.artifact.approval_authority is False
    assert len(analysis.evidence_ledger.read_records()) == 3
    assert analysis.artifact_store.read_artifacts() == (response.artifact,)


def test_artifact_restart_persistence_and_deterministic_identity(tmp_path: Path) -> None:
    first = service(tmp_path, FakeProvider())
    response = first.analyze(request(), completed_at=LATER)
    assert response.artifact is not None
    restarted = DurableAnalysisArtifactStore(tmp_path.resolve())
    assert restarted.read_artifacts()[0] == response.artifact

    payload = response.artifact.structured_payload
    duplicate = build_analysis_artifact(
        request_id="analysis-request",
        task_correlation_id="analysis-task",
        provider_id="local-runtime",
        model_id="gemma-analysis",
        model_version="1.0.0",
        capability=Capability.REASONING,
        responsibility=Responsibility.RESEARCH_ANALYSIS,
        created_at=LATER,
        routing_evidence_id=response.routing_evidence_id,
        invocation_evidence_id=response.invocation_evidence_id,
        input_digest=DIGEST_A,
        output_digest=response.artifact.output_digest,
        structured_payload=payload,
        citations=payload.evidence_references,
        confidence=payload.confidence,
        limitations=payload.limitations,
        stale_after=None,
    )
    assert duplicate.artifact_id == response.artifact.artifact_id
    with pytest.raises(AnalysisArtifactConflictError):
        restarted.append(duplicate)


def test_artifact_store_malformed_version_and_truncated_tail(tmp_path: Path) -> None:
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    store.path.write_text('{"malformed":true}\n', encoding="utf-8")
    with pytest.raises(AnalysisArtifactCorruptionError):
        store.read_artifacts()

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    other = DurableAnalysisArtifactStore(fresh.resolve())
    artifact = build_analysis_artifact(
        request_id="analysis-request",
        task_correlation_id="analysis-task",
        provider_id="local-runtime",
        model_id="gemma-analysis",
        model_version="1.0.0",
        capability=Capability.REASONING,
        responsibility=Responsibility.RESEARCH_ANALYSIS,
        created_at=LATER,
        routing_evidence_id=DIGEST_A,
        invocation_evidence_id=DIGEST_B,
        input_digest=DIGEST_A,
        output_digest=DIGEST_B,
        structured_payload=GenericAnalysisPayload("summary", (), (), (DIGEST_B,), (), None),
        citations=(DIGEST_B,),
        confidence=None,
        limitations=(),
        stale_after=None,
    )
    other.append(artifact)
    with other.path.open("ab") as output:
        output.write(b'{"truncated":')
    assert other.read_artifacts() == (artifact,)

    envelope = json.loads(other.path.read_text(encoding="utf-8"))
    envelope["store_version"] = 99
    other.path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
    with pytest.raises(AnalysisArtifactCorruptionError, match="unsupported"):
        other.read_artifacts()


@pytest.mark.parametrize(
    "responsibility",
    [
        Responsibility.CAPITAL_AUTHORIZATION,
        Responsibility.PROPOSAL_APPROVAL,
        Responsibility.POLICY_CHANGE,
        Responsibility.BROKER_SUBMISSION,
        Responsibility.ORDER_EXECUTION,
        Responsibility.PORTFOLIO_MUTATION,
        Responsibility.CREDENTIAL_ACCESS,
        Responsibility.UNRESTRICTED_SHELL_EXECUTION,
    ],
)
def test_prohibited_responsibilities_are_rejected(responsibility) -> None:
    with pytest.raises(AnalysisValidationError, match="prohibited"):
        request(responsibility=responsibility)


@pytest.mark.parametrize(
    "output",
    [
        {"summary": "missing fields"},
        {**valid_output(), "summary": "submit order now"},
        {**valid_output(), "summary": "authorization: Bearer abc"},
        {**valid_output(), "summary": "x" * 40_000},
        {**valid_output(), "evidence_references": [DIGEST_A]},
    ],
)
def test_unsafe_or_malformed_structured_output_is_rejected(output) -> None:
    with pytest.raises(AnalysisValidationError):
        validate_generic_analysis(output, trusted_evidence=(DIGEST_B,))


def test_provider_malformed_output_creates_failure_evidence(tmp_path: Path) -> None:
    analysis = service(tmp_path, FakeProvider(output={"summary": "bad"}))
    response = analysis.analyze(request(), completed_at=LATER)
    assert response.failure_classification == AnalysisFailureClass.OUTPUT_VALIDATION_FAILED
    assert response.artifact is None
    assert len(analysis.evidence_ledger.read_records()) == 4


@pytest.mark.parametrize(
    "classification",
    [
        ProviderFailureClass.UNAVAILABLE,
        ProviderFailureClass.TIMEOUT,
        ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
        ProviderFailureClass.CAPABILITY_MISMATCH,
    ],
)
def test_provider_failures_are_fail_closed(tmp_path: Path, classification) -> None:
    response = service(tmp_path, FakeProvider(failure=classification)).analyze(
        request(), completed_at=LATER
    )
    assert response.failure_classification == classification.value
    assert response.artifact is None
    assert response.broker_submission is False


def test_privacy_trust_and_no_candidate_fail_closed(tmp_path: Path) -> None:
    provider = FakeProvider(location=ExecutionLocation.EXTERNAL)
    weak = registration(
        provider,
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
    )
    analysis = service(tmp_path, provider, model=weak)
    response = analysis.analyze(request(minimum_trust_tier=TrustTier.TRUSTED), completed_at=LATER)
    assert response.failure_classification == RoutingFailureClass.NO_SUITABLE_MODEL.value
    assert provider.calls == 0


def test_fallback_allowed_and_disabled(tmp_path: Path) -> None:
    local = FakeProvider()
    disabled_local = registration(local, health=ProviderHealth.UNAVAILABLE, enabled=False)
    fleet = FakeProvider(
        provider_id="fleet-runtime",
        model_id="fleet-model",
        location=ExecutionLocation.FLEET,
    )
    fleet_model = registration(
        fleet,
        privacy=PrivacyTier.GOVERNED_REMOTE,
        family="specialist",
    )
    allowed = service(
        tmp_path,
        local,
        model=disabled_local,
        extra_providers=(fleet.identity,),
        extra_models=(fleet_model,),
    )
    allowed.providers[fleet.identity.provider_id] = fleet
    response = allowed.analyze(
        request(privacy_requirement=PrivacyTier.GOVERNED_REMOTE), completed_at=LATER
    )
    assert response.succeeded
    assert response.routing_summary == "fallback selected"

    second_root = tmp_path / "second"
    second_root.mkdir()
    blocked = service(
        second_root,
        local,
        model=disabled_local,
        extra_providers=(fleet.identity,),
        extra_models=(fleet_model,),
    )
    blocked.providers[fleet.identity.provider_id] = fleet
    denied = blocked.analyze(
        request(
            request_id="blocked-request",
            privacy_requirement=PrivacyTier.GOVERNED_REMOTE,
            fallback_permission=False,
        ),
        completed_at=LATER,
    )
    assert denied.failure_classification == RoutingFailureClass.PREFERRED_ROUTE_UNAVAILABLE.value


def test_service_disabled_and_status_are_startup_safe(tmp_path: Path) -> None:
    analysis = service(tmp_path, FakeProvider(), enabled=False)
    response = analysis.analyze(request(), completed_at=LATER)
    status = analysis.status()
    assert response.failure_classification == AnalysisFailureClass.SERVICE_DISABLED
    assert status.enabled is False
    assert status.evidence_ledger_health == "healthy"
    assert status.artifact_store_health == "healthy"
    assert status.paper_only is True
    assert status.broker_submission is False


def test_corrupt_persistence_fails_closed_without_provider_authority(tmp_path: Path) -> None:
    provider = FakeProvider()
    analysis = service(tmp_path, provider)
    analysis.evidence_ledger.path.write_text('{"corrupt":true}\n', encoding="utf-8")

    response = analysis.analyze(request(), completed_at=LATER)
    assert response.failure_classification == AnalysisFailureClass.EVIDENCE_PERSISTENCE_FAILED
    assert response.artifact is None
    assert provider.calls == 0
    assert analysis.status().evidence_ledger_health == "corrupt"


def test_capability_mismatch_is_rejected_by_router(tmp_path: Path) -> None:
    provider = FakeProvider()
    response = service(tmp_path, provider).analyze(
        request(requested_capability=Capability.EMBEDDINGS), completed_at=LATER
    )
    assert response.failure_classification == RoutingFailureClass.NO_SUITABLE_MODEL.value
    assert provider.calls == 0


def test_hermes_handoff_success_and_failure(tmp_path: Path) -> None:
    work = GovernedModelWorkRequest(
        request_id="hermes-analysis",
        task_correlation_id="hermes-task",
        evidence_correlation_id="hermes-evidence",
        capability=Capability.REASONING,
        responsibility=Responsibility.RESEARCH_ANALYSIS,
        privacy_requirement=PrivacyTier.LOCAL_ONLY,
        evidence_context=(DIGEST_B,),
        expected_output_contract=GovernedOutputSchema.GENERIC_ANALYSIS_V1.value,
    )
    success = service(tmp_path, FakeProvider()).analyze_hermes(
        work, requested_at=NOW, completed_at=LATER
    )
    assert success.succeeded
    assert success.artifact is not None

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    failure = service(
        failed_root, FakeProvider(failure=ProviderFailureClass.UNAVAILABLE)
    ).analyze_hermes(work, requested_at=NOW, completed_at=LATER)
    assert failure.failure_classification == ProviderFailureClass.UNAVAILABLE.value


def test_provider_admission_blocks_unlisted_fallback_in_analysis_service(
    tmp_path: Path,
) -> None:
    local = FakeProvider()
    disabled_local = registration(local, health=ProviderHealth.UNAVAILABLE, enabled=False)
    external = FakeProvider(
        provider_id="hermes-claude",
        model_id="claude-advisory",
        location=ExecutionLocation.EXTERNAL,
    )
    external_model = registration(
        external,
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        family="claude",
    )
    analysis = service(
        tmp_path,
        local,
        model=disabled_local,
        extra_providers=(external.identity,),
        extra_models=(external_model,),
    )
    analysis.providers[external.identity.provider_id] = external

    blocked = analysis.analyze(
        request(
            privacy_requirement=PrivacyTier.EXTERNAL_APPROVED,
            execution_location_preference=(
                ExecutionLocation.LOCAL,
                ExecutionLocation.EXTERNAL,
            ),
            allowed_provider_ids=frozenset({"local-runtime"}),
        ),
        completed_at=LATER,
    )

    assert blocked.failure_classification == RoutingFailureClass.NO_SUITABLE_MODEL.value
    assert external.calls == 0
    assert blocked.artifact is None


def test_hermes_provider_admission_reaches_analysis_router(tmp_path: Path) -> None:
    external = FakeProvider(
        provider_id="hermes-claude",
        model_id="claude-advisory",
        location=ExecutionLocation.EXTERNAL,
    )
    external_model = registration(
        external,
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        family="claude",
    )
    analysis = service(tmp_path, external, model=external_model)
    work = GovernedModelWorkRequest(
        request_id="hermes-analysis",
        task_correlation_id="hermes-task",
        evidence_correlation_id="hermes-evidence",
        capability=Capability.REASONING,
        responsibility=Responsibility.RESEARCH_ANALYSIS,
        privacy_requirement=PrivacyTier.EXTERNAL_APPROVED,
        evidence_context=(DIGEST_B,),
        expected_output_contract=GovernedOutputSchema.GENERIC_ANALYSIS_V1.value,
        allowed_provider_ids=frozenset({"hermes-claude"}),
    )

    response = analysis.analyze_hermes(
        work,
        requested_at=NOW,
        completed_at=LATER,
    )

    assert response.succeeded
    assert response.artifact is not None
    assert response.artifact.provider_id == "hermes-claude"
    assert response.artifact.model_id == "claude-advisory"
    assert external.calls == 1
    assert response.paper_only is True
    assert response.broker_submission is False


def test_analysis_provider_admission_validation_fails_closed() -> None:
    with pytest.raises(AnalysisValidationError, match="cannot be empty"):
        request(allowed_provider_ids=frozenset())
    with pytest.raises(AnalysisValidationError, match="stable lowercase"):
        request(allowed_provider_ids=frozenset({"Invalid Provider"}))
