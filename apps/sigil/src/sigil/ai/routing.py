"""Deterministic, capability-first and fail-closed model routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .registry import GovernedModelRegistry, RegistryValidationError, canonical_digest


class RoutingFailureClass(str, Enum):
    NO_SUITABLE_MODEL = "no_suitable_model"
    REGISTRY_INVALID = "registry_invalid"
    PROHIBITED_RESPONSIBILITY = "prohibited_responsibility"
    PREFERRED_ROUTE_UNAVAILABLE = "preferred_route_unavailable"


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    request_id: str
    task_correlation_id: str
    evidence_correlation_id: str
    responsibility: Responsibility
    required_capabilities: frozenset[Capability]
    preferred_model_family: str | None
    privacy_requirement: PrivacyTier
    maximum_cost_class: CostClass
    execution_location_preference: tuple[ExecutionLocation, ...]
    minimum_trust_tier: TrustTier
    timeout_ms: int
    fallback_allowed: bool

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        validate_identifier(self.evidence_correlation_id, "evidence_correlation_id")
        if self.preferred_model_family is not None:
            validate_identifier(self.preferred_model_family, "preferred_model_family")
        if not self.required_capabilities:
            raise ValueError("routing requires at least one capability")
        if not self.execution_location_preference:
            raise ValueError("execution location preference cannot be empty")
        if len(set(self.execution_location_preference)) != len(self.execution_location_preference):
            raise ValueError("execution location preference cannot contain duplicates")
        if self.timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    provider_id: str
    model_id: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    ranking: tuple[int, int, int, str, str] | None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    request_id: str
    task_correlation_id: str
    selected_provider_id: str | None
    selected_model_id: str | None
    considered_candidates: tuple[CandidateEvaluation, ...]
    fallback: bool
    decision_timestamp: str
    registry_revision: str
    evidence_identity: str
    failure_class: RoutingFailureClass | None
    broker_submission: bool = False
    paper_only: bool = True

    @property
    def succeeded(self) -> bool:
        return self.failure_class is None


class GovernedModelRouter:
    def __init__(self, registry: GovernedModelRegistry) -> None:
        self._registry = registry

    @classmethod
    def route_registry_data(
        cls,
        *,
        providers: tuple[ProviderIdentity, ...],
        models: tuple[ModelRegistration, ...],
        request: RoutingRequest,
        decision_timestamp: str,
    ) -> RoutingDecision:
        """Validate untrusted catalog data and return a decision even on rejection."""
        try:
            registry = GovernedModelRegistry(providers=providers, models=models)
        except RegistryValidationError as error:
            registry_revision = f"invalid:sha256:{canonical_digest({'error': str(error)})}"
            identity = {
                "request": asdict(request),
                "timestamp": decision_timestamp,
                "registry_revision": registry_revision,
                "failure": RoutingFailureClass.REGISTRY_INVALID.value,
                "paper_only": True,
                "broker_submission": False,
            }
            return RoutingDecision(
                request_id=request.request_id,
                task_correlation_id=request.task_correlation_id,
                selected_provider_id=None,
                selected_model_id=None,
                considered_candidates=(),
                fallback=False,
                decision_timestamp=decision_timestamp,
                registry_revision=registry_revision,
                evidence_identity=f"sha256:{canonical_digest(identity)}",
                failure_class=RoutingFailureClass.REGISTRY_INVALID,
            )
        return cls(registry).route(request, decision_timestamp=decision_timestamp)

    def route(self, request: RoutingRequest, *, decision_timestamp: str) -> RoutingDecision:
        if request.responsibility in PROHIBITED_RESPONSIBILITIES:
            return self._decision(
                request,
                decision_timestamp,
                (),
                None,
                RoutingFailureClass.PROHIBITED_RESPONSIBILITY,
            )

        providers = {provider.provider_id: provider for provider in self._registry.providers}
        evaluated = tuple(
            self._evaluate(model, providers[model.provider_id], request)
            for model in sorted(
                self._registry.models, key=lambda item: (item.provider_id, item.model_id)
            )
        )
        eligible = sorted(
            (candidate for candidate in evaluated if candidate.eligible),
            key=lambda candidate: candidate.ranking or (999, 999, 999, "", ""),
        )
        selected = eligible[0] if eligible else None
        failure = None if selected else RoutingFailureClass.NO_SUITABLE_MODEL
        if selected and not request.fallback_allowed:
            model = next(
                item for item in self._registry.models if item.model_id == selected.model_id
            )
            preferred_location = request.execution_location_preference[0]
            preferred_family = request.preferred_model_family
            if model.execution_location != preferred_location or (
                preferred_family is not None and model.family != preferred_family
            ):
                selected = None
                failure = RoutingFailureClass.PREFERRED_ROUTE_UNAVAILABLE
        return self._decision(request, decision_timestamp, evaluated, selected, failure)

    def _evaluate(self, model, provider, request: RoutingRequest) -> CandidateEvaluation:
        reasons: list[str] = []
        if not provider.enabled:
            reasons.append("provider_disabled")
        if provider.health != ProviderHealth.HEALTHY:
            reasons.append("provider_unhealthy")
        if not model.enabled:
            reasons.append("model_disabled")
        if model.health != ProviderHealth.HEALTHY:
            reasons.append("model_unhealthy")
        if not request.required_capabilities.issubset(model.capabilities):
            reasons.append("capability_mismatch")
        if request.responsibility not in model.allowed_responsibilities:
            reasons.append("responsibility_not_allowed")
        if request.responsibility in model.prohibited_responsibilities:
            reasons.append("responsibility_prohibited")
        if model.privacy_tier < request.privacy_requirement:
            reasons.append("privacy_requirement_unmet")
        if model.trust_tier < request.minimum_trust_tier:
            reasons.append("trust_requirement_unmet")
        if model.cost_class > request.maximum_cost_class:
            reasons.append("cost_class_exceeded")
        location_rank = self._rank_location(model.execution_location, request)
        family_rank = 0 if model.family == request.preferred_model_family else 1
        specialization_rank = len(model.capabilities - request.required_capabilities)
        ranking = (
            location_rank,
            family_rank,
            specialization_rank,
            model.provider_id,
            model.model_id,
        )
        return CandidateEvaluation(
            provider_id=model.provider_id,
            model_id=model.model_id,
            eligible=not reasons,
            rejection_reasons=tuple(sorted(reasons)),
            ranking=None if reasons else ranking,
        )

    @staticmethod
    def _rank_location(location: ExecutionLocation, request: RoutingRequest) -> int:
        try:
            return request.execution_location_preference.index(location)
        except ValueError:
            return len(request.execution_location_preference)

    def _decision(
        self,
        request: RoutingRequest,
        timestamp: str,
        candidates: tuple[CandidateEvaluation, ...],
        selected: CandidateEvaluation | None,
        failure: RoutingFailureClass | None,
    ) -> RoutingDecision:
        selected_model: ModelRegistration | None = None
        if selected:
            selected_model = next(
                model for model in self._registry.models if model.model_id == selected.model_id
            )
        fallback = bool(
            selected_model
            and (
                selected_model.execution_location != request.execution_location_preference[0]
                or (
                    request.preferred_model_family is not None
                    and selected_model.family != request.preferred_model_family
                )
            )
        )
        identity = {
            "request": asdict(request),
            "selected": None if selected is None else [selected.provider_id, selected.model_id],
            "candidates": [asdict(candidate) for candidate in candidates],
            "fallback": fallback,
            "timestamp": timestamp,
            "registry_revision": self._registry.revision,
            "failure": None if failure is None else failure.value,
            "paper_only": True,
            "broker_submission": False,
        }
        return RoutingDecision(
            request_id=request.request_id,
            task_correlation_id=request.task_correlation_id,
            selected_provider_id=None if selected is None else selected.provider_id,
            selected_model_id=None if selected is None else selected.model_id,
            considered_candidates=candidates,
            fallback=fallback,
            decision_timestamp=timestamp,
            registry_revision=self._registry.revision,
            evidence_identity=f"sha256:{canonical_digest(identity)}",
            failure_class=failure,
        )
