"""Deterministic, governed selection of configured AI models.

This module is deliberately provider-agnostic and non-executing.  Registry
records contain routing metadata only; routing returns immutable evidence and
never invokes a provider or authorizes spending.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum, IntEnum
from typing import Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MODEL_ROUTING_SCHEMA_VERSION = 1


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _clean_identifier(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value):
        raise ValueError("routing identifiers must be stable lowercase identifiers")
    return value


def _clean_label(value: str) -> str:
    value = value.strip()
    lowered = value.lower()
    if not value or any(marker in lowered for marker in (
        "api_key", "api-key", "authorization", "bearer ", "password",
        "private_key", "secret=", "token=",
    )):
        raise ValueError("routing labels must not contain sensitive configuration")
    return value


class LatencyClass(IntEnum):
    INTERACTIVE = 1
    STANDARD = 2
    BATCH = 3


class TrustTier(IntEnum):
    UNTRUSTED = 0
    RESTRICTED = 1
    TRUSTED = 2
    PRIVILEGED = 3


class ProviderRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    display_name: str = Field(..., min_length=1, max_length=128)
    available: bool = True
    enabled: bool = True

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return _clean_label(value)


class ModelRecord(BaseModel):
    """Sanitized model metadata; credentials and provider configuration are forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    provider_id: str
    display_name: str = Field(..., min_length=1, max_length=128)
    capabilities: Tuple[str, ...]
    task_types: Tuple[str, ...]
    context_limit: int = Field(..., ge=1)
    input_cost_micros_per_million: int = Field(default=0, ge=0)
    output_cost_micros_per_million: int = Field(default=0, ge=0)
    estimated_cost_micros: int = Field(default=0, ge=0)
    latency_class: LatencyClass
    quality_score: int = Field(..., ge=0, le=100)
    reliability_score: int = Field(..., ge=0, le=100)
    enabled: bool = True
    available: bool = True
    trust_tier: TrustTier = TrustTier.TRUSTED

    @field_validator("model_id", "provider_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return _clean_label(value)

    @field_validator("capabilities", "task_types")
    @classmethod
    def _sets(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        clean = tuple(sorted({_clean_identifier(value) for value in values}))
        if not clean:
            raise ValueError("models require at least one capability and task type")
        return clean


class ModelRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    providers: Tuple[ProviderRecord, ...]
    models: Tuple[ModelRecord, ...]

    @model_validator(mode="after")
    def _valid_registry(self) -> "ModelRegistry":
        provider_ids = [item.provider_id for item in self.providers]
        model_ids = [item.model_id for item in self.models]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("duplicate provider identifier")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("duplicate model identifier")
        missing = sorted({item.provider_id for item in self.models} - set(provider_ids))
        if missing:
            raise ValueError(f"models reference unknown providers: {', '.join(missing)}")
        return self


class RoutingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    task_type: str
    required_capabilities: Tuple[str, ...] = ()
    minimum_quality: int = Field(default=0, ge=0, le=100)
    maximum_latency_class: LatencyClass = LatencyClass.BATCH
    budget_limit_micros: int = Field(default=0, ge=0)
    preferred_providers: Tuple[str, ...] = ()
    excluded_providers: Tuple[str, ...] = ()
    excluded_models: Tuple[str, ...] = ()
    paid_routing_requires_approval: bool = True
    minimum_trust_tier: TrustTier = TrustTier.RESTRICTED

    @field_validator("request_id", "task_type")
    @classmethod
    def _request_ids(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator(
        "required_capabilities", "preferred_providers", "excluded_providers",
        "excluded_models",
    )
    @classmethod
    def _identifier_sets(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(sorted({_clean_identifier(value) for value in values}))

    @model_validator(mode="after")
    def _consistent_request(self) -> "RoutingRequest":
        conflict = set(self.preferred_providers) & set(self.excluded_providers)
        if conflict:
            raise ValueError(
                f"providers cannot be preferred and excluded: {', '.join(sorted(conflict))}"
            )
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class RoutingWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    quality: int = Field(default=40, ge=0)
    reliability: int = Field(default=25, ge=0)
    latency: int = Field(default=15, ge=0)
    cost: int = Field(default=10, ge=0)
    preference: int = Field(default=5, ge=0)
    trust: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def _positive_total(self) -> "RoutingWeights":
        if self.total <= 0:
            raise ValueError("routing scoring weights must have a positive total")
        return self

    @property
    def total(self) -> int:
        return sum((self.quality, self.reliability, self.latency, self.cost,
                    self.preference, self.trust))


class RoutingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    weights: RoutingWeights = Field(default_factory=RoutingWeights)
    allow_paid_models: bool = True


class CandidateDisposition(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"


class CandidateScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    model_id: str
    disposition: CandidateDisposition
    rejection_reasons: Tuple[str, ...] = ()
    estimated_cost_micros: int = Field(..., ge=0)
    score: Optional[int] = Field(default=None, ge=0)
    quality_factor: Optional[int] = Field(default=None, ge=0, le=100)
    reliability_factor: Optional[int] = Field(default=None, ge=0, le=100)
    latency_factor: Optional[int] = Field(default=None, ge=0, le=100)
    cost_factor: Optional[int] = Field(default=None, ge=0, le=100)
    preference_factor: Optional[int] = Field(default=None, ge=0, le=100)
    trust_factor: Optional[int] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _consistent_disposition(self) -> "CandidateScore":
        factors = (
            self.quality_factor, self.reliability_factor, self.latency_factor,
            self.cost_factor, self.preference_factor, self.trust_factor,
        )
        if self.disposition == CandidateDisposition.ELIGIBLE:
            if self.rejection_reasons or self.score is None or any(v is None for v in factors):
                raise ValueError("eligible candidates require a score and all factors")
        elif not self.rejection_reasons or self.score is not None or any(v is not None for v in factors):
            raise ValueError("rejected candidates require reasons and no score factors")
        return self


class RoutingPolicyOutcome(str, Enum):
    FREE = "free"
    PREAPPROVED_PAID = "preapproved_paid"
    APPROVAL_REQUIRED = "approval_required"
    NO_ROUTE = "no_route"


class RoutingDecision(BaseModel):
    """Immutable and sanitized evidence for a single routing evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = MODEL_ROUTING_SCHEMA_VERSION
    decision_id: str
    request_id: str
    request_fingerprint: str = Field(..., min_length=64, max_length=64)
    selected_provider_id: Optional[str] = None
    selected_model_id: Optional[str] = None
    candidates: Tuple[CandidateScore, ...]
    estimated_cost_micros: Optional[int] = Field(default=None, ge=0)
    budget_limit_micros: int = Field(..., ge=0)
    policy_outcome: RoutingPolicyOutcome
    fallback_chain: Tuple[str, ...]
    created_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _consistent_decision(self) -> "RoutingDecision":
        if self.schema_version != MODEL_ROUTING_SCHEMA_VERSION:
            raise ValueError("unsupported model routing schema version")
        no_route = self.policy_outcome == RoutingPolicyOutcome.NO_ROUTE
        selected = self.selected_provider_id is not None and self.selected_model_id is not None
        if no_route == selected:
            raise ValueError("routing selection and policy outcome are inconsistent")
        if selected and self.estimated_cost_micros is None:
            raise ValueError("selected routes require estimated cost")
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        forbidden = (
            "prompt", "api_key", "api-key", "authorization", "bearer ",
            "password", "private_key", "secret", "token",
        )
        if any(marker in encoded for marker in forbidden):
            raise ValueError("routing evidence contains forbidden sensitive content")
        return self

    @property
    def approval_required(self) -> bool:
        return self.policy_outcome == RoutingPolicyOutcome.APPROVAL_REQUIRED


class GovernedModelRouter:
    def __init__(self, registry: ModelRegistry, policy: Optional[RoutingPolicy] = None) -> None:
        self._registry = registry
        self._policy = policy or RoutingPolicy()

    def route(self, request: RoutingRequest, *, timestamp: int) -> RoutingDecision:
        if timestamp < 0:
            raise ValueError("routing timestamp must be non-negative")
        providers = {item.provider_id: item for item in self._registry.providers}
        evaluated = tuple(
            self._evaluate(model, providers[model.provider_id], request)
            for model in sorted(self._registry.models, key=lambda item: item.model_id)
        )
        eligible = sorted(
            (item for item in evaluated if item.disposition == CandidateDisposition.ELIGIBLE),
            key=lambda item: (-int(item.score or 0), item.estimated_cost_micros,
                              item.provider_id, item.model_id),
        )
        rejected = sorted(
            (item for item in evaluated if item.disposition == CandidateDisposition.REJECTED),
            key=lambda item: (item.provider_id, item.model_id),
        )
        candidates = tuple(eligible + rejected)
        selected = eligible[0] if eligible else None
        outcome = RoutingPolicyOutcome.NO_ROUTE
        if selected is not None:
            if selected.estimated_cost_micros == 0:
                outcome = RoutingPolicyOutcome.FREE
            elif request.paid_routing_requires_approval:
                outcome = RoutingPolicyOutcome.APPROVAL_REQUIRED
            else:
                outcome = RoutingPolicyOutcome.PREAPPROVED_PAID
        fallback = tuple(f"{item.provider_id}/{item.model_id}" for item in eligible[1:])
        identity = {
            "request_fingerprint": request.fingerprint,
            "selected": None if selected is None else [selected.provider_id, selected.model_id],
            "candidates": [item.model_dump(mode="json") for item in candidates],
            "outcome": outcome.value,
            "fallback_chain": fallback,
            "created_at": timestamp,
        }
        return RoutingDecision(
            decision_id=f"routing_decision_{_digest(identity)[:24]}",
            request_id=request.request_id,
            request_fingerprint=request.fingerprint,
            selected_provider_id=selected.provider_id if selected else None,
            selected_model_id=selected.model_id if selected else None,
            candidates=candidates,
            estimated_cost_micros=selected.estimated_cost_micros if selected else None,
            budget_limit_micros=request.budget_limit_micros,
            policy_outcome=outcome,
            fallback_chain=fallback,
            created_at=timestamp,
        )

    def _evaluate(
        self, model: ModelRecord, provider: ProviderRecord, request: RoutingRequest
    ) -> CandidateScore:
        reasons = []
        if not provider.enabled:
            reasons.append("provider_disabled")
        if not provider.available:
            reasons.append("provider_unavailable")
        if not model.enabled:
            reasons.append("model_disabled")
        if not model.available:
            reasons.append("model_unavailable")
        if model.provider_id in request.excluded_providers:
            reasons.append("provider_excluded")
        if model.model_id in request.excluded_models:
            reasons.append("model_excluded")
        if request.task_type not in model.task_types:
            reasons.append("task_type_unsupported")
        if not set(request.required_capabilities).issubset(model.capabilities):
            reasons.append("required_capability_missing")
        if model.quality_score < request.minimum_quality:
            reasons.append("quality_below_minimum")
        if model.latency_class > request.maximum_latency_class:
            reasons.append("latency_limit_exceeded")
        if model.estimated_cost_micros > request.budget_limit_micros:
            reasons.append("budget_exceeded")
        if model.estimated_cost_micros > 0 and not self._policy.allow_paid_models:
            reasons.append("paid_route_policy_blocked")
        if model.trust_tier < request.minimum_trust_tier:
            reasons.append("trust_tier_insufficient")
        if reasons:
            return CandidateScore(
                provider_id=model.provider_id, model_id=model.model_id,
                disposition=CandidateDisposition.REJECTED,
                rejection_reasons=tuple(sorted(reasons)),
                estimated_cost_micros=model.estimated_cost_micros,
            )

        latency = {LatencyClass.INTERACTIVE: 100, LatencyClass.STANDARD: 60,
                   LatencyClass.BATCH: 20}[model.latency_class]
        if model.estimated_cost_micros == 0:
            cost = 100
        elif request.budget_limit_micros == 0:
            cost = 0
        else:
            cost = max(0, (request.budget_limit_micros - model.estimated_cost_micros) * 100
                       // request.budget_limit_micros)
        preference = 100 if model.provider_id in request.preferred_providers else 0
        trust = int(model.trust_tier) * 100 // int(TrustTier.PRIVILEGED)
        weights = self._policy.weights
        score = (
            model.quality_score * weights.quality
            + model.reliability_score * weights.reliability
            + latency * weights.latency
            + cost * weights.cost
            + preference * weights.preference
            + trust * weights.trust
        )
        return CandidateScore(
            provider_id=model.provider_id, model_id=model.model_id,
            disposition=CandidateDisposition.ELIGIBLE,
            estimated_cost_micros=model.estimated_cost_micros, score=score,
            quality_factor=model.quality_score,
            reliability_factor=model.reliability_score, latency_factor=latency,
            cost_factor=cost, preference_factor=preference, trust_factor=trust,
        )
