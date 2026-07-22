"""Deterministic integer-only performance and candidate-plan scoring."""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PerformanceSubject(str, Enum):
    MODEL = "model"
    PROVIDER = "provider"
    AGENT = "agent"
    WORKFLOW = "workflow"


class PerformanceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_ref: str
    succeeded: bool = False
    failed: bool = False
    timed_out: bool = False
    fallback: bool = False
    recovered: bool = False
    cancelled: bool = False
    estimated_cost_micros: int = Field(default=0, ge=0)
    actual_cost_micros: int = Field(default=0, ge=0)
    input_units: int = Field(default=0, ge=0)
    output_units: int = Field(default=0, ge=0)
    latency_units: int = Field(default=0, ge=0)
    quality_outcome_score: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def _one_outcome(self) -> "PerformanceObservation":
        if sum((self.succeeded, self.failed, self.timed_out, self.cancelled)) != 1:
            raise ValueError("performance observation requires exactly one terminal outcome")
        return self


class PerformanceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    subject_type: PerformanceSubject
    subject_id: str
    evidence_references: Tuple[str, ...]
    attempt_count: int = Field(..., ge=1)
    success_count: int = Field(..., ge=0)
    failure_count: int = Field(..., ge=0)
    timeout_count: int = Field(..., ge=0)
    fallback_count: int = Field(..., ge=0)
    recovery_count: int = Field(..., ge=0)
    cancellation_count: int = Field(..., ge=0)
    estimated_cost_micros: int = Field(..., ge=0)
    actual_cost_micros: int = Field(..., ge=0)
    input_units: int = Field(..., ge=0)
    output_units: int = Field(..., ge=0)
    latency_units: int = Field(..., ge=0)
    quality_outcome_score: int = Field(..., ge=0, le=1000)
    reliability_score: int = Field(..., ge=0, le=1000)
    cost_efficiency_score: int = Field(..., ge=0, le=1000)
    latency_efficiency_score: int = Field(..., ge=0, le=1000)
    composite_performance_score: int = Field(..., ge=0, le=1000)
    confidence: int = Field(..., ge=0, le=1000)


def aggregate_performance(
    subject_type: PerformanceSubject,
    subject_id: str,
    observations: Iterable[PerformanceObservation],
) -> PerformanceRecord:
    items = tuple(sorted(observations, key=lambda item: item.evidence_ref))
    if not items:
        raise ValueError("performance aggregation requires evidence")
    count = len(items)
    successes = sum(item.succeeded for item in items)
    failures = sum(item.failed for item in items)
    timeouts = sum(item.timed_out for item in items)
    cancellations = sum(item.cancelled for item in items)
    quality = sum(item.quality_outcome_score for item in items) // count
    # Bayesian shrinkage toward a neutral score prevents one sample from being extreme.
    reliability = (500 * 5 + successes * 1000) // (5 + count)
    actual = sum(item.actual_cost_micros for item in items)
    estimated = sum(item.estimated_cost_micros for item in items)
    cost_efficiency = 1000 if estimated == actual == 0 else min(1000, estimated * 1000 // max(1, actual))
    latency = sum(item.latency_units for item in items)
    latency_efficiency = max(0, 1000 - min(1000, latency // count))
    composite = (quality * 35 + reliability * 35 + cost_efficiency * 15 + latency_efficiency * 15) // 100
    return PerformanceRecord(
        subject_type=subject_type, subject_id=subject_id,
        evidence_references=tuple(item.evidence_ref for item in items),
        attempt_count=count, success_count=successes, failure_count=failures,
        timeout_count=timeouts, fallback_count=sum(item.fallback for item in items),
        recovery_count=sum(item.recovered for item in items),
        cancellation_count=cancellations, estimated_cost_micros=estimated,
        actual_cost_micros=actual, input_units=sum(item.input_units for item in items),
        output_units=sum(item.output_units for item in items), latency_units=latency,
        quality_outcome_score=quality, reliability_score=reliability,
        cost_efficiency_score=cost_efficiency, latency_efficiency_score=latency_efficiency,
        composite_performance_score=composite, confidence=min(1000, count * 100),
    )


class OptimizationAction(str, Enum):
    KEEP_CURRENT = "keep_current"
    SELECT_ELIGIBLE_ROUTE = "select_eligible_route"
    USE_GOVERNED_FALLBACK = "use_governed_fallback"
    REDUCE_CONTEXT = "reduce_context"
    REDUCE_PARALLELISM = "reduce_parallelism"
    REORDER_LOW_RISK_WORK = "reorder_low_risk_work"
    PAUSE_TASK = "pause_task"
    INCREASE_BUDGET = "increase_budget"
    ACTIVATE_PROVIDER = "activate_provider"
    CHANGE_POLICY = "change_policy"
    HIGH_RISK_ACTION = "high_risk_action"


_AUTOMATIC_ACTIONS = frozenset({
    OptimizationAction.KEEP_CURRENT, OptimizationAction.SELECT_ELIGIBLE_ROUTE,
    OptimizationAction.USE_GOVERNED_FALLBACK, OptimizationAction.REDUCE_CONTEXT,
    OptimizationAction.REDUCE_PARALLELISM, OptimizationAction.REORDER_LOW_RISK_WORK,
    OptimizationAction.PAUSE_TASK,
})


class CandidatePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_id: str
    action: OptimizationAction
    policy_compliant: bool
    predicted_quality: int = Field(..., ge=0, le=1000)
    predicted_reliability: int = Field(..., ge=0, le=1000)
    predicted_cost_micros: int = Field(..., ge=0)
    predicted_latency: int = Field(..., ge=0)
    recovery_risk: int = Field(..., ge=0, le=1000)
    context_efficiency: int = Field(..., ge=0, le=1000)
    parallelism_efficiency: int = Field(..., ge=0, le=1000)
    evidence_confidence: int = Field(..., ge=0, le=1000)
    operator_preference: int = Field(..., ge=0, le=1000)
    reversibility: int = Field(..., ge=0, le=1000)
    requires_approval: bool = False
    tradeoffs: Tuple[str, ...] = ()

    @property
    def automatic_application_permitted(self) -> bool:
        return self.policy_compliant and not self.requires_approval and self.action in _AUTOMATIC_ACTIONS

    @property
    def score(self) -> int:
        if not self.policy_compliant:
            return -1
        cost_factor = max(0, 1000 - min(1000, self.predicted_cost_micros))
        latency_factor = max(0, 1000 - min(1000, self.predicted_latency))
        return (
            self.predicted_quality * 20 + self.predicted_reliability * 20
            + cost_factor * 10 + latency_factor * 10
            + (1000 - self.recovery_risk) * 10 + self.context_efficiency * 5
            + self.parallelism_efficiency * 5 + self.evidence_confidence * 10
            + self.operator_preference * 5 + self.reversibility * 5
        ) // 100


class CandidateSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    selected_plan: CandidatePlan | None
    eligible_plans: Tuple[CandidatePlan, ...]
    rejected_plans: Tuple[CandidatePlan, ...]
    reason_codes: Tuple[str, ...]


def select_candidate(plans: Iterable[CandidatePlan]) -> CandidateSelection:
    items = tuple(plans)
    eligible = tuple(sorted((p for p in items if p.policy_compliant), key=lambda p: (-p.score, p.predicted_cost_micros, p.plan_id)))
    rejected = tuple(sorted((p for p in items if not p.policy_compliant), key=lambda p: p.plan_id))
    selected = eligible[0] if eligible else None
    reasons = ("policy_compliant", f"score:{selected.score}", "deterministic_tie_break") if selected else ("no_policy_eligible_plan",)
    return CandidateSelection(selected_plan=selected, eligible_plans=eligible, rejected_plans=rejected, reason_codes=reasons)
