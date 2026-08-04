"""Governed Claude provider foundation backed by the Hermes runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping

from .evidence import build_invocation_evidence
from .hermes_claude_transport import (
    ClaudeTransportError,
    ClaudeTransportFailure,
    HermesClaudeTransport,
)
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

# Dedicated Sigil credential. Preferred over the shared Hermes coding-agent
# credential in every mode, and the *only* source consulted when running in
# strict production-integrated mode (see ClaudeConfig.strict_credentials).
SIGIL_CLAUDE_CREDENTIAL_ENV_VAR = "SIGIL_AI_CLAUDE_API_KEY"
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


def _resolve_sigil_claude_credential(
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the Sigil-specific, dedicated Claude credential.

    This is deliberately independent of Hermes's own coding-agent credential
    resolution — Sigil's governed Claude provider should not, by default,
    borrow the credential the operator is using to run the Hermes CLI/coding
    agent itself.
    """

    source = os.environ if environment is None else environment
    value = source.get(SIGIL_CLAUDE_CREDENTIAL_ENV_VAR)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_hermes_anthropic_credential() -> str | None:
    """Resolve Claude credentials via the shared Hermes coding-agent adapter.

    This is a local-development compatibility fallback only: it reuses
    whatever credential the operator has configured for the Hermes coding
    agent itself (OAuth token, Claude Code credential file, API key, ...).
    It is never consulted in strict production-integrated mode — see
    ``default_claude_credential_resolver``.
    """

    from agent.anthropic_adapter import resolve_anthropic_token

    return resolve_anthropic_token()


def default_claude_credential_resolver(
    *,
    strict_production_integrated: bool = False,
    environment: Mapping[str, str] | None = None,
) -> Callable[[], str | None]:
    """Build the default credential resolver for the governed Claude provider.

    Resolution order:
      1. The Sigil-specific credential (``SIGIL_AI_CLAUDE_API_KEY``), always
         preferred when configured.
      2. In non-strict (local-development) mode only: the shared Hermes
         coding-agent credential, as a documented compatibility path.

    In strict production-integrated mode, step 2 never runs — an absent
    Sigil-specific credential resolves to ``None`` (fails closed) rather than
    silently borrowing the shared coding-agent credential.
    """

    def resolver() -> str | None:
        sigil_credential = _resolve_sigil_claude_credential(environment)
        if sigil_credential is not None:
            return sigil_credential
        if strict_production_integrated:
            return None
        return _resolve_hermes_anthropic_credential()

    return resolver


@dataclass(frozen=True, slots=True)
class ClaudeConfig:
    """Credential-free governed configuration for Claude registration."""

    enabled: bool = False
    model_id: str = CLAUDE_MODEL_ID
    runtime_model: str | None = None
    context_limit: int = 200_000
    request_timeout_ms: int = 60_000
    max_output_tokens: int = 8_192
    strict_credentials: bool = False

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("Claude model_id must not be empty")
        if self.runtime_model is not None and not self.runtime_model.strip():
            raise ValueError("Claude runtime_model must not be empty")
        if self.context_limit < 1:
            raise ValueError("Claude context_limit must be positive")
        if self.request_timeout_ms < 1:
            raise ValueError("Claude request_timeout_ms must be positive")
        if self.max_output_tokens < 1:
            raise ValueError("Claude max_output_tokens must be positive")

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
            runtime_model=(
                source["SIGIL_AI_CLAUDE_RUNTIME_MODEL"].strip()
                if "SIGIL_AI_CLAUDE_RUNTIME_MODEL" in source
                else None
            ),
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
            max_output_tokens=_environment_positive_int(
                source,
                "SIGIL_AI_CLAUDE_MAX_OUTPUT_TOKENS",
                8_192,
            ),
            strict_credentials=_environment_bool(
                source,
                "SIGIL_AI_CLAUDE_STRICT_CREDENTIALS",
                False,
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
        transport: HermesClaudeTransport | None = None,
    ) -> None:
        self.config = config or ClaudeConfig()
        self.credential_resolver = credential_resolver or default_claude_credential_resolver(
            strict_production_integrated=self.config.strict_credentials,
        )
        self.transport = transport or HermesClaudeTransport(
            credential_resolver=self.credential_resolver,
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
        failure: ProviderFailure | None = None
        output: dict[str, object] | None = None

        if not self.config.enabled:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE,
                "Governed Claude provider is disabled.",
                False,
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Invocation model does not match governed Claude registration.",
                False,
            )
        elif invocation.capability not in self.capabilities:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH,
                "Claude does not declare the requested capability.",
                False,
            )
        elif invocation.timeout_ms < 1:
            failure = ProviderFailure(
                ProviderFailureClass.TIMEOUT,
                "Claude invocation timeout must be positive.",
                True,
            )
        elif self.config.runtime_model is None:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE,
                "Claude runtime model is not configured.",
                False,
            )
        else:
            prompt = invocation.input_payload.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                failure = ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Claude invocation requires a non-empty prompt.",
                    False,
                )
            else:
                try:
                    result = self.transport.invoke(
                        model=self.config.runtime_model,
                        prompt=prompt,
                        timeout_ms=min(
                            invocation.timeout_ms,
                            self.request_timeout_ms,
                        ),
                        max_output_tokens=self.config.max_output_tokens,
                    )
                    output = {
                        "schema_version": 1,
                        "status": "ok",
                        "request_id": invocation.request_id,
                        "model_id": self.model_id,
                        "runtime_model": self.config.runtime_model,
                        "content": result.content,
                        "finish_reason": result.finish_reason,
                        "usage": {
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                            "total_tokens": result.total_tokens,
                        },
                        "paper_only": True,
                        "execution_authorized": False,
                        "broker_submission": False,
                        "portfolio_mutation": False,
                        "approval_authority": False,
                        "tool_execution": False,
                    }
                    self._set_health(ProviderHealth.HEALTHY)
                except ClaudeTransportError as error:
                    self._set_health(ProviderHealth.UNAVAILABLE)
                    failure = ProviderFailure(
                        _provider_failure_class(error.classification),
                        "Governed Claude transport failed safely.",
                        error.classification
                        in {
                            ClaudeTransportFailure.TIMEOUT,
                            ClaudeTransportFailure.UNAVAILABLE,
                        },
                    )

        evidence_output = None
        if output is not None:
            evidence_output = {
                key: value
                for key, value in output.items()
                if key != "content"
            }

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
            failure_classification=(
                None if failure is None else failure.classification.value
            ),
            input_payload={
                key: value
                for key, value in invocation.input_payload.items()
                if key != "prompt"
            },
            output_payload=evidence_output,
            provider_metadata=(
                ("adapter", "hermes-claude-gamma-v1"),
                ("transport", "hermes-anthropic-v1"),
            ),
        )

        return ProviderResult(
            output=output,
            failure=failure,
            evidence=evidence,
            broker_submission=False,
            paper_only=True,
        )


def _provider_failure_class(
    classification: ClaudeTransportFailure,
) -> ProviderFailureClass:
    if classification == ClaudeTransportFailure.TIMEOUT:
        return ProviderFailureClass.TIMEOUT
    if classification == ClaudeTransportFailure.MALFORMED:
        return ProviderFailureClass.MALFORMED_OUTPUT
    return ProviderFailureClass.UNAVAILABLE
