"""Routing score (spec section 12).

The optimization target is Principle C — *lowest expected cost per
successful, safe task* — so eligible candidates are ranked primarily by
``expected_cost_to_success_micros`` (raw cost divided by predicted success
probability), not by raw price. A weighted, centralized, configurable
:class:`RoutingWeights` score is computed alongside for explainability and as
a deterministic tiebreaker, but it is never the primary sort key: a cheap
route with a poor track record should lose to an expensive route that
reliably succeeds, and burying that behind a single opaque weighted number
would make the "why did Runway choose X" question harder to answer, not
easier.

No step here ever authorizes spend or invokes a provider — this module only
produces evidence (spec: "routing returns immutable evidence").
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runway.budget import BudgetLedger
from runway.capabilities import Capability
from runway.classification import DataClassification
from runway.health import HealthProbe, HealthStatus
from runway.identifiers import clean_identifier, contains_sensitive_marker, digest
from runway.lifecycle import is_routable
from runway.outcomes import HistoricalPerformanceStore
from runway.pricing import UsageEstimate, cache_savings_micros, estimate_cost_micros
from runway.registry import LatencyClass, Registry
from runway.trust import TrustTier, data_classification_allowed

_MIN_SUCCESS_PROBABILITY = 0.001
_LATENCY_SCORE = {LatencyClass.INTERACTIVE: 100, LatencyClass.STANDARD: 60, LatencyClass.BATCH: 20}
_HEALTH_SCORE = {HealthStatus.HEALTHY: 100, HealthStatus.DEGRADED: 50, HealthStatus.UNKNOWN: 25}


class RoutingWeights(BaseModel):
    """Centralized, configurable scoring weights — the single place factor
    importance is decided, so no magic numbers are scattered through
    :class:`RouteScorer`. Overridable per task class by constructing a
    different instance and passing it to :class:`RouteScorer`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    quality: int = Field(default=25, ge=0)
    reliability: int = Field(default=25, ge=0)
    cost: int = Field(default=15, ge=0)
    trust: int = Field(default=10, ge=0)
    latency: int = Field(default=10, ge=0)
    health: int = Field(default=10, ge=0)
    cache: int = Field(default=3, ge=0)
    preference: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def _positive_total(self) -> "RoutingWeights":
        if self.total <= 0:
            raise ValueError("routing weights must have a positive total")
        return self

    @property
    def total(self) -> int:
        return (
            self.quality + self.reliability + self.cost + self.trust
            + self.latency + self.health + self.cache + self.preference
        )


class RouteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    task_type: str
    data_classification: DataClassification
    usage_estimate: UsageEstimate
    required_capabilities: Tuple[Capability, ...] = ()
    minimum_quality: int = Field(default=0, ge=0, le=100)
    maximum_latency_class: LatencyClass = LatencyClass.BATCH
    minimum_trust_tier: TrustTier = TrustTier.EXPERIMENTAL_UNKNOWN
    budget_limit_micros: int = Field(default=0, ge=0, description="0 = no request-level ceiling beyond the ledger")
    preferred_providers: Tuple[str, ...] = ()
    excluded_providers: Tuple[str, ...] = ()
    excluded_models: Tuple[str, ...] = ()

    @field_validator("request_id", "task_type")
    @classmethod
    def _ids(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("preferred_providers", "excluded_providers", "excluded_models")
    @classmethod
    def _id_sets(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(sorted({clean_identifier(value) for value in values}))

    @model_validator(mode="after")
    def _consistent(self) -> "RouteRequest":
        conflict = set(self.preferred_providers) & set(self.excluded_providers)
        if conflict:
            raise ValueError(f"providers cannot be preferred and excluded: {', '.join(sorted(conflict))}")
        return self

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class CandidateDisposition(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"


class RouteCandidateScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    model_key: str
    disposition: CandidateDisposition
    rejection_reasons: Tuple[str, ...] = ()
    estimated_cost_micros: Optional[int] = Field(default=None, ge=0)
    predicted_success_probability_permille: Optional[int] = Field(default=None, ge=0, le=1000)
    expected_cost_to_success_micros: Optional[int] = Field(default=None, ge=0)
    score: Optional[int] = Field(default=None, ge=0)
    quality_factor: Optional[int] = None
    reliability_factor: Optional[int] = None
    latency_factor: Optional[int] = None
    cost_factor: Optional[int] = None
    trust_factor: Optional[int] = None
    health_factor: Optional[int] = None
    cache_factor: Optional[int] = None
    preference_factor: Optional[int] = None

    @model_validator(mode="after")
    def _consistent_disposition(self) -> "RouteCandidateScore":
        if self.disposition == CandidateDisposition.ELIGIBLE:
            if self.rejection_reasons or self.score is None or self.expected_cost_to_success_micros is None:
                raise ValueError("eligible candidates require a score and expected_cost_to_success")
        elif not self.rejection_reasons or self.score is not None:
            raise ValueError("rejected candidates require reasons and no score")
        return self


class RouteOutcome(str, Enum):
    ROUTED = "routed"
    NO_ROUTE = "no_route"


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    request_id: str
    request_fingerprint: str = Field(..., min_length=64, max_length=64)
    selected_provider_id: Optional[str] = None
    selected_model_key: Optional[str] = None
    estimated_cost_micros: Optional[int] = Field(default=None, ge=0)
    expected_cost_to_success_micros: Optional[int] = Field(default=None, ge=0)
    outcome: RouteOutcome
    candidates: Tuple[RouteCandidateScore, ...]
    fallback_chain: Tuple[str, ...]
    weights: RoutingWeights
    created_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "RouteDecision":
        selected = self.selected_provider_id is not None and self.selected_model_key is not None
        no_route = self.outcome == RouteOutcome.NO_ROUTE
        if selected == no_route:
            raise ValueError("route selection and outcome are inconsistent")
        encoded = self.model_dump_json().lower()
        if contains_sensitive_marker(encoded) or "prompt" in encoded:
            raise ValueError("route decision must not contain sensitive or prompt content")
        return self


class RouteScorer:
    def __init__(
        self,
        registry: Registry,
        *,
        weights: Optional[RoutingWeights] = None,
        health_probe: HealthProbe,
        budget_ledger: BudgetLedger,
        performance_store: HistoricalPerformanceStore,
        success_prior_weight: int = 5,
    ) -> None:
        self._registry = registry
        self._weights = weights or RoutingWeights()
        self._health = health_probe
        self._budget = budget_ledger
        self._performance = performance_store
        self._prior_weight = success_prior_weight

    def route(self, request: RouteRequest, *, timestamp: int) -> RouteDecision:
        candidates_models = [
            model for model in self._registry.models
            if request.task_type in model.task_types
            and model.provider_id not in request.excluded_providers
            and model.model_key not in request.excluded_models
        ]

        provisional: list[dict] = []
        rejected: list[RouteCandidateScore] = []
        for model in sorted(candidates_models, key=lambda item: item.model_key):
            provider = self._registry.provider(model.provider_id)
            reasons = self._eligibility_reasons(model, provider, request, timestamp)
            if reasons:
                rejected.append(RouteCandidateScore(
                    provider_id=model.provider_id, model_key=model.model_key,
                    disposition=CandidateDisposition.REJECTED,
                    rejection_reasons=tuple(sorted(reasons)),
                ))
                continue

            usage = request.usage_estimate
            cost = estimate_cost_micros(model.cost, usage)
            probability = self._performance.success_probability(
                task_type=request.task_type, model_key=model.model_key,
                prior=model.quality_score / 100.0, prior_weight=self._prior_weight,
            )
            probability = max(_MIN_SUCCESS_PROBABILITY, min(1.0, probability))
            expected_cost_to_success = int(round(cost / probability))

            headroom = self._budget.headroom(
                provider_id=model.provider_id, model_key=model.model_key, timestamp=timestamp
            )
            if cost > headroom.binding_remaining_micros:
                rejected.append(RouteCandidateScore(
                    provider_id=model.provider_id, model_key=model.model_key,
                    disposition=CandidateDisposition.REJECTED,
                    rejection_reasons=("budget_exceeded",),
                ))
                continue
            if request.budget_limit_micros and cost > request.budget_limit_micros:
                rejected.append(RouteCandidateScore(
                    provider_id=model.provider_id, model_key=model.model_key,
                    disposition=CandidateDisposition.REJECTED,
                    rejection_reasons=("request_budget_exceeded",),
                ))
                continue

            provider_health = self._health.provider_health(model.provider_id)
            model_health = self._health.model_health(model.model_key)
            health_status = min(
                (provider_health.best_endpoint_status(), model_health.status),
                key=lambda item: {HealthStatus.HEALTHY: 0, HealthStatus.DEGRADED: 1,
                                   HealthStatus.UNHEALTHY: 2, HealthStatus.UNKNOWN: 3}[item],
            )
            savings = cache_savings_micros(model.cost, usage)

            provisional.append({
                "model": model, "provider": provider, "cost": cost,
                "probability": probability, "expected_cost_to_success": expected_cost_to_success,
                "health_status": health_status, "cache_savings": savings,
            })

        max_ecs = max((item["expected_cost_to_success"] for item in provisional), default=0)
        eligible: list[RouteCandidateScore] = []
        for item in provisional:
            model, provider = item["model"], item["provider"]
            cost, ecs = item["cost"], item["expected_cost_to_success"]
            cost_factor = 100 if max_ecs == 0 else 100 - (ecs * 100 // max_ecs)
            cache_factor = 0 if cost == 0 else min(100, (item["cache_savings"] * 100) // max(cost, 1))
            factors = dict(
                quality_factor=model.quality_score,
                reliability_factor=int(item["probability"] * 100),
                latency_factor=_LATENCY_SCORE[model.latency_class],
                cost_factor=cost_factor,
                trust_factor=int(provider.trust_tier) * 100 // int(TrustTier.DIRECT_OFFICIAL),
                health_factor=_HEALTH_SCORE[item["health_status"]],
                cache_factor=cache_factor,
                preference_factor=100 if provider.provider_id in request.preferred_providers else 0,
            )
            weights = self._weights
            score = (
                factors["quality_factor"] * weights.quality
                + factors["reliability_factor"] * weights.reliability
                + factors["latency_factor"] * weights.latency
                + factors["cost_factor"] * weights.cost
                + factors["trust_factor"] * weights.trust
                + factors["health_factor"] * weights.health
                + factors["cache_factor"] * weights.cache
                + factors["preference_factor"] * weights.preference
            )
            eligible.append(RouteCandidateScore(
                provider_id=provider.provider_id, model_key=model.model_key,
                disposition=CandidateDisposition.ELIGIBLE,
                estimated_cost_micros=cost,
                predicted_success_probability_permille=int(item["probability"] * 1000),
                expected_cost_to_success_micros=ecs,
                score=score, **factors,
            ))

        eligible.sort(key=lambda item: (
            item.expected_cost_to_success_micros, -item.score, item.provider_id, item.model_key
        ))
        rejected.sort(key=lambda item: (item.provider_id, item.model_key))
        candidates = tuple(eligible + rejected)
        selected = eligible[0] if eligible else None
        fallback = tuple(f"{item.provider_id}/{item.model_key}" for item in eligible[1:])

        identity = {
            "request_fingerprint": request.fingerprint,
            "selected": None if selected is None else [selected.provider_id, selected.model_key],
            "candidates": [item.model_dump(mode="json") for item in candidates],
            "created_at": timestamp,
        }
        return RouteDecision(
            decision_id=f"route_decision_{digest(identity)[:24]}",
            request_id=request.request_id,
            request_fingerprint=request.fingerprint,
            selected_provider_id=selected.provider_id if selected else None,
            selected_model_key=selected.model_key if selected else None,
            estimated_cost_micros=selected.estimated_cost_micros if selected else None,
            expected_cost_to_success_micros=selected.expected_cost_to_success_micros if selected else None,
            outcome=RouteOutcome.ROUTED if selected else RouteOutcome.NO_ROUTE,
            candidates=candidates, fallback_chain=fallback, weights=self._weights, created_at=timestamp,
        )

    def _eligibility_reasons(self, model, provider, request: RouteRequest, timestamp: int) -> list[str]:
        reasons: list[str] = []
        if not is_routable(provider.lifecycle_state):
            reasons.append("provider_not_approved")
        if not data_classification_allowed(provider.trust_tier, request.data_classification):
            reasons.append("data_classification_not_eligible_for_trust_tier")
        if provider.trust_tier < request.minimum_trust_tier:
            reasons.append("trust_tier_insufficient")
        if not model.is_routable():
            reasons.append("model_not_qualified")
        if not model.capability_profile.supports_all(request.required_capabilities):
            reasons.append("required_capability_missing")
        if not model.capability_profile.fits_context(
            input_tokens=request.usage_estimate.input_tokens, output_tokens=request.usage_estimate.output_tokens
        ):
            reasons.append("context_window_insufficient")
        if model.quality_score < request.minimum_quality:
            reasons.append("quality_below_minimum")
        if model.latency_class > request.maximum_latency_class:
            reasons.append("latency_limit_exceeded")

        provider_health = self._health.provider_health(model.provider_id).best_endpoint_status()
        model_health = self._health.model_health(model.model_key).status
        if provider_health == HealthStatus.UNHEALTHY:
            reasons.append("provider_unhealthy")
        if model_health == HealthStatus.UNHEALTHY:
            reasons.append("model_unhealthy")
        return reasons
