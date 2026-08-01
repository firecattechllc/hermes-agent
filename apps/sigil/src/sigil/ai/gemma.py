"""Backend-only governed adapter for a local Ollama-compatible Gemma endpoint."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .evidence import build_invocation_evidence
from .ledger import (
    AIEvidenceRecordType,
    DurableAIEvidenceLedger,
    GovernedAIEvidenceRecord,
    evidence_identity,
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
    validate_identifier,
)
from .provider import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)
from .registry import canonical_digest

LOCAL_GEMMA_PROVIDER_ID = "local-gemma-ollama"
LOCAL_GEMMA_FAMILY = "gemma"
LOCAL_GEMMA_CAPABILITIES = frozenset(
    {
        Capability.REASONING,
        Capability.STRUCTURED_GENERATION,
        Capability.SUMMARIZATION,
    }
)


class GemmaConfigurationError(ValueError):
    """Local Gemma configuration violates the backend-only safety policy."""


class GemmaTransportFailure(str, Enum):
    UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    REJECTED = "provider_request_rejected"
    MALFORMED = "malformed_output"


class GemmaTransportError(RuntimeError):
    def __init__(self, classification: GemmaTransportFailure) -> None:
        super().__init__(classification.value)
        self.classification = classification


class GemmaTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> object: ...


class UrllibGemmaTransport:
    """Small injectable JSON transport restricted by validated local configuration."""

    def request(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> object:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise GemmaTransportError(GemmaTransportFailure.REJECTED)
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            classification = (
                GemmaTransportFailure.UNAVAILABLE
                if error.code >= 500
                else GemmaTransportFailure.REJECTED
            )
            raise GemmaTransportError(classification) from None
        except TimeoutError:
            raise GemmaTransportError(GemmaTransportFailure.TIMEOUT) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise GemmaTransportError(GemmaTransportFailure.TIMEOUT) from None
            raise GemmaTransportError(GemmaTransportFailure.UNAVAILABLE) from None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise GemmaTransportError(GemmaTransportFailure.MALFORMED) from None


@dataclass(frozen=True, slots=True)
class LocalGemmaConfig:
    enabled: bool = False
    endpoint: str | None = None
    model_id: str | None = None
    model_version: str = "configured-v1"
    request_timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        validate_identifier(self.model_version, "model_version")
        if not 100 <= self.request_timeout_ms <= 300_000:
            raise GemmaConfigurationError("local Gemma timeout is outside the governed bound")
        if self.model_id is not None:
            validate_identifier(self.model_id, "model_id")
        if self.endpoint is not None:
            parsed = urllib.parse.urlsplit(self.endpoint)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise GemmaConfigurationError(
                    "local Gemma endpoint must be credential-free loopback HTTP"
                )
        if self.enabled and (self.endpoint is None or self.model_id is None):
            raise GemmaConfigurationError(
                "enabled local Gemma requires both endpoint and model configuration"
            )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> LocalGemmaConfig:
        source = os.environ if environment is None else environment
        enabled = source.get("SIGIL_AI_GEMMA_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        endpoint = source.get("SIGIL_AI_GEMMA_ENDPOINT") or None
        model_id = source.get("SIGIL_AI_GEMMA_MODEL") or None
        model_version = source.get("SIGIL_AI_GEMMA_MODEL_VERSION", "configured-v1")
        timeout_text = source.get("SIGIL_AI_GEMMA_TIMEOUT_MS", "30000")
        try:
            timeout_ms = int(timeout_text)
        except ValueError as error:
            raise GemmaConfigurationError("local Gemma timeout must be an integer") from error
        return cls(
            enabled=enabled,
            endpoint=endpoint,
            model_id=model_id,
            model_version=model_version,
            request_timeout_ms=timeout_ms,
        )


@dataclass(frozen=True, slots=True)
class GemmaHealth:
    health: ProviderHealth
    classification: str
    provider_id: str = LOCAL_GEMMA_PROVIDER_ID
    broker_submission: bool = False
    paper_only: bool = True


class LocalGemmaProvider:
    """Governed local Gemma invocation; it never owns execution authority."""

    input_contract = "application/json;schema=sigil.ai.input.v1"
    output_contract = "application/json;schema=sigil.ai.output.v1"
    capabilities = LOCAL_GEMMA_CAPABILITIES
    model_family = LOCAL_GEMMA_FAMILY

    def __init__(
        self,
        config: LocalGemmaConfig,
        *,
        transport: GemmaTransport | None = None,
        ledger: DurableAIEvidenceLedger | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibGemmaTransport()
        self.ledger = ledger
        self.model_id = config.model_id or "gemma-unconfigured"
        self.model_version = config.model_version
        self.request_timeout_ms = config.request_timeout_ms
        self.identity = ProviderIdentity(
            LOCAL_GEMMA_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            health=ProviderHealth.DEGRADED if config.enabled else ProviderHealth.UNAVAILABLE,
            enabled=config.enabled,
        )

    def health_probe(self) -> GemmaHealth:
        if not self.config.enabled or self.config.endpoint is None or self.config.model_id is None:
            return GemmaHealth(ProviderHealth.UNAVAILABLE, "provider_disabled")
        try:
            payload = self.transport.request(
                method="GET",
                url=f"{self.config.endpoint.rstrip('/')}/api/tags",
                payload=None,
                timeout_seconds=self.config.request_timeout_ms / 1_000,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
            names = {
                item.get("name")
                for item in payload["models"]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            if self.config.model_id not in names:
                self._set_health(ProviderHealth.UNAVAILABLE)
                return GemmaHealth(ProviderHealth.UNAVAILABLE, "model_unavailable")
            self._set_health(ProviderHealth.HEALTHY)
            return GemmaHealth(ProviderHealth.HEALTHY, "healthy")
        except GemmaTransportError as error:
            self._set_health(ProviderHealth.UNAVAILABLE)
            classification = error.classification.value
            return GemmaHealth(ProviderHealth.UNAVAILABLE, classification)

    def _set_health(self, health: ProviderHealth) -> None:
        self.identity = ProviderIdentity(
            LOCAL_GEMMA_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            health=health,
            enabled=self.config.enabled,
        )

    def registration(self) -> ModelRegistration:
        """Return the current fail-closed registry view of this configured adapter."""
        return ModelRegistration(
            model_id=self.model_id,
            provider_id=self.identity.provider_id,
            family=LOCAL_GEMMA_FAMILY,
            version=self.model_version,
            capabilities=self.capabilities,
            execution_location=ExecutionLocation.LOCAL,
            context_limit=8_192,
            supported_input_types=frozenset({InputType.TEXT, InputType.STRUCTURED_JSON}),
            structured_output=True,
            cost_class=CostClass.FREE,
            trust_tier=TrustTier.TRUSTED,
            privacy_tier=PrivacyTier.LOCAL_ONLY,
            health=self.identity.health,
            enabled=self.identity.enabled,
            allowed_responsibilities=frozenset(
                {
                    Responsibility.ANALYSIS,
                    Responsibility.EXPLANATION,
                    Responsibility.RESEARCH,
                    Responsibility.RESEARCH_ANALYSIS,
                    Responsibility.PROPOSAL_SUPPORT,
                    Responsibility.EVIDENCE_SUMMARIZATION,
                    Responsibility.RISK_ANALYSIS,
                    Responsibility.MARKET_CONTEXT,
                    Responsibility.ORCHESTRATION_SUPPORT,
                }
            ),
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        input_digest = f"sha256:{canonical_digest(dict(invocation.input_payload))}"
        self._append_attempt(invocation, input_digest)
        failure: ProviderFailure | None = None
        output: Mapping[str, object] | None = None

        if not self.config.enabled:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Local Gemma provider is disabled.", False
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Invocation model does not match configured local Gemma.",
                False,
            )
        elif invocation.capability not in self.capabilities:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH,
                "Local Gemma does not declare the requested capability.",
                False,
            )
        else:
            health = self.health_probe()
            if health.health != ProviderHealth.HEALTHY:
                failure_class = (
                    ProviderFailureClass.TIMEOUT
                    if health.classification == GemmaTransportFailure.TIMEOUT.value
                    else ProviderFailureClass.UNAVAILABLE
                )
                failure = ProviderFailure(failure_class, "Local Gemma is unavailable.", True)
            else:
                output, failure = self._invoke_transport(invocation)

        metadata = self._metadata()
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=ExecutionLocation.LOCAL,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=output,
            provider_metadata=metadata,
        )
        result = ProviderResult(output=output, failure=failure, evidence=evidence)
        self._append_result(invocation, result)
        return result

    def _invoke_transport(
        self, invocation: ProviderInvocation
    ) -> tuple[Mapping[str, object] | None, ProviderFailure | None]:
        assert self.config.endpoint is not None
        try:
            payload = self.transport.request(
                method="POST",
                url=f"{self.config.endpoint.rstrip('/')}/api/chat",
                payload={
                    "model": self.model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(
                                dict(invocation.input_payload),
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    "format": "json",
                    "stream": False,
                },
                timeout_seconds=min(invocation.timeout_ms, self.request_timeout_ms) / 1_000,
            )
            if not isinstance(payload, dict):
                raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
            message = payload.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
            structured = json.loads(content)
            if not isinstance(structured, dict):
                raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
            return structured, None
        except json.JSONDecodeError:
            return None, ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "Local Gemma returned malformed structured output.",
                False,
            )
        except GemmaTransportError as error:
            mapping = {
                GemmaTransportFailure.TIMEOUT: ProviderFailureClass.TIMEOUT,
                GemmaTransportFailure.MALFORMED: ProviderFailureClass.MALFORMED_OUTPUT,
                GemmaTransportFailure.UNAVAILABLE: ProviderFailureClass.UNAVAILABLE,
                GemmaTransportFailure.REJECTED: ProviderFailureClass.UNAVAILABLE,
            }
            classification = mapping[error.classification]
            return None, ProviderFailure(
                classification,
                "Local Gemma request failed safely.",
                classification in {ProviderFailureClass.TIMEOUT, ProviderFailureClass.UNAVAILABLE},
            )

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        host = "unconfigured"
        if self.config.endpoint:
            host = urllib.parse.urlsplit(self.config.endpoint).hostname or "unconfigured"
        return (
            ("adapter", "ollama-compatible-v1"),
            ("endpoint_host", host),
            ("model_family", LOCAL_GEMMA_FAMILY),
            ("model_version", self.model_version),
        )

    def _append_attempt(self, invocation: ProviderInvocation, input_digest: str) -> None:
        if self.ledger is None:
            return
        identity = evidence_identity(
            {
                "record_type": AIEvidenceRecordType.PROVIDER_INVOCATION_ATTEMPT.value,
                "request_id": invocation.request_id,
                "provider_id": self.identity.provider_id,
                "model_id": invocation.model_id,
                "model_version": self.model_version,
                "registry_revision": invocation.registry_revision,
                "capability": invocation.capability.value,
                "started_at": invocation.started_at,
                "input_digest": input_digest,
            }
        )
        self.ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=identity,
                record_type=AIEvidenceRecordType.PROVIDER_INVOCATION_ATTEMPT,
                request_id=invocation.request_id,
                task_correlation_id=invocation.task_correlation_id,
                provider_id=self.identity.provider_id,
                model_id=invocation.model_id,
                model_version=self.model_version,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=ExecutionLocation.LOCAL,
                routing_status="selected",
                fallback=False,
                started_at=invocation.started_at,
                ended_at=invocation.started_at,
                succeeded=False,
                failure_classification=None,
                input_digest=input_digest,
                output_digest=None,
                provider_metadata=self._metadata(),
            )
        )

    def _append_result(self, invocation: ProviderInvocation, result: ProviderResult) -> None:
        if self.ledger is None:
            return
        health_rejected = (
            result.failure is not None
            and result.failure.classification == ProviderFailureClass.UNAVAILABLE
        )
        record_type = (
            AIEvidenceRecordType.PROVIDER_HEALTH_REJECTED
            if health_rejected
            else (
                AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED
                if result.succeeded
                else AIEvidenceRecordType.PROVIDER_RESULT_FAILED
            )
        )
        self.ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=result.evidence.evidence_identity,
                record_type=record_type,
                request_id=invocation.request_id,
                task_correlation_id=invocation.task_correlation_id,
                provider_id=self.identity.provider_id,
                model_id=invocation.model_id,
                model_version=self.model_version,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=ExecutionLocation.LOCAL,
                routing_status="selected",
                fallback=False,
                started_at=invocation.started_at,
                ended_at=invocation.ended_at,
                succeeded=result.succeeded,
                failure_classification=result.evidence.failure_classification,
                input_digest=result.evidence.input_digest,
                output_digest=result.evidence.output_digest,
                provider_metadata=result.evidence.provider_metadata,
            )
        )
