from dataclasses import FrozenInstanceError

import pytest

from sigil.ai import (
    Capability,
    CostClass,
    DeterministicProvider,
    DeterministicProviderMode,
    ExecutionLocation,
    GovernedModelRegistry,
    GovernedModelRouter,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderFailureClass,
    ProviderHealth,
    ProviderIdentity,
    ProviderInvocation,
    RegistryValidationError,
    Responsibility,
    RoutingFailureClass,
    RoutingRequest,
    TrustTier,
)


def model(
    model_id: str,
    provider_id: str,
    family: str,
    location: ExecutionLocation,
    capabilities: frozenset[Capability],
    *,
    health: ProviderHealth = ProviderHealth.HEALTHY,
    privacy: PrivacyTier = PrivacyTier.LOCAL_ONLY,
    trust: TrustTier = TrustTier.TRUSTED,
    cost: CostClass = CostClass.FREE,
    responsibilities: frozenset[Responsibility] = frozenset({Responsibility.ANALYSIS}),
) -> ModelRegistration:
    return ModelRegistration(
        model_id=model_id,
        provider_id=provider_id,
        family=family,
        version="1.0.0",
        capabilities=capabilities,
        execution_location=location,
        context_limit=8_192,
        supported_input_types=frozenset({InputType.TEXT, InputType.STRUCTURED_JSON}),
        structured_output=True,
        cost_class=cost,
        trust_tier=trust,
        privacy_tier=privacy,
        health=health,
        enabled=True,
        allowed_responsibilities=responsibilities,
    )


def registry(*models: ModelRegistration) -> GovernedModelRegistry:
    provider_locations = {item.provider_id: item.execution_location for item in models}
    return GovernedModelRegistry(
        providers=tuple(
            ProviderIdentity(provider_id, location)
            for provider_id, location in sorted(provider_locations.items())
        ),
        models=tuple(models),
    )


def request(
    capability: Capability = Capability.REASONING,
    *,
    family: str | None = "gemma",
    responsibility: Responsibility = Responsibility.ANALYSIS,
    privacy: PrivacyTier = PrivacyTier.LOCAL_ONLY,
    trust: TrustTier = TrustTier.RESTRICTED,
    fallback: bool = True,
    allowed_provider_ids: frozenset[str] | None = None,
) -> RoutingRequest:
    return RoutingRequest(
        request_id="request-001",
        task_correlation_id="task-001",
        evidence_correlation_id="evidence-001",
        responsibility=responsibility,
        required_capabilities=frozenset({capability}),
        preferred_model_family=family,
        privacy_requirement=privacy,
        maximum_cost_class=CostClass.STANDARD,
        execution_location_preference=(
            ExecutionLocation.LOCAL,
            ExecutionLocation.FLEET,
            ExecutionLocation.EXTERNAL,
        ),
        minimum_trust_tier=trust,
        timeout_ms=1_000,
        fallback_allowed=fallback,
        allowed_provider_ids=allowed_provider_ids,
    )


def test_valid_registration_and_registry_revision_are_immutable() -> None:
    gemma = model(
        "gemma-local-1",
        "local-runtime",
        "gemma",
        ExecutionLocation.LOCAL,
        frozenset({Capability.REASONING, Capability.STRUCTURED_GENERATION}),
    )
    catalog = registry(gemma)

    assert catalog.revision.startswith("sha256:")
    assert len(catalog.revision) == 71
    with pytest.raises(FrozenInstanceError):
        gemma.model_id = "changed"  # type: ignore[misc]


def test_invalid_and_duplicate_registry_entries_fail_closed() -> None:
    with pytest.raises(ValueError, match="stable lowercase"):
        ProviderIdentity("Invalid Provider", ExecutionLocation.LOCAL)

    gemma = model(
        "gemma-local-1",
        "local-runtime",
        "gemma",
        ExecutionLocation.LOCAL,
        frozenset({Capability.REASONING}),
    )
    with pytest.raises(RegistryValidationError, match="duplicate model identity"):
        GovernedModelRegistry(
            providers=(ProviderIdentity("local-runtime", ExecutionLocation.LOCAL),),
            models=(gemma, gemma),
        )

    decision = GovernedModelRouter.route_registry_data(
        providers=(ProviderIdentity("local-runtime", ExecutionLocation.LOCAL),),
        models=(gemma, gemma),
        request=request(),
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    assert decision.failure_class == RoutingFailureClass.REGISTRY_INVALID
    assert decision.selected_model_id is None
    assert decision.registry_revision.startswith("invalid:sha256:")


def test_local_gemma_preference_and_candidate_order_are_deterministic() -> None:
    candidates = (
        model(
            "specialized-reasoner",
            "specialized",
            "specialized",
            ExecutionLocation.FLEET,
            frozenset({Capability.REASONING}),
        ),
        model(
            "gemma-fleet",
            "fleet-runtime",
            "gemma",
            ExecutionLocation.FLEET,
            frozenset({Capability.REASONING}),
        ),
        model(
            "gemma-local",
            "local-runtime",
            "gemma",
            ExecutionLocation.LOCAL,
            frozenset({Capability.REASONING}),
        ),
    )
    router = GovernedModelRouter(registry(*reversed(candidates)))

    first = router.route(request(), decision_timestamp="2026-08-01T12:00:00Z")
    second = router.route(request(), decision_timestamp="2026-08-01T12:00:00Z")

    assert first.selected_model_id == "gemma-local"
    assert first.evidence_identity == second.evidence_identity
    assert first.considered_candidates == second.considered_candidates
    assert first.broker_submission is False
    assert first.paper_only is True


def test_capability_suitability_selects_specialized_model() -> None:
    gemma = model(
        "gemma-local",
        "local-runtime",
        "gemma",
        ExecutionLocation.LOCAL,
        frozenset({Capability.REASONING}),
    )
    finbert = model(
        "finbert-governed",
        "specialized-runtime",
        "finbert",
        ExecutionLocation.FLEET,
        frozenset({Capability.FINANCIAL_SENTIMENT}),
        privacy=PrivacyTier.GOVERNED_REMOTE,
        responsibilities=frozenset({Responsibility.SENTIMENT}),
    )
    decision = GovernedModelRouter(registry(gemma, finbert)).route(
        request(
            Capability.FINANCIAL_SENTIMENT,
            responsibility=Responsibility.SENTIMENT,
            privacy=PrivacyTier.GOVERNED_REMOTE,
        ),
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert decision.selected_model_id == "finbert-governed"
    assert decision.fallback is True


def test_privacy_trust_health_and_prohibited_responsibility_fail_closed() -> None:
    external = model(
        "external-model",
        "external-runtime",
        "external",
        ExecutionLocation.EXTERNAL,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        health=ProviderHealth.UNAVAILABLE,
        cost=CostClass.STANDARD,
    )
    router = GovernedModelRouter(registry(external))

    blocked = router.route(request(), decision_timestamp="2026-08-01T12:00:00Z")
    reasons = blocked.considered_candidates[0].rejection_reasons
    assert blocked.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert "model_unhealthy" in reasons
    assert "privacy_requirement_unmet" in reasons

    prohibited = router.route(
        request(responsibility=Responsibility.SUBMIT_BROKER_ORDER),
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    assert prohibited.failure_class == RoutingFailureClass.PROHIBITED_RESPONSIBILITY
    assert prohibited.selected_model_id is None
    assert prohibited.broker_submission is False


def test_provider_health_rejection_is_recorded() -> None:
    gemma = model(
        "gemma-local",
        "local-runtime",
        "gemma",
        ExecutionLocation.LOCAL,
        frozenset({Capability.REASONING}),
    )
    catalog = GovernedModelRegistry(
        providers=(
            ProviderIdentity(
                "local-runtime",
                ExecutionLocation.LOCAL,
                health=ProviderHealth.UNAVAILABLE,
            ),
        ),
        models=(gemma,),
    )
    decision = GovernedModelRouter(catalog).route(
        request(), decision_timestamp="2026-08-01T12:00:00Z"
    )

    assert decision.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert "provider_unhealthy" in decision.considered_candidates[0].rejection_reasons


def test_fallback_disabled_rejects_nonpreferred_route() -> None:
    fleet_gemma = model(
        "gemma-fleet",
        "fleet-runtime",
        "gemma",
        ExecutionLocation.FLEET,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.GOVERNED_REMOTE,
    )
    decision = GovernedModelRouter(registry(fleet_gemma)).route(
        request(privacy=PrivacyTier.GOVERNED_REMOTE, fallback=False),
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert decision.failure_class == RoutingFailureClass.PREFERRED_ROUTE_UNAVAILABLE
    assert decision.selected_model_id is None


def invocation(capability: Capability = Capability.REASONING) -> ProviderInvocation:
    return ProviderInvocation(
        request_id="request-001",
        task_correlation_id="task-001",
        model_id="gemma-test",
        registry_revision="sha256:" + "a" * 64,
        capability=capability,
        input_payload={"document_digest": "sha256:sanitized"},
        timeout_ms=1_000,
        started_at="2026-08-01T12:00:00Z",
        ended_at="2026-08-01T12:00:01Z",
    )


@pytest.mark.parametrize(
    ("mode", "failure_class"),
    [
        (DeterministicProviderMode.UNAVAILABLE, ProviderFailureClass.UNAVAILABLE),
        (DeterministicProviderMode.TIMEOUT, ProviderFailureClass.TIMEOUT),
        (DeterministicProviderMode.MALFORMED_OUTPUT, ProviderFailureClass.MALFORMED_OUTPUT),
    ],
)
def test_deterministic_provider_classifies_failures(mode, failure_class) -> None:
    result = DeterministicProvider(mode=mode).invoke(invocation())

    assert result.failure is not None
    assert result.failure.classification == failure_class
    assert result.output is None
    assert result.evidence.failure_classification == failure_class.value
    assert result.broker_submission is False
    assert result.paper_only is True


def test_deterministic_provider_structured_output_and_evidence_identity() -> None:
    provider = DeterministicProvider()
    first = provider.invoke(invocation())
    second = provider.invoke(invocation())

    assert first.succeeded
    assert first.output == {
        "schema_version": 1,
        "status": "ok",
        "request_id": "request-001",
        "result": "deterministic-structured-output",
    }
    assert first.evidence == second.evidence
    assert first.evidence.evidence_identity.startswith("sha256:")
    assert first.evidence.input_digest.startswith("sha256:")
    assert first.evidence.output_digest is not None
    assert first.evidence.broker_submission is False


def test_evidence_digests_sensitive_input_without_persisting_it() -> None:
    sensitive_value = "do-not-persist-this-prompt"
    original = invocation()
    result = DeterministicProvider().invoke(
        ProviderInvocation(
            request_id=original.request_id,
            task_correlation_id=original.task_correlation_id,
            model_id=original.model_id,
            registry_revision=original.registry_revision,
            capability=original.capability,
            input_payload={"prompt": sensitive_value},
            timeout_ms=original.timeout_ms,
            started_at=original.started_at,
            ended_at=original.ended_at,
        )
    )

    assert sensitive_value not in repr(result.evidence)
    assert result.evidence.input_digest.startswith("sha256:")


def test_credential_bearing_provider_metadata_is_rejected() -> None:
    with pytest.raises(ValueError, match="credential-bearing"):
        ProviderIdentity(
            "unsafe-provider",
            ExecutionLocation.EXTERNAL,
            metadata=(("api_key", "redacted"),),
        )


def test_capability_mismatch_is_structured_and_paper_only() -> None:
    result = DeterministicProvider().invoke(invocation(Capability.EMBEDDINGS))

    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.CAPABILITY_MISMATCH
    assert result.evidence.broker_submission is False
    assert result.evidence.paper_only is True


def test_external_provider_requires_explicit_admission() -> None:
    external = model(
        "claude-advisory",
        "hermes-claude",
        "claude",
        ExecutionLocation.EXTERNAL,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        cost=CostClass.STANDARD,
    )
    router = GovernedModelRouter(registry(external))

    implicit = router.route(
        request(privacy=PrivacyTier.EXTERNAL_APPROVED),
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    unlisted = router.route(
        request(
            privacy=PrivacyTier.EXTERNAL_APPROVED,
            allowed_provider_ids=frozenset({"local-runtime"}),
        ),
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    for decision in (implicit, unlisted):
        assert decision.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
        assert decision.selected_provider_id is None
        assert decision.selected_model_id is None
        assert decision.considered_candidates[0].rejection_reasons == (
            "external_provider_not_explicitly_admitted",
        )


def test_explicit_provider_admission_allows_advisory_external_route() -> None:
    external = model(
        "claude-advisory",
        "hermes-claude",
        "claude",
        ExecutionLocation.EXTERNAL,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        cost=CostClass.STANDARD,
    )
    router = GovernedModelRouter(registry(external))
    route_request = request(
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        allowed_provider_ids=frozenset({"hermes-claude"}),
    )

    first = router.route(
        route_request,
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    second = router.route(
        route_request,
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert first.selected_provider_id == "hermes-claude"
    assert first.selected_model_id == "claude-advisory"
    assert first.fallback is True
    assert first.evidence_identity == second.evidence_identity
    assert first.considered_candidates == second.considered_candidates
    assert first.paper_only is True
    assert first.broker_submission is False


def test_provider_admission_does_not_override_prohibited_responsibility() -> None:
    external = model(
        "claude-advisory",
        "hermes-claude",
        "claude",
        ExecutionLocation.EXTERNAL,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        cost=CostClass.STANDARD,
    )
    decision = GovernedModelRouter(registry(external)).route(
        request(
            responsibility=Responsibility.SUBMIT_BROKER_ORDER,
            privacy=PrivacyTier.EXTERNAL_APPROVED,
            allowed_provider_ids=frozenset({"hermes-claude"}),
        ),
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert decision.failure_class == RoutingFailureClass.PROHIBITED_RESPONSIBILITY
    assert decision.selected_provider_id is None
    assert decision.selected_model_id is None
    assert decision.broker_submission is False


def test_provider_admission_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        request(allowed_provider_ids=frozenset())

    with pytest.raises(ValueError, match="stable lowercase"):
        request(allowed_provider_ids=frozenset({"Invalid Provider"}))


def test_local_and_fleet_routes_do_not_require_explicit_provider_admission() -> None:
    local = model(
        "gemma-local",
        "local-runtime",
        "gemma",
        ExecutionLocation.LOCAL,
        frozenset({Capability.REASONING}),
    )
    fleet = model(
        "gemma-fleet",
        "fleet-runtime",
        "gemma",
        ExecutionLocation.FLEET,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.GOVERNED_REMOTE,
    )

    local_decision = GovernedModelRouter(registry(local)).route(
        request(),
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    fleet_decision = GovernedModelRouter(registry(fleet)).route(
        request(privacy=PrivacyTier.GOVERNED_REMOTE),
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert local_decision.selected_provider_id == "local-runtime"
    assert local_decision.selected_model_id == "gemma-local"
    assert fleet_decision.selected_provider_id == "fleet-runtime"
    assert fleet_decision.selected_model_id == "gemma-fleet"


def test_external_admission_policy_is_deterministic_and_audited() -> None:
    external = model(
        "claude-advisory",
        "hermes-claude",
        "claude",
        ExecutionLocation.EXTERNAL,
        frozenset({Capability.REASONING}),
        privacy=PrivacyTier.EXTERNAL_APPROVED,
        trust=TrustTier.RESTRICTED,
        cost=CostClass.STANDARD,
    )
    router = GovernedModelRouter(registry(external))
    route_request = request(privacy=PrivacyTier.EXTERNAL_APPROVED)

    first = router.route(
        route_request,
        decision_timestamp="2026-08-01T12:00:00Z",
    )
    second = router.route(
        route_request,
        decision_timestamp="2026-08-01T12:00:00Z",
    )

    assert first.evidence_identity == second.evidence_identity
    assert first.considered_candidates == second.considered_candidates
    assert first.considered_candidates[0].rejection_reasons == (
        "external_provider_not_explicitly_admitted",
    )
    assert first.paper_only is True
    assert first.broker_submission is False
