"""Model-neutral governed analysis coordination inside the backend authority layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .analysis import (
    AnalysisValidationError,
    GovernedAnalysisArtifact,
    GovernedAnalysisRequest,
    GovernedOutputSchema,
    build_analysis_artifact,
    validate_generic_analysis,
)
from .artifact_store import (
    AnalysisArtifactStoreError,
    DurableAnalysisArtifactStore,
)
from .evidence import build_invocation_evidence
from .handoff import GovernedModelWorkRequest
from .ledger import (
    AIEvidenceLedgerError,
    AIEvidenceRecordType,
    DurableAIEvidenceLedger,
    GovernedAIEvidenceRecord,
    append_routing_decision,
    evidence_identity,
)
from .provider import (
    ModelProvider,
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)
from .registry import GovernedModelRegistry, canonical_digest
from .routing import GovernedModelRouter, RoutingRequest


class AnalysisFailureClass(str):
    SERVICE_DISABLED = "service_disabled"
    ROUTING_REJECTED = "routing_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    ARTIFACT_PERSISTENCE_FAILED = "artifact_persistence_failed"
    EVIDENCE_PERSISTENCE_FAILED = "evidence_persistence_failed"


@dataclass(frozen=True, slots=True)
class GovernedAnalysisResponse:
    request_id: str
    artifact: GovernedAnalysisArtifact | None
    routing_evidence_id: str | None
    invocation_evidence_id: str | None
    failure_classification: str | None
    routing_summary: str
    limitations: tuple[str, ...]
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    @property
    def succeeded(self) -> bool:
        return self.artifact is not None and self.failure_classification is None


@dataclass(frozen=True, slots=True)
class GovernedAnalysisStatus:
    enabled: bool
    provider_availability: tuple[tuple[str, str], ...]
    registry_revision: str
    last_successful_analysis: str | None
    last_failure_classification: str | None
    evidence_ledger_health: str
    artifact_store_health: str
    configured_models: tuple[str, ...]
    paper_only: bool = True
    broker_submission: bool = False


class GovernedAnalysisService:
    def __init__(
        self,
        *,
        registry: GovernedModelRegistry,
        providers: Mapping[str, ModelProvider],
        evidence_ledger: DurableAIEvidenceLedger,
        artifact_store: DurableAnalysisArtifactStore,
        enabled: bool = False,
    ) -> None:
        if any(getattr(provider, "ledger", None) is not None for provider in providers.values()):
            raise ValueError("analysis service providers must delegate evidence ownership")
        if any(key != provider.identity.provider_id for key, provider in providers.items()):
            raise ValueError("analysis provider mapping identity mismatch")
        self.registry = registry
        self.providers = dict(providers)
        self.evidence_ledger = evidence_ledger
        self.artifact_store = artifact_store
        self.enabled = enabled
        self._last_failure: str | None = None

    def analyze(
        self, request: GovernedAnalysisRequest, *, completed_at: str
    ) -> GovernedAnalysisResponse:
        if not self.enabled:
            return self._failure(
                request,
                AnalysisFailureClass.SERVICE_DISABLED,
                "service disabled",
            )
        route_request = RoutingRequest(
            request_id=request.request_id,
            task_correlation_id=request.task_correlation_id,
            evidence_correlation_id=f"analysis-{request.request_id}",
            responsibility=request.responsibility,
            required_capabilities=frozenset({request.requested_capability}),
            preferred_model_family="gemma",
            privacy_requirement=request.privacy_requirement,
            maximum_cost_class=request.maximum_cost_class,
            execution_location_preference=request.execution_location_preference,
            minimum_trust_tier=request.minimum_trust_tier,
            timeout_ms=request.timeout_ms,
            fallback_allowed=request.fallback_permission,
        )
        decision = GovernedModelRouter(self.registry).route(
            route_request, decision_timestamp=request.requested_at
        )
        selected_model = next(
            (item for item in self.registry.models if item.model_id == decision.selected_model_id),
            None,
        )
        try:
            append_routing_decision(
                self.evidence_ledger,
                request=route_request,
                decision=decision,
                model_version=None if selected_model is None else selected_model.version,
                execution_location=(
                    None if selected_model is None else selected_model.execution_location
                ),
            )
        except AIEvidenceLedgerError:
            return self._failure(
                request,
                AnalysisFailureClass.EVIDENCE_PERSISTENCE_FAILED,
                "evidence ledger unavailable",
            )
        if not decision.succeeded or selected_model is None:
            failure = (
                AnalysisFailureClass.ROUTING_REJECTED
                if decision.failure_class is None
                else decision.failure_class.value
            )
            return self._failure(request, failure, "routing rejected", decision.evidence_identity)

        provider = self.providers.get(selected_model.provider_id)
        invocation = ProviderInvocation(
            request_id=request.request_id,
            task_correlation_id=request.task_correlation_id,
            model_id=selected_model.model_id,
            registry_revision=self.registry.revision,
            capability=request.requested_capability,
            input_payload={
                "input_digest": request.input_digest,
                "evidence_context_digests": list(request.evidence_context_digests),
                "expected_output_schema": request.expected_output_schema.value,
                "responsibility": request.responsibility.value,
            },
            timeout_ms=request.timeout_ms,
            started_at=request.requested_at,
            ended_at=completed_at,
        )
        try:
            self._append_attempt(
                invocation,
                selected_model.provider_id,
                selected_model.version,
                selected_model.execution_location,
            )
        except AIEvidenceLedgerError:
            return self._failure(
                request,
                AnalysisFailureClass.EVIDENCE_PERSISTENCE_FAILED,
                "evidence ledger unavailable",
                decision.evidence_identity,
            )
        if provider is None:
            result = self._unavailable_result(invocation, selected_model.execution_location)
        else:
            result = provider.invoke(invocation)
        if not self._result_matches(
            result,
            invocation,
            selected_model.provider_id,
            selected_model.execution_location,
        ):
            result = self._malformed_result(
                invocation, selected_model.provider_id, selected_model.execution_location
            )
        try:
            self._append_result(result, invocation, selected_model.version)
        except AIEvidenceLedgerError:
            return self._failure(
                request,
                AnalysisFailureClass.EVIDENCE_PERSISTENCE_FAILED,
                "evidence ledger unavailable",
                decision.evidence_identity,
                result.evidence.evidence_identity,
            )
        if not result.succeeded or result.output is None:
            classification = (
                AnalysisFailureClass.PROVIDER_UNAVAILABLE
                if result.failure is None
                else result.failure.classification.value
            )
            return self._failure(
                request,
                classification,
                "provider invocation failed",
                decision.evidence_identity,
                result.evidence.evidence_identity,
            )
        try:
            payload = validate_generic_analysis(
                result.output, trusted_evidence=request.evidence_context_digests
            )
            if result.evidence.output_digest is None:
                raise AnalysisValidationError("provider output evidence digest is missing")
        except AnalysisValidationError:
            try:
                rejected_id = self._append_output_rejection(
                    request, invocation, selected_model.version, result
                )
            except AIEvidenceLedgerError:
                return self._failure(
                    request,
                    AnalysisFailureClass.EVIDENCE_PERSISTENCE_FAILED,
                    "evidence ledger unavailable",
                    decision.evidence_identity,
                    result.evidence.evidence_identity,
                )
            return self._failure(
                request,
                AnalysisFailureClass.OUTPUT_VALIDATION_FAILED,
                "provider output rejected",
                decision.evidence_identity,
                rejected_id,
            )

        artifact = build_analysis_artifact(
            request_id=request.request_id,
            task_correlation_id=request.task_correlation_id,
            provider_id=selected_model.provider_id,
            model_id=selected_model.model_id,
            model_version=selected_model.version,
            capability=request.requested_capability,
            responsibility=request.responsibility,
            created_at=completed_at,
            routing_evidence_id=decision.evidence_identity,
            invocation_evidence_id=result.evidence.evidence_identity,
            input_digest=request.input_digest,
            output_digest=result.evidence.output_digest,
            structured_payload=payload,
            citations=payload.evidence_references,
            confidence=payload.confidence,
            limitations=payload.limitations,
            stale_after=None,
        )
        try:
            self.artifact_store.append(artifact)
        except AnalysisArtifactStoreError:
            return self._failure(
                request,
                AnalysisFailureClass.ARTIFACT_PERSISTENCE_FAILED,
                "artifact store unavailable",
                decision.evidence_identity,
                result.evidence.evidence_identity,
            )
        self._last_failure = None
        return GovernedAnalysisResponse(
            request_id=request.request_id,
            artifact=artifact,
            routing_evidence_id=decision.evidence_identity,
            invocation_evidence_id=result.evidence.evidence_identity,
            failure_classification=None,
            routing_summary=(
                "fallback selected" if decision.fallback else "preferred model selected"
            ),
            limitations=payload.limitations,
        )

    def analyze_hermes(
        self,
        work: GovernedModelWorkRequest,
        *,
        requested_at: str,
        completed_at: str,
    ) -> GovernedAnalysisResponse:
        request = GovernedAnalysisRequest(
            request_id=work.request_id,
            task_correlation_id=work.task_correlation_id,
            requested_capability=work.capability,
            responsibility=work.responsibility,
            privacy_requirement=work.privacy_requirement,
            maximum_cost_class=work.routing_request().maximum_cost_class,
            minimum_trust_tier=work.routing_request().minimum_trust_tier,
            execution_location_preference=work.routing_request().execution_location_preference,
            fallback_permission=work.fallback_allowed,
            timeout_ms=work.timeout_ms,
            input_digest=f"sha256:{canonical_digest(asdict(work))}",
            evidence_context_digests=work.evidence_context,
            expected_output_schema=GovernedOutputSchema(work.expected_output_contract),
            requested_at=requested_at,
        )
        return self.analyze(request, completed_at=completed_at)

    def status(self) -> GovernedAnalysisStatus:
        try:
            evidence_records = self.evidence_ledger.read_records()
            evidence_health = "healthy"
        except AIEvidenceLedgerError:
            evidence_records = ()
            evidence_health = "corrupt"
        try:
            artifacts = self.artifact_store.read_artifacts()
            artifact_health = "healthy"
        except AnalysisArtifactStoreError:
            artifacts = ()
            artifact_health = "corrupt"
        return GovernedAnalysisStatus(
            enabled=self.enabled,
            provider_availability=tuple(
                sorted(
                    (provider_id, provider.identity.health.value)
                    for provider_id, provider in self.providers.items()
                )
            ),
            registry_revision=self.registry.revision,
            last_successful_analysis=None if not artifacts else artifacts[-1].artifact_id,
            last_failure_classification=self._last_failure
            or next(
                (
                    item.failure_classification
                    for item in reversed(evidence_records)
                    if item.failure_classification is not None
                ),
                None,
            ),
            evidence_ledger_health=evidence_health,
            artifact_store_health=artifact_health,
            configured_models=tuple(sorted(model.model_id for model in self.registry.models)),
        )

    def _append_attempt(self, invocation, provider_id, model_version, location) -> None:
        digest = f"sha256:{canonical_digest(dict(invocation.input_payload))}"
        identity = evidence_identity(
            {"type": "analysis_attempt", "request": invocation.request_id, "digest": digest}
        )
        self.evidence_ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=identity,
                record_type=AIEvidenceRecordType.PROVIDER_INVOCATION_ATTEMPT,
                request_id=invocation.request_id,
                task_correlation_id=invocation.task_correlation_id,
                provider_id=provider_id,
                model_id=invocation.model_id,
                model_version=model_version,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=location,
                routing_status="selected",
                fallback=False,
                started_at=invocation.started_at,
                ended_at=invocation.started_at,
                succeeded=False,
                failure_classification=None,
                input_digest=digest,
                output_digest=None,
            )
        )

    def _append_result(self, result, invocation, model_version) -> None:
        self.evidence_ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=result.evidence.evidence_identity,
                record_type=(
                    AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED
                    if result.succeeded
                    else AIEvidenceRecordType.PROVIDER_RESULT_FAILED
                ),
                request_id=invocation.request_id,
                task_correlation_id=invocation.task_correlation_id,
                provider_id=result.evidence.provider_id,
                model_id=invocation.model_id,
                model_version=model_version,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=result.evidence.execution_location,
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

    def _append_output_rejection(self, request, invocation, model_version, result) -> str:
        identity = evidence_identity(
            {"type": "output_rejection", "invocation": result.evidence.evidence_identity}
        )
        self.evidence_ledger.append(
            GovernedAIEvidenceRecord(
                evidence_identity=identity,
                record_type=AIEvidenceRecordType.ANALYSIS_OUTPUT_REJECTED,
                request_id=request.request_id,
                task_correlation_id=request.task_correlation_id,
                provider_id=result.evidence.provider_id,
                model_id=invocation.model_id,
                model_version=model_version,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=result.evidence.execution_location,
                routing_status="selected",
                fallback=False,
                started_at=invocation.started_at,
                ended_at=invocation.ended_at,
                succeeded=False,
                failure_classification=AnalysisFailureClass.OUTPUT_VALIDATION_FAILED,
                input_digest=result.evidence.input_digest,
                output_digest=result.evidence.output_digest,
                provider_metadata=result.evidence.provider_metadata,
            )
        )
        return identity

    @staticmethod
    def _unavailable_result(invocation, location) -> ProviderResult:
        failure = ProviderFailure(
            ProviderFailureClass.UNAVAILABLE, "Selected provider is unavailable.", True
        )
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id="provider-unavailable",
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=False,
            failure_classification=failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=None,
        )
        return ProviderResult(output=None, failure=failure, evidence=evidence)

    @staticmethod
    def _result_matches(result, invocation, provider_id, location) -> bool:
        evidence = result.evidence
        return (
            evidence.request_id == invocation.request_id
            and evidence.task_correlation_id == invocation.task_correlation_id
            and evidence.provider_id == provider_id
            and evidence.model_id == invocation.model_id
            and evidence.registry_revision == invocation.registry_revision
            and evidence.capability == invocation.capability
            and evidence.execution_location == location
            and evidence.paper_only is True
            and evidence.broker_submission is False
            and result.paper_only is True
            and result.broker_submission is False
        )

    @staticmethod
    def _malformed_result(invocation, provider_id, location) -> ProviderResult:
        failure = ProviderFailure(
            ProviderFailureClass.MALFORMED_OUTPUT,
            "Provider evidence did not match the governed invocation.",
            False,
        )
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=False,
            failure_classification=failure.classification.value,
            input_payload=dict(invocation.input_payload),
            output_payload=None,
        )
        return ProviderResult(output=None, failure=failure, evidence=evidence)

    def _failure(
        self,
        request,
        classification,
        limitation,
        routing_evidence_id=None,
        invocation_evidence_id=None,
    ) -> GovernedAnalysisResponse:
        self._last_failure = classification
        return GovernedAnalysisResponse(
            request_id=request.request_id,
            artifact=None,
            routing_evidence_id=routing_evidence_id,
            invocation_evidence_id=invocation_evidence_id,
            failure_classification=classification,
            routing_summary=limitation,
            limitations=(limitation,),
        )
