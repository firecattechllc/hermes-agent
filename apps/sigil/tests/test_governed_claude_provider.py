from __future__ import annotations

import pytest

from sigil.ai import (
    CLAUDE_CAPABILITIES,
    CLAUDE_MODEL_ID,
    CLAUDE_PROVIDER_ID,
    CLAUDE_RESPONSIBILITIES,
    Capability,
    ClaudeConfig,
    ClaudeTransportError,
    ClaudeTransportFailure,
    ClaudeTransportResult,
    ExecutionLocation,
    HermesClaudeProvider,
    PrivacyTier,
    ProviderFailureClass,
    ProviderHealth,
    ProviderInvocation,
    Responsibility,
    TrustTier,
)


def invocation(
    *,
    model_id: str = CLAUDE_MODEL_ID,
    capability: Capability = Capability.REASONING,
) -> ProviderInvocation:
    return ProviderInvocation(
        request_id="claude-request-001",
        task_correlation_id="claude-task-001",
        model_id=model_id,
        registry_revision="sha256:" + "a" * 64,
        capability=capability,
        input_payload={"prompt_digest": "sha256:sanitized"},
        timeout_ms=60_000,
        started_at="2026-08-01T23:45:00Z",
        ended_at="2026-08-01T23:45:01Z",
    )


def test_claude_provider_is_disabled_and_unavailable_by_default() -> None:
    provider = HermesClaudeProvider()

    assert provider.identity.provider_id == CLAUDE_PROVIDER_ID
    assert provider.identity.execution_location == ExecutionLocation.EXTERNAL
    assert provider.identity.enabled is False
    assert provider.identity.health == ProviderHealth.UNAVAILABLE


def test_enabled_configuration_does_not_claim_verified_health() -> None:
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=lambda: None,
    )

    assert provider.identity.enabled is True
    assert provider.identity.health == ProviderHealth.DEGRADED

    registration = provider.registration()
    assert registration.enabled is True
    assert registration.health == ProviderHealth.DEGRADED


def test_claude_registration_is_external_restricted_and_advisory_only() -> None:
    registration = HermesClaudeProvider(ClaudeConfig(enabled=True)).registration()

    assert registration.model_id == CLAUDE_MODEL_ID
    assert registration.provider_id == CLAUDE_PROVIDER_ID
    assert registration.family == "claude"
    assert registration.capabilities == CLAUDE_CAPABILITIES
    assert registration.execution_location == ExecutionLocation.EXTERNAL
    assert registration.privacy_tier == PrivacyTier.EXTERNAL_APPROVED
    assert registration.trust_tier == TrustTier.RESTRICTED
    assert registration.allowed_responsibilities == CLAUDE_RESPONSIBILITIES

    assert Responsibility.BROKER_SUBMISSION in registration.prohibited_responsibilities
    assert Responsibility.ORDER_EXECUTION in registration.prohibited_responsibilities
    assert Responsibility.PORTFOLIO_MUTATION in registration.prohibited_responsibilities
    assert Responsibility.CREDENTIAL_ACCESS in registration.prohibited_responsibilities
    assert Responsibility.UNRESTRICTED_SHELL_EXECUTION in (
        registration.prohibited_responsibilities
    )
    assert Responsibility.SELF_MODIFYING_GOVERNANCE in (
        registration.prohibited_responsibilities
    )


def test_claude_invoke_fails_closed_until_hermes_transport_is_connected() -> None:
    provider = HermesClaudeProvider(ClaudeConfig(enabled=True))
    result = provider.invoke(invocation())

    assert result.output is None
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.UNAVAILABLE
    assert result.failure.retryable is False
    assert result.paper_only is True
    assert result.broker_submission is False

    assert result.evidence.provider_id == CLAUDE_PROVIDER_ID
    assert result.evidence.model_id == CLAUDE_MODEL_ID
    assert result.evidence.execution_location == ExecutionLocation.EXTERNAL
    assert result.evidence.failure_classification == (
        ProviderFailureClass.UNAVAILABLE.value
    )
    assert result.evidence.paper_only is True
    assert result.evidence.broker_submission is False


def test_claude_evidence_digests_input_without_persisting_prompt() -> None:
    secret_prompt = "do-not-persist-this-claude-prompt"
    original = invocation()

    result = HermesClaudeProvider(ClaudeConfig(enabled=True)).invoke(
        ProviderInvocation(
            request_id=original.request_id,
            task_correlation_id=original.task_correlation_id,
            model_id=original.model_id,
            registry_revision=original.registry_revision,
            capability=original.capability,
            input_payload={"prompt": secret_prompt},
            timeout_ms=original.timeout_ms,
            started_at=original.started_at,
            ended_at=original.ended_at,
        )
    )

    assert secret_prompt not in repr(result.evidence)
    assert result.evidence.input_digest.startswith("sha256:")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("context_limit", 0, "context_limit"),
        ("request_timeout_ms", 0, "request_timeout_ms"),
    ],
)
def test_invalid_claude_configuration_fails_closed(field, value, message) -> None:
    with pytest.raises(ValueError, match=message):
        ClaudeConfig(**{field: value})


def test_claude_configuration_loads_from_environment() -> None:
    config = ClaudeConfig.from_environment(
        {
            "SIGIL_AI_CLAUDE_ENABLED": "true",
            "SIGIL_AI_CLAUDE_MODEL_ID": "claude-sonnet-governed-test",
            "SIGIL_AI_CLAUDE_CONTEXT_LIMIT": "100000",
            "SIGIL_AI_CLAUDE_REQUEST_TIMEOUT_MS": "45000",
        }
    )

    assert config.enabled is True
    assert config.model_id == "claude-sonnet-governed-test"
    assert config.context_limit == 100_000
    assert config.request_timeout_ms == 45_000


def test_claude_configuration_is_disabled_by_default_from_environment() -> None:
    config = ClaudeConfig.from_environment({})

    assert config.enabled is False
    assert config.model_id == CLAUDE_MODEL_ID
    assert config.context_limit == 200_000
    assert config.request_timeout_ms == 60_000


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SIGIL_AI_CLAUDE_ENABLED", "maybe", "boolean"),
        ("SIGIL_AI_CLAUDE_CONTEXT_LIMIT", "zero", "integer"),
        ("SIGIL_AI_CLAUDE_CONTEXT_LIMIT", "0", "positive"),
        ("SIGIL_AI_CLAUDE_REQUEST_TIMEOUT_MS", "-1", "positive"),
    ],
)
def test_invalid_claude_environment_configuration_fails_closed(
    name,
    value,
    message,
) -> None:
    with pytest.raises(ValueError, match=message):
        ClaudeConfig.from_environment({name: value})


def test_empty_claude_model_id_fails_closed() -> None:
    with pytest.raises(ValueError, match="model_id"):
        ClaudeConfig.from_environment(
            {
                "SIGIL_AI_CLAUDE_MODEL_ID": "   ",
            }
        )


def test_disabled_claude_health_probe_does_not_resolve_credentials() -> None:
    calls = []

    def resolver() -> str | None:
        calls.append("called")
        return "should-not-be-read"

    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=False),
        credential_resolver=resolver,
    )

    health = provider.health_probe()

    assert calls == []
    assert health.health == ProviderHealth.UNAVAILABLE
    assert health.classification == "provider_disabled"
    assert health.credentials_available is False
    assert health.paper_only is True
    assert health.broker_submission is False


def test_enabled_claude_without_credentials_remains_degraded() -> None:
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=lambda: None,
    )

    health = provider.health_probe()

    assert health.health == ProviderHealth.DEGRADED
    assert health.classification == "credentials_unavailable"
    assert health.credentials_available is False
    assert provider.identity.health == ProviderHealth.DEGRADED
    assert provider.registration().health == ProviderHealth.DEGRADED


def test_enabled_claude_with_credentials_remains_degraded_until_transport() -> None:
    secret = "claude-secret-must-not-escape"
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=lambda: secret,
    )

    health = provider.health_probe()

    assert health.health == ProviderHealth.DEGRADED
    assert health.classification == "transport_unverified"
    assert health.credentials_available is True
    assert secret not in repr(health)
    assert secret not in repr(provider.identity)
    assert secret not in repr(provider.registration())


def test_claude_credential_resolution_failure_fails_closed() -> None:
    def resolver() -> str | None:
        raise RuntimeError("credential backend unavailable")

    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=resolver,
    )

    health = provider.health_probe()

    assert health.health == ProviderHealth.UNAVAILABLE
    assert health.classification == "credential_resolution_failed"
    assert health.credentials_available is False
    assert provider.identity.health == ProviderHealth.UNAVAILABLE


def test_claude_health_metadata_never_contains_credentials() -> None:
    secret = "sk-ant-secret-value"
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=lambda: secret,
    )

    provider.health_probe()

    metadata = dict(provider.identity.metadata)
    assert metadata == {"adapter": "hermes-claude-gamma-v1"}
    assert secret not in repr(metadata)


class FakeClaudeTransport:
    def __init__(
        self,
        *,
        result: ClaudeTransportResult | None = None,
        error: ClaudeTransportError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def invoke(
        self,
        *,
        model: str,
        prompt: str,
        timeout_ms: int,
        max_output_tokens: int,
    ) -> ClaudeTransportResult:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "timeout_ms": timeout_ms,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def runtime_config(**overrides) -> ClaudeConfig:
    values = {
        "enabled": True,
        "runtime_model": "claude-sonnet-4-6",
        "request_timeout_ms": 30_000,
        "max_output_tokens": 4_096,
    }
    values.update(overrides)
    return ClaudeConfig(**values)


def prompt_invocation(
    *,
    prompt: str = "Analyze the governed evidence.",
    timeout_ms: int = 60_000,
    model_id: str = CLAUDE_MODEL_ID,
    capability: Capability = Capability.REASONING,
) -> ProviderInvocation:
    original = invocation(model_id=model_id, capability=capability)
    return ProviderInvocation(
        request_id=original.request_id,
        task_correlation_id=original.task_correlation_id,
        model_id=original.model_id,
        registry_revision=original.registry_revision,
        capability=original.capability,
        input_payload={"prompt": prompt},
        timeout_ms=timeout_ms,
        started_at=original.started_at,
        ended_at=original.ended_at,
    )


def test_governed_claude_transport_success_is_sanitized() -> None:
    secret_prompt = "private prompt that must not enter evidence"
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="Governed analysis complete.",
            finish_reason="stop",
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(),
        credential_resolver=lambda: "not-used-by-fake",
        transport=transport,
    )

    result = provider.invoke(prompt_invocation(prompt=secret_prompt))

    assert result.succeeded is True
    assert result.failure is None
    assert result.output is not None
    assert result.output["content"] == "Governed analysis complete."
    assert result.output["finish_reason"] == "stop"
    assert result.output["usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
    }
    assert result.output["paper_only"] is True
    assert result.output["execution_authorized"] is False
    assert result.output["broker_submission"] is False
    assert result.output["portfolio_mutation"] is False
    assert result.output["approval_authority"] is False
    assert result.output["tool_execution"] is False
    assert result.broker_submission is False
    assert result.paper_only is True
    assert provider.identity.health == ProviderHealth.HEALTHY

    assert transport.calls == [
        {
            "model": "claude-sonnet-4-6",
            "prompt": secret_prompt,
            "timeout_ms": 30_000,
            "max_output_tokens": 4_096,
        }
    ]

    assert secret_prompt not in repr(result.evidence)
    assert "Governed analysis complete." not in repr(result.evidence)
    assert result.evidence.input_digest.startswith("sha256:")
    assert result.evidence.output_digest is not None


def test_claude_provider_requires_runtime_model() -> None:
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="unused",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
    )
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    result = provider.invoke(prompt_invocation())

    assert result.output is None
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.UNAVAILABLE
    assert result.failure.retryable is False
    assert transport.calls == []


def test_claude_provider_rejects_empty_prompt_before_transport() -> None:
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="unused",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    result = provider.invoke(prompt_invocation(prompt="   "))

    assert result.output is None
    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.MALFORMED_OUTPUT
    assert result.failure.retryable is False
    assert transport.calls == []


@pytest.mark.parametrize(
    ("transport_failure", "provider_failure", "retryable"),
    [
        (
            ClaudeTransportFailure.UNAVAILABLE,
            ProviderFailureClass.UNAVAILABLE,
            True,
        ),
        (
            ClaudeTransportFailure.TIMEOUT,
            ProviderFailureClass.TIMEOUT,
            True,
        ),
        (
            ClaudeTransportFailure.MALFORMED,
            ProviderFailureClass.MALFORMED_OUTPUT,
            False,
        ),
    ],
)
def test_claude_transport_failures_are_normalized(
    transport_failure,
    provider_failure,
    retryable,
) -> None:
    secret = "credential-or-provider-detail-must-not-escape"
    transport = FakeClaudeTransport(
        error=ClaudeTransportError(
            transport_failure,
            secret,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    result = provider.invoke(prompt_invocation())

    assert result.output is None
    assert result.failure is not None
    assert result.failure.classification == provider_failure
    assert result.failure.retryable is retryable
    assert result.failure.message == "Governed Claude transport failed safely."
    assert secret not in repr(result)
    assert provider.identity.health == ProviderHealth.UNAVAILABLE


def test_claude_invocation_rejects_model_identity_mismatch() -> None:
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="unused",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    result = provider.invoke(
        prompt_invocation(model_id="different-registered-model")
    )

    assert result.failure is not None
    assert (
        result.failure.classification
        == ProviderFailureClass.MODEL_IDENTITY_MISMATCH
    )
    assert transport.calls == []


def test_claude_invocation_rejects_unsupported_capability() -> None:
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="unused",
            finish_reason="stop",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    result = provider.invoke(
        prompt_invocation(capability=Capability.EMBEDDINGS)
    )

    assert result.failure is not None
    assert result.failure.classification == ProviderFailureClass.CAPABILITY_MISMATCH
    assert transport.calls == []


def test_claude_invocation_uses_stricter_timeout_bound() -> None:
    transport = FakeClaudeTransport(
        result=ClaudeTransportResult(
            content="bounded",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )
    )
    provider = HermesClaudeProvider(
        runtime_config(request_timeout_ms=45_000),
        credential_resolver=lambda: "unused",
        transport=transport,
    )

    provider.invoke(prompt_invocation(timeout_ms=5_000))

    assert transport.calls[0]["timeout_ms"] == 5_000
