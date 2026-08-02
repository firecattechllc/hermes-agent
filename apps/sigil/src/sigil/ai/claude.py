"""Governed Claude provider foundation backed by the Hermes runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping

from .evidence import build_invocation_evidence
from .models import (
    Capability,
    CostClass,
    ExecutionLocation,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    TrustTier,
)
from .provider import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)

CLAUDE_PROVIDER_ID = "hermes-claude"
CLAUDE_MODEL_ID = "claude-sonnet-governed"
CLAUDE_MODEL_FAMILY = "claude"
CLAUDE_MODEL_VERSION = "gamma-foundation-v1"

CLAUDE_CAPABILITIES = frozenset(
    {
        Capability.REASONING,
        Capability.STRUCTURED_GENERATION,
        Capability.SUMMARIZATION,
        Capability.CODING,
    }
)

CLAUDE_RESPONSIBILITIES = frozenset(
    {
        Responsibility.ANALYSIS,
        Responsibility.EXPLANATION,
        Responsibility.RESEARCH,
        Responsibility.CODE_ASSISTANCE,
        Responsibility.RESEARCH_ANALYSIS,
        Responsibility.PROPOSAL_SUPPORT,
        Responsibility.EVIDENCE_SUMMARIZATION,
        Responsibility.RISK_ANALYSIS,
        Responsibility.MARKET_CONTEXT,
        Responsibility.ORCHESTRATION_PLANNING,
        Responsibility.ORCHESTRATION_SUPPORT,
    }
)


def _environment_bool(
    environment: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = environment.get(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be a boolean value")


def _environment_positive_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = environment.get(name)
    if raw is None:
        return default

    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value < 1:
        raise ValueError(f"{name} must be positive")

    return value


@dataclass(frozen=True, slots=True)
class ClaudeHealth:
    """Credential-safe readiness result for governed Claude access."""

    health: ProviderHealth
    classification: str
    credentials_available: bool
    provider_id: str = CLAUDE_PROVIDER_ID
    broker_submission: bool = False
    paper_only: bool = True


def _resolve_hermes_anthropic_credential() -> str | None:
    """Resolve Claude credentials inside the Hermes backend boundary."""

    from agent.anthropic_adapter import resolve_anthropic_token

    return resolve_anthropic_token()


@dataclass(frozen=True, slots=True)
class ClaudeConfig:
    """Credential-free governed configuration for Claude registration."""

    enabled: bool = False
    model_id: str = CLAUDE_MODEL_ID
    context_limit: int = 200_000
    request_timeout_ms: int = 60_000

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("Claude model_id must not be empty")
        if self.context_limit < 1:
            raise ValueError("Claude context_limit must be positive")
        if self.request_timeout_ms < 1:
            raise ValueError("Claude request_timeout_ms must be positive")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "ClaudeConfig":
        source = os.environ if environment is None else environment

        return cls(
            enabled=_environment_bool(
                source,
                "SIGIL_AI_CLAUDE_ENABLED",
                False,
            ),
            model_id=source.get(
                "SIGIL_AI_CLAUDE_MODEL_ID",
                CLAUDE_MODEL_ID,
            ).strip(),
            context_limit=_environment_positive_int(
                source,
                "SIGIL_AI_CLAUDE_CONTEXT_LIMIT",
                200_000,
            ),
            request_timeout_ms=_environment_positive_int(
                source,
                "SIGIL_AI_CLAUDE_REQUEST_TIMEOUT_MS",
                60_000,
            ),
        )


class HermesClaudeProvider:
    """Fail-closed Claude provider until governed Hermes execution is connected."""

    input_contract = "application/json;schema=sigil.ai.input.v1"
    output_contract = "application/json;schema=sigil.ai.output.v1"
    capabilities = CLAUDE_CAPABILITIES
    model_family = CLAUDE_MODEL_FAMILY

    def __init__(
        self,
        config: ClaudeConfig | None = None,
        *,
        credential_resolver: Callable[[], str | None] | None = None,
    ) -> None:
        self.config = config or ClaudeConfig()
        self.credential_resolver = (
            credential_resolver or _resolve_hermes_anthropic_credential
        )
        self.model_id = self.config.model_id
        self.model_version = CLAUDE_MODEL_VERSION
        self.request_timeout_ms = self.config.request_timeout_ms

        # Enabled Claude begins degraded until credentials and the governed
        # Hermes transport have both been verified.
        self.identity = ProviderIdentity(
            provider_id=CLAUDE_PROVIDER_ID,
            execution_location=ExecutionLocation.EXTERNAL,
            health=(
                ProviderHealth.DEGRADED
                if self.config.enabled
                else ProviderHealth.UNAVAILABLE
            ),
            enabled=self.config.enabled,
            metadata=(("adapter", "hermes-claude-gamma-v1"),),
        )

    def health_probe(self) -> ClaudeHealth:
        if not self.config.enabled:
            self._set_health(ProviderHealth.UNAVAILABLE)
            return ClaudeHealth(
                ProviderHealth.UNAVAILABLE,
                "provider_disabled",
                False,
            )

        try:
            credential = self.credential_resolver()
        except Exception:
            self._set_health(ProviderHealth.UNAVAILABLE)
            return ClaudeHealth(
                ProviderHealth.UNAVAILABLE,
                "credential_resolution_failed",
                False,
            )

        credentials_available = bool(
            isinstance(credential, str) and credential.strip()
        )
        self._set_health(ProviderHealth.DEGRADED)

        if not credentials_available:
            return ClaudeHealth(
                ProviderHealth.DEGRADED,
                "credentials_unavailable",
                False,
            )

        return ClaudeHealth(
            ProviderHealth.DEGRADED,
            "transport_unverified",
            True,
        )

    def _set_health(self, health: ProviderHealth) -> None:
        self.identity = ProviderIdentity(
            provider_id=CLAUDE_PROVIDER_ID,
            execution_location=ExecutionLocation.EXTERNAL,
            health=health,
            enabled=self.config.enabled,
            metadata=(("adapter", "hermes-claude-gamma-v1"),),
        )

    def registration(self) -> ModelRegistration:
        return ModelRegistration(
            model_id=self.model_id,
            provider_id=self.identity.provider_id,
            family=self.model_family,
            version=self.model_version,
            capabilities=self.capabilities,
            execution_location=self.identity.execution_location,
            context_limit=self.config.context_limit,
            supported_input_types=frozenset(
                {
                    InputType.TEXT,
                    InputType.STRUCTURED_JSON,
                }
            ),
            structured_output=True,
            cost_class=CostClass.HIGH,
            trust_tier=TrustTier.RESTRICTED,
            privacy_tier=PrivacyTier.EXTERNAL_APPROVED,
            health=self.identity.health,
            enabled=self.identity.enabled,
            allowed_responsibilities=CLAUDE_RESPONSIBILITIES,
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        failure = ProviderFailure(
            classification=ProviderFailureClass.UNAVAILABLE,
            message="Governed Hermes Claude execution is not connected.",
            retryable=False,
        )

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
            succeeded=False,
            failure_classification=failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=None,
            provider_metadata=(("adapter", "hermes-claude-gamma-v1"),),
        )

        return ProviderResult(
            output=None,
            failure=failure,
            evidence=evidence,
            broker_submission=False,
            paper_only=True,
        )
