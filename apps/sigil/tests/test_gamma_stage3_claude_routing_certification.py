from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    CLAUDE_PROVIDER_ID,
    Capability,
    ClaudeConfig,
    CostClass,
    ExecutionLocation,
    GovernedModelRegistry,
    GovernedModelRouter,
    HermesClaudeProvider,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    RoutingFailureClass,
    RoutingRequest,
    TrustTier,
)
from sigil.ai.models import PROHIBITED_RESPONSIBILITIES

NOW = "2026-08-02T20:45:00Z"


def gemma_registration(
    *,
    health: ProviderHealth = ProviderHealth.HEALTHY,
    enabled: bool = True,
) -> ModelRegistration:
    return ModelRegistration(
        model_id="gemma-governed",
        provider_id="local-gemma",
        family="gemma",
        version="gamma-stage3",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.STRUCTURED_GENERATION,
                Capability.SUMMARIZATION,
            }
        ),
        execution_location=ExecutionLocation.LOCAL,
        context_limit=32_768,
        supported_input_types=frozenset(
            {
                InputType.TEXT,
                InputType.STRUCTURED_JSON,
            }
        ),
        structured_output=True,
        cost_class=CostClass.FREE,
        trust_tier=TrustTier.TRUSTED,
        privacy_tier=PrivacyTier.LOCAL_ONLY,
        health=health,
        enabled=enabled,
        allowed_responsibilities=frozenset(
            {
                Responsibility.ANALYSIS,
                Responsibility.RESEARCH_ANALYSIS,
                Responsibility.EVIDENCE_SUMMARIZATION,
                Responsibility.RISK_ANALYSIS,
            }
        ),
    )


def claude_catalog(
    *,
    provider_health: ProviderHealth = ProviderHealth.HEALTHY,
    model_health: ProviderHealth = ProviderHealth.HEALTHY,
    enabled: bool = True,
) -> tuple[ProviderIdentity, ModelRegistration]:
    provider = HermesClaudeProvider(
        ClaudeConfig(
            enabled=enabled,
            runtime_model="claude-sonnet-runtime",
        ),
        credential_resolver=lambda: None,
    )
    identity = ProviderIdentity(
        provider_id=CLAUDE_PROVIDER_ID,
        execution_location=ExecutionLocation.EXTERNAL,
        health=provider_health,
        enabled=enabled,
        metadata=provider.identity.metadata,
    )
    registration = replace(
        provider.registration(),
        health=model_health,
        enabled=enabled,
    )
    return identity, registration


def routing_request(
    *,
    responsibility: Responsibility = Responsibility.RESEARCH_ANALYSIS,
    preferred_family: str | None = "gemma",
    allowed_provider_ids: frozenset[str] | None = None,
    privacy: PrivacyTier = PrivacyTier.EXTERNAL_APPROVED,
    trust: TrustTier = TrustTier.RESTRICTED,
    cost: CostClass = CostClass.HIGH,
    fallback_allowed: bool = True,
) -> RoutingRequest:
    return RoutingRequest(
        request_id="gamma-stage3-routing",
        task_correlation_id="gamma-stage3-task",
        evidence_correlation_id="gamma-stage3-evidence",
        responsibility=responsibility,
        required_capabilities=frozenset({Capability.REASONING}),
        preferred_model_family=preferred_family,
        privacy_requirement=privacy,
        maximum_cost_class=cost,
        execution_location_preference=(
            ExecutionLocation.LOCAL,
            ExecutionLocation.FLEET,
            ExecutionLocation.EXTERNAL,
        ),
        minimum_trust_tier=trust,
        timeout_ms=30_000,
        fallback_allowed=fallback_allowed,
        allowed_provider_ids=allowed_provider_ids,
    )


def registry(
    *,
    gemma: ModelRegistration | None = None,
    claude_provider: ProviderIdentity | None = None,
    claude_model: ModelRegistration | None = None,
) -> GovernedModelRegistry:
    models: list[ModelRegistration] = []
    providers: list[ProviderIdentity] = []

    if gemma is not None:
        providers.append(
            ProviderIdentity(
                provider_id=gemma.provider_id,
                execution_location=gemma.execution_location,
                health=gemma.health,
                enabled=gemma.enabled,
            )
        )
        models.append(gemma)

    if claude_provider is not None and claude_model is not None:
        providers.append(claude_provider)
        models.append(claude_model)

    return GovernedModelRegistry(
        providers=tuple(providers),
        models=tuple(models),
    )


def test_gemma_remains_preferred_when_claude_is_explicitly_admitted() -> None:
    claude_provider, claude_model = claude_catalog()
    router = GovernedModelRouter(
        registry(
            gemma=gemma_registration(),
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    )

    decision = router.route(
        routing_request(
            allowed_provider_ids=frozenset(
                {
                    "local-gemma",
                    CLAUDE_PROVIDER_ID,
                }
            )
        ),
        decision_timestamp=NOW,
    )

    assert decision.succeeded
    assert decision.selected_provider_id == "local-gemma"
    assert decision.selected_model_id == "gemma-governed"
    assert decision.fallback is False
    assert decision.paper_only is True
    assert decision.broker_submission is False


def test_claude_requires_explicit_provider_admission() -> None:
    claude_provider, claude_model = claude_catalog()
    router = GovernedModelRouter(
        registry(
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    )

    implicit = router.route(
        routing_request(),
        decision_timestamp=NOW,
    )
    explicit = router.route(
        routing_request(
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        ),
        decision_timestamp=NOW,
    )

    assert implicit.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert implicit.selected_provider_id is None
    assert implicit.considered_candidates[0].rejection_reasons == (
        "external_provider_not_explicitly_admitted",
    )
    assert explicit.succeeded
    assert explicit.selected_provider_id == CLAUDE_PROVIDER_ID
    assert explicit.selected_model_id == claude_model.model_id
    assert explicit.fallback is True


@pytest.mark.parametrize(
    "responsibility",
    sorted(PROHIBITED_RESPONSIBILITIES, key=lambda item: item.value),
)
def test_claude_can_never_receive_prohibited_responsibilities(
    responsibility: Responsibility,
) -> None:
    claude_provider, claude_model = claude_catalog()
    decision = GovernedModelRouter(
        registry(
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    ).route(
        routing_request(
            responsibility=responsibility,
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        ),
        decision_timestamp=NOW,
    )

    assert decision.failure_class == RoutingFailureClass.PROHIBITED_RESPONSIBILITY
    assert decision.selected_provider_id is None
    assert decision.selected_model_id is None
    assert decision.considered_candidates == ()
    assert decision.paper_only is True
    assert decision.broker_submission is False


@pytest.mark.parametrize(
    ("provider_health", "model_health", "reason"),
    [
        (
            ProviderHealth.DEGRADED,
            ProviderHealth.HEALTHY,
            "provider_unhealthy",
        ),
        (
            ProviderHealth.UNAVAILABLE,
            ProviderHealth.HEALTHY,
            "provider_unhealthy",
        ),
        (
            ProviderHealth.HEALTHY,
            ProviderHealth.DEGRADED,
            "model_unhealthy",
        ),
        (
            ProviderHealth.HEALTHY,
            ProviderHealth.UNAVAILABLE,
            "model_unhealthy",
        ),
    ],
)
def test_unhealthy_claude_is_skipped_safely(
    provider_health: ProviderHealth,
    model_health: ProviderHealth,
    reason: str,
) -> None:
    claude_provider, claude_model = claude_catalog(
        provider_health=provider_health,
        model_health=model_health,
    )
    decision = GovernedModelRouter(
        registry(
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    ).route(
        routing_request(
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        ),
        decision_timestamp=NOW,
    )

    assert decision.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert decision.selected_provider_id is None
    assert reason in decision.considered_candidates[0].rejection_reasons


def test_missing_claude_credentials_do_not_break_gemma_routing() -> None:
    claude = HermesClaudeProvider(
        ClaudeConfig(
            enabled=True,
            runtime_model="claude-sonnet-runtime",
        ),
        credential_resolver=lambda: None,
    )
    health = claude.health_probe()
    claude_model = claude.registration()
    router = GovernedModelRouter(
        registry(
            gemma=gemma_registration(),
            claude_provider=claude.identity,
            claude_model=claude_model,
        )
    )

    decision = router.route(
        routing_request(
            allowed_provider_ids=frozenset(
                {
                    "local-gemma",
                    CLAUDE_PROVIDER_ID,
                }
            )
        ),
        decision_timestamp=NOW,
    )

    assert health.health == ProviderHealth.DEGRADED
    assert health.classification == "credentials_unavailable"
    assert decision.succeeded
    assert decision.selected_provider_id == "local-gemma"
    claude_candidate = next(
        candidate
        for candidate in decision.considered_candidates
        if candidate.provider_id == CLAUDE_PROVIDER_ID
    )
    assert "provider_unhealthy" in claude_candidate.rejection_reasons
    assert "model_unhealthy" in claude_candidate.rejection_reasons


@pytest.mark.parametrize(
    ("privacy", "trust", "cost", "reason"),
    [
        (
            PrivacyTier.GOVERNED_REMOTE,
            TrustTier.RESTRICTED,
            CostClass.HIGH,
            "privacy_requirement_unmet",
        ),
        (
            PrivacyTier.EXTERNAL_APPROVED,
            TrustTier.TRUSTED,
            CostClass.HIGH,
            "trust_requirement_unmet",
        ),
        (
            PrivacyTier.EXTERNAL_APPROVED,
            TrustTier.RESTRICTED,
            CostClass.STANDARD,
            "cost_class_exceeded",
        ),
    ],
)
def test_claude_respects_privacy_trust_and_cost_policy(
    privacy: PrivacyTier,
    trust: TrustTier,
    cost: CostClass,
    reason: str,
) -> None:
    claude_provider, claude_model = claude_catalog()
    decision = GovernedModelRouter(
        registry(
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    ).route(
        routing_request(
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
            privacy=privacy,
            trust=trust,
            cost=cost,
        ),
        decision_timestamp=NOW,
    )

    assert decision.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert decision.selected_provider_id is None
    assert reason in decision.considered_candidates[0].rejection_reasons


def test_claude_responsibility_admission_is_explicit_and_advisory_only() -> None:
    claude_provider, claude_model = claude_catalog()
    router = GovernedModelRouter(
        registry(
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    )

    allowed = router.route(
        routing_request(
            responsibility=Responsibility.RISK_ANALYSIS,
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        ),
        decision_timestamp=NOW,
    )
    not_allowed = router.route(
        routing_request(
            responsibility=Responsibility.FORECASTING,
            allowed_provider_ids=frozenset({CLAUDE_PROVIDER_ID}),
        ),
        decision_timestamp=NOW,
    )

    assert allowed.succeeded
    assert allowed.selected_provider_id == CLAUDE_PROVIDER_ID
    assert not_allowed.failure_class == RoutingFailureClass.NO_SUITABLE_MODEL
    assert "responsibility_not_allowed" in (
        not_allowed.considered_candidates[0].rejection_reasons
    )


def test_stage3_routing_decisions_are_deterministic_and_auditable() -> None:
    claude_provider, claude_model = claude_catalog()
    router = GovernedModelRouter(
        registry(
            gemma=gemma_registration(),
            claude_provider=claude_provider,
            claude_model=claude_model,
        )
    )
    request = routing_request(
        allowed_provider_ids=frozenset(
            {
                "local-gemma",
                CLAUDE_PROVIDER_ID,
            }
        )
    )

    first = router.route(request, decision_timestamp=NOW)
    second = router.route(request, decision_timestamp=NOW)

    assert first == second
    assert first.evidence_identity.startswith("sha256:")
    assert first.considered_candidates == second.considered_candidates
    assert tuple(
        (candidate.provider_id, candidate.model_id)
        for candidate in first.considered_candidates
    ) == tuple(
        sorted(
            (
                (candidate.provider_id, candidate.model_id)
                for candidate in first.considered_candidates
            )
        )
    )
    assert first.paper_only is True
    assert first.broker_submission is False
