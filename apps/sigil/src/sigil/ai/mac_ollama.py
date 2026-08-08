"""Governed, disabled-by-default Mac Ollama role profile and embedding adapter."""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum

from .evidence import build_invocation_evidence
from .gemma import (
    GemmaTransport,
    GemmaTransportError,
    GemmaTransportFailure,
    LocalGemmaConfig,
    LocalGemmaProvider,
    UrllibGemmaTransport,
)
from .ledger import AIEvidenceRecordType, DurableAIEvidenceLedger, GovernedAIEvidenceRecord
from .models import (
    Capability,
    ExecutionLocation,
    ProviderHealth,
    ProviderIdentity,
    validate_identifier,
)
from .provider import ProviderFailure, ProviderFailureClass, ProviderInvocation, ProviderResult
from .registry import canonical_digest
from .retrieval import normalized_vector

PRIMARY_MODEL = "huihui_ai/gemma-4-abliterated:12b"
FAST_MODEL = "huihui_ai/gemma-4-abliterated:e4b"
EMBEDDING_MODEL = "embeddinggemma:latest"
FALLBACK_MODEL = "hermes-llama3.2:3b-64k"
MAC_OLLAMA_PROVIDER_ID = "mac-ollama"

GOVERNANCE_BOUNDARIES: dict[str, bool] = {
    "paper_only": True,
    "broker_submission": False,
    "execution_authorized": False,
    "approval_authority": False,
    "portfolio_mutation": False,
    "capital_authority": False,
    "policy_mutation": False,
    "credential_access": False,
    "arbitrary_shell": False,
    "arbitrary_filesystem": False,
    "governance_bypass": False,
}


class MacOllamaConfigurationError(ValueError):
    """The Mac Ollama profile violates a governed configuration invariant."""


class OllamaTextRole(str, Enum):
    PRIMARY_REASONING = "primary_reasoning"
    FAST_TEXT_REASONING = "fast_text_reasoning"
    COMPATIBILITY_FALLBACK = "compatibility_fallback"


@dataclass(frozen=True, slots=True)
class OllamaModelIdentity:
    manifest_identity: str
    manifest_digest: str | None
    layer_digests: tuple[str, ...]
    architecture: str | None
    family: str | None
    parameter_scale: str | None
    quantization: str | None
    model_size: int | None
    upstream_repository: str | None
    upstream_revision: str | None = None
    license_evidence: str | None = None


@dataclass(frozen=True, slots=True)
class MacOllamaProfileConfig:
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11434"
    device_id: str = "mac-local"
    fleet_role: str = "mac"
    primary_model: str = PRIMARY_MODEL
    fast_model: str = FAST_MODEL
    embedding_model: str = EMBEDDING_MODEL
    fallback_model: str = FALLBACK_MODEL
    embedding_adapter: str = "sentence_transformers"
    fallback_enabled: bool = False
    timeout_ms: int = 30_000
    embedding_timeout_ms: int = 15_000
    embedding_dimension: int = 768
    embedding_max_input_chars: int = 8_000
    embedding_max_batch_size: int = 8

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MacOllamaConfigurationError("Mac Ollama endpoint must be loopback HTTP")
        validate_identifier(self.device_id, "Mac device identity")
        if self.fleet_role != "mac":
            raise MacOllamaConfigurationError("Mac Ollama fleet role must be mac")
        expected = (PRIMARY_MODEL, FAST_MODEL, EMBEDDING_MODEL, FALLBACK_MODEL)
        configured = (
            self.primary_model,
            self.fast_model,
            self.embedding_model,
            self.fallback_model,
        )
        if configured != expected:
            raise MacOllamaConfigurationError("Mac Ollama model manifest identity is not approved")
        if self.embedding_adapter not in {"sentence_transformers", "ollama"}:
            raise MacOllamaConfigurationError("embedding adapter selection must be explicit")
        if not 100 <= self.timeout_ms <= 300_000 or not 100 <= self.embedding_timeout_ms <= 300_000:
            raise MacOllamaConfigurationError("Mac Ollama timeout is outside its governed bound")
        if not 2 <= self.embedding_dimension <= 4_096:
            raise MacOllamaConfigurationError("embedding dimension is outside its governed bound")
        if not 256 <= self.embedding_max_input_chars <= 100_000:
            raise MacOllamaConfigurationError("embedding input bound is invalid")
        if not 1 <= self.embedding_max_batch_size <= 32:
            raise MacOllamaConfigurationError("embedding batch bound is invalid")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> MacOllamaProfileConfig:
        source = os.environ if environment is None else environment
        truth = {"1", "true", "yes"}

        def integer(name: str, default: int) -> int:
            try:
                return int(source.get(name, str(default)))
            except ValueError as error:
                raise MacOllamaConfigurationError(f"{name} must be an integer") from error

        return cls(
            enabled=source.get("SIGIL_AI_MAC_OLLAMA_ENABLED", "").lower() in truth,
            endpoint=source.get("SIGIL_AI_MAC_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"),
            device_id=source.get("SIGIL_AI_MAC_OLLAMA_DEVICE_ID", "mac-local"),
            primary_model=source.get("SIGIL_AI_MAC_OLLAMA_PRIMARY_MODEL", PRIMARY_MODEL),
            fast_model=source.get("SIGIL_AI_MAC_OLLAMA_FAST_MODEL", FAST_MODEL),
            embedding_model=source.get("SIGIL_AI_MAC_OLLAMA_EMBEDDING_MODEL", EMBEDDING_MODEL),
            fallback_model=source.get("SIGIL_AI_MAC_OLLAMA_FALLBACK_MODEL", FALLBACK_MODEL),
            embedding_adapter=source.get(
                "SIGIL_AI_EMBEDDING_GEMMA_ADAPTER", "sentence_transformers"
            ),
            fallback_enabled=source.get("SIGIL_AI_MAC_OLLAMA_FALLBACK_ENABLED", "").lower()
            in truth,
            timeout_ms=integer("SIGIL_AI_MAC_OLLAMA_TIMEOUT_MS", 30_000),
            embedding_timeout_ms=integer("SIGIL_AI_MAC_OLLAMA_EMBEDDING_TIMEOUT_MS", 15_000),
            embedding_dimension=integer("SIGIL_AI_MAC_OLLAMA_EMBEDDING_DIMENSION", 768),
            embedding_max_input_chars=integer(
                "SIGIL_AI_MAC_OLLAMA_EMBEDDING_MAX_INPUT_CHARS", 8_000
            ),
            embedding_max_batch_size=integer("SIGIL_AI_MAC_OLLAMA_EMBEDDING_MAX_BATCH_SIZE", 8),
        )


def _show_identity(model: str, tags: object, show: object) -> OllamaModelIdentity:
    if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
    tag = next(
        (item for item in tags["models"] if isinstance(item, dict) and item.get("name") == model),
        None,
    )
    if tag is None:
        raise LookupError("model_unavailable")
    if not isinstance(show, dict):
        raise GemmaTransportError(GemmaTransportFailure.MALFORMED)
    details = show.get("details") if isinstance(show.get("details"), dict) else {}
    model_info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    layers = show.get("layers") if isinstance(show.get("layers"), list) else []
    layer_digests = tuple(
        sorted(
            item["digest"]
            for item in layers
            if isinstance(item, dict) and isinstance(item.get("digest"), str)
        )
    )
    architecture = model_info.get("general.architecture")
    upstream = show.get("remote_model") or show.get("remote_host")
    shown_model = show.get("model")
    return OllamaModelIdentity(
        manifest_identity=shown_model if isinstance(shown_model, str) else model,
        manifest_digest=tag.get("digest") if isinstance(tag.get("digest"), str) else None,
        layer_digests=layer_digests,
        architecture=architecture if isinstance(architecture, str) else None,
        family=details.get("family") if isinstance(details.get("family"), str) else None,
        parameter_scale=details.get("parameter_size")
        if isinstance(details.get("parameter_size"), str)
        else None,
        quantization=details.get("quantization_level")
        if isinstance(details.get("quantization_level"), str)
        else None,
        model_size=tag.get("size") if isinstance(tag.get("size"), int) else None,
        upstream_repository=upstream if isinstance(upstream, str) else None,
    )


class MacOllamaInspector:
    def __init__(self, config: MacOllamaProfileConfig, transport: GemmaTransport | None = None):
        self.config = config
        self.transport = transport or UrllibGemmaTransport()

    def inspect_model(self, model: str) -> OllamaModelIdentity:
        tags = self.transport.request(
            method="GET",
            url=f"{self.config.endpoint.rstrip('/')}/api/tags",
            payload=None,
            timeout_seconds=self.config.timeout_ms / 1_000,
        )
        show = self.transport.request(
            method="POST",
            url=f"{self.config.endpoint.rstrip('/')}/api/show",
            payload={"model": model, "verbose": True},
            timeout_seconds=self.config.timeout_ms / 1_000,
        )
        return _show_identity(model, tags, show)

    def status(self) -> dict[str, object]:
        roles = {
            "primary": (self.config.primary_model, False, self.config.enabled),
            "fast": (self.config.fast_model, False, self.config.enabled),
            "embedding": (
                self.config.embedding_model,
                False,
                self.config.enabled and self.config.embedding_adapter == "ollama",
            ),
            "fallback": (
                self.config.fallback_model,
                True,
                self.config.enabled and self.config.fallback_enabled,
            ),
        }
        result: dict[str, object] = {}
        for role, (model, deprecated, configured) in roles.items():
            health = "disabled" if not configured else "configured_unverified"
            identity = None
            reason = None
            if configured:
                try:
                    identity = self.inspect_model(model)
                    if identity.manifest_identity != model:
                        health, reason = "identity_mismatch", "manifest_identity_mismatch"
                    else:
                        health = "healthy"
                except LookupError:
                    health, reason = "unavailable", "model_unavailable"
                except GemmaTransportError as error:
                    health, reason = "unavailable", error.classification.value
            result[role] = {
                "configured": configured,
                "model_identity": model,
                "health": health,
                "identity_match": health == "healthy",
                "readiness": "ready" if health == "healthy" else "not_ready",
                "reason": reason,
                "deprecated": deprecated,
                "enabled": configured,
                "admission_state": "admitted" if health == "healthy" else "rejected",
                "manifest": None if identity is None else asdict(identity),
                "upstream_revision_evidence": "unknown"
                if identity is None or identity.upstream_revision is None
                else identity.upstream_revision,
                "license_evidence": "unknown"
                if identity is None or identity.license_evidence is None
                else identity.license_evidence,
            }
        return {
            "enabled": self.config.enabled,
            "device_identity": self.config.device_id,
            "fleet_role": self.config.fleet_role,
            "endpoint_classification": "loopback_http",
            "embedding_adapter": self.config.embedding_adapter,
            "roles": result,
            **GOVERNANCE_BOUNDARIES,
        }


class MacOllamaRoleProvider(LocalGemmaProvider):
    """One independently healthy provider instance for one approved text role."""

    def __init__(
        self,
        profile: MacOllamaProfileConfig,
        role: OllamaTextRole,
        *,
        transport: GemmaTransport | None = None,
        ledger: DurableAIEvidenceLedger | None = None,
    ) -> None:
        model = {
            OllamaTextRole.PRIMARY_REASONING: profile.primary_model,
            OllamaTextRole.FAST_TEXT_REASONING: profile.fast_model,
            OllamaTextRole.COMPATIBILITY_FALLBACK: profile.fallback_model,
        }[role]
        enabled = profile.enabled and (
            role is not OllamaTextRole.COMPATIBILITY_FALLBACK or profile.fallback_enabled
        )
        super().__init__(
            LocalGemmaConfig(
                enabled=enabled,
                endpoint=profile.endpoint,
                model_id=model,
                model_version="ollama-manifest-v1",
                request_timeout_ms=profile.timeout_ms,
                keep_alive=0,
            ),
            transport=transport,
            ledger=ledger,
        )
        self.profile = profile
        self.role = role
        self.identity = self.identity.__class__(
            f"mac-ollama-{role.value.replace('_', '-')}",
            ExecutionLocation.LOCAL,
            health=self.identity.health,
            enabled=enabled,
            metadata=(("device", profile.device_id), ("role", role.value)),
        )

    def _set_health(self, health: ProviderHealth) -> None:
        self.identity = ProviderIdentity(
            f"mac-ollama-{self.role.value.replace('_', '-')}",
            ExecutionLocation.LOCAL,
            health=health,
            enabled=self.config.enabled,
            metadata=(("device", self.profile.device_id), ("role", self.role.value)),
        )

    def health_probe(self):
        health = super().health_probe()
        if health.health is ProviderHealth.HEALTHY:
            try:
                identity = MacOllamaInspector(self.profile, self.transport).inspect_model(
                    self.model_id
                )
                if identity.manifest_identity != self.model_id:
                    self._set_health(ProviderHealth.UNAVAILABLE)
                    return health.__class__(
                        ProviderHealth.UNAVAILABLE,
                        "model_identity_mismatch",
                        self.identity.provider_id,
                    )
            except (LookupError, GemmaTransportError):
                self._set_health(ProviderHealth.UNAVAILABLE)
                return health.__class__(
                    ProviderHealth.UNAVAILABLE, "provider_unavailable", self.identity.provider_id
                )
        return health.__class__(health.health, health.classification, self.identity.provider_id)


@dataclass(frozen=True, slots=True)
class MacOllamaRouteRequest:
    request_id: str
    role: OllamaTextRole
    fallback_allowed: bool = False
    fallback_reason: str | None = None
    task_correlation_id: str = "mac-ollama-routing"
    decided_at: str = "1970-01-01T00:00:00Z"
    registry_revision: str = (
        "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    )


@dataclass(frozen=True, slots=True)
class MacOllamaRouteDecision:
    selected_role: OllamaTextRole | None
    selected_model: str | None
    admitted: bool
    reason: str
    evidence_digest: str
    paper_only: bool = True
    broker_submission: bool = False
    execution_authorized: bool = False
    approval_authority: bool = False


def route_mac_ollama(
    request: MacOllamaRouteRequest,
    profile: MacOllamaProfileConfig,
    providers: Mapping[OllamaTextRole, MacOllamaRoleProvider],
    ledger: DurableAIEvidenceLedger | None = None,
) -> MacOllamaRouteDecision:
    role = request.role
    reason = "requested_role"
    if role is OllamaTextRole.COMPATIBILITY_FALLBACK:
        if not request.fallback_allowed:
            role, reason = None, "request_fallback_permission_required"
        elif not profile.fallback_enabled:
            role, reason = None, "profile_fallback_disabled"
        elif not request.fallback_reason:
            role, reason = None, "documented_fallback_reason_required"
    if role is not None:
        provider = providers.get(role)
        if provider is None or provider.health_probe().health is not ProviderHealth.HEALTHY:
            role, reason = None, "requested_role_unavailable"
    model = None if role is None else providers[role].model_id
    identity = {
        "request_id": request.request_id,
        "requested_role": request.role.value,
        "selected_role": None if role is None else role.value,
        "selected_model": model,
        "reason": reason,
        **GOVERNANCE_BOUNDARIES,
    }
    decision = MacOllamaRouteDecision(
        selected_role=role,
        selected_model=model,
        admitted=role is not None,
        reason=reason,
        evidence_digest=f"sha256:{canonical_digest(identity)}",
    )
    if ledger is not None:
        ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=decision.evidence_digest,
                record_type=AIEvidenceRecordType.FALLBACK_DECISION
                if request.role is OllamaTextRole.COMPATIBILITY_FALLBACK
                else AIEvidenceRecordType.ROUTING_DECISION,
                request_id=request.request_id,
                task_correlation_id=request.task_correlation_id,
                provider_id=None if role is None else providers[role].identity.provider_id,
                model_id=decision.selected_model,
                model_version=None if role is None else providers[role].model_version,
                registry_revision=request.registry_revision,
                capability=Capability.REASONING,
                execution_location=None if role is None else ExecutionLocation.LOCAL,
                routing_status="selected" if decision.admitted else "rejected",
                fallback=request.role is OllamaTextRole.COMPATIBILITY_FALLBACK,
                started_at=request.decided_at,
                ended_at=request.decided_at,
                succeeded=decision.admitted,
                failure_classification=None if decision.admitted else decision.reason,
                input_digest=f"sha256:{canonical_digest(asdict(request))}",
                output_digest=None,
                provider_metadata=(("reason", decision.reason),),
            )
        )
    return decision


class OllamaEmbeddingProvider:
    capabilities = frozenset({Capability.EMBEDDINGS, Capability.SEMANTIC_RETRIEVAL})
    model_family = "embeddinggemma"
    input_contract = "application/json;schema=sigil.ai.input.embedding.v1"
    output_contract = "application/json;schema=sigil.ai.output.embedding.v1"

    def __init__(
        self,
        profile: MacOllamaProfileConfig,
        *,
        transport: GemmaTransport | None = None,
        ledger: DurableAIEvidenceLedger | None = None,
    ) -> None:
        self.profile = profile
        self.transport = transport or UrllibGemmaTransport()
        self.ledger = ledger
        self.model_id = profile.embedding_model
        self.model_version = "ollama-manifest-v1"
        self.request_timeout_ms = profile.embedding_timeout_ms
        enabled = profile.enabled and profile.embedding_adapter == "ollama"
        self.identity = ProviderIdentity(
            "mac-ollama-embedding",
            ExecutionLocation.LOCAL,
            health=ProviderHealth.DEGRADED if enabled else ProviderHealth.UNAVAILABLE,
            enabled=enabled,
            metadata=(("adapter", "ollama-api-embed-v1"),),
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        texts = invocation.input_payload.get("texts")
        failure = None
        output = None
        if not self.identity.enabled:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Ollama embedding adapter is disabled.", False
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Embedding model identity mismatch.",
                False,
            )
        elif invocation.capability not in self.capabilities:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH, "Embedding capability mismatch.", False
            )
        elif (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= self.profile.embedding_max_batch_size
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > self.profile.embedding_max_input_chars
                for item in texts
            )
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "Embedding input exceeded its governed bound.",
                False,
            )
        else:
            try:
                identity = MacOllamaInspector(self.profile, self.transport).inspect_model(
                    self.model_id
                )
                if identity.manifest_identity != self.model_id:
                    raise LookupError("identity_mismatch")
                response = self.transport.request(
                    method="POST",
                    url=f"{self.profile.endpoint.rstrip('/')}/api/embed",
                    payload={"model": self.model_id, "input": texts},
                    timeout_seconds=min(invocation.timeout_ms, self.request_timeout_ms) / 1_000,
                )
                vectors = response.get("embeddings") if isinstance(response, dict) else None
                if not isinstance(vectors, Sequence) or len(vectors) != len(texts):
                    raise ValueError("batch mismatch")
                normalized = [
                    list(normalized_vector(value, self.profile.embedding_dimension))
                    for value in vectors
                ]
                output = {
                    "schema_version": 1,
                    "model_id": self.model_id,
                    "model_version": self.model_version,
                    "vector_dimension": self.profile.embedding_dimension,
                    "vectors": normalized,
                    "vector_digests": [f"sha256:{canonical_digest(v)}" for v in normalized],
                    "normalized": True,
                    **GOVERNANCE_BOUNDARIES,
                }
            except LookupError:
                failure = ProviderFailure(
                    ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                    "Embedding model identity mismatch.",
                    False,
                )
            except GemmaTransportError as error:
                cls = (
                    ProviderFailureClass.TIMEOUT
                    if error.classification is GemmaTransportFailure.TIMEOUT
                    else ProviderFailureClass.UNAVAILABLE
                )
                failure = ProviderFailure(cls, "Ollama embedding request failed safely.", True)
            except (TypeError, ValueError):
                failure = ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Ollama embedding returned malformed vectors.",
                    False,
                )
        evidence_output = (
            None if output is None else {k: v for k, v in output.items() if k != "vectors"}
        )
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
            input_payload={"batch_size": len(texts) if isinstance(texts, list) else 0},
            output_payload=evidence_output,
            provider_metadata=(("adapter", "ollama-api-embed-v1"),),
        )
        result = ProviderResult(output=output, failure=failure, evidence=evidence)
        if self.ledger is not None:
            self.ledger.append(
                GovernedAIEvidenceRecord(
                    evidence_identity=evidence.evidence_identity,
                    record_type=AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED
                    if result.succeeded
                    else AIEvidenceRecordType.PROVIDER_RESULT_FAILED,
                    request_id=invocation.request_id,
                    task_correlation_id=invocation.task_correlation_id,
                    provider_id=self.identity.provider_id,
                    model_id=self.model_id,
                    model_version=self.model_version,
                    registry_revision=invocation.registry_revision,
                    capability=invocation.capability,
                    execution_location=ExecutionLocation.LOCAL,
                    routing_status="selected",
                    fallback=False,
                    started_at=invocation.started_at,
                    ended_at=invocation.ended_at,
                    succeeded=result.succeeded,
                    failure_classification=evidence.failure_classification,
                    input_digest=evidence.input_digest,
                    output_digest=evidence.output_digest,
                    provider_metadata=evidence.provider_metadata,
                )
            )
        return result
