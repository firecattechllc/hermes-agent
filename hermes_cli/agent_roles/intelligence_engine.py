"""Governed Step 28 intelligence and efficiency coordination.

The engine recommends and simulates bounded optimizations. It never invokes a
provider, creates authority, changes policy, or performs an external action.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .intelligence_context import ContextPlan
from .intelligence_scheduling import SchedulingPlan
from .intelligence_scoring import CandidatePlan, CandidateSelection, PerformanceRecord, select_candidate
from .model_routing import CandidateDisposition, RoutingDecision, RoutingPolicyOutcome, TrustTier


INTELLIGENCE_SCHEMA_VERSION = 1
_FORBIDDEN = ("raw_prompt", "prompt:", "api_key", "api-key", "authorization:", "bearer ", "password", "private_key", "private key", "secret=", "token=")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _safe(value: str, field: str, maximum: int = 512) -> str:
    value = value.strip()
    if not value or len(value) > maximum or any(marker in value.lower() for marker in _FORBIDDEN):
        raise ValueError(f"{field} is blank, oversized, or sensitive")
    return value


def _ref(value: str, field: str) -> str:
    value = _safe(value, field)
    if "://" not in value:
        raise ValueError(f"{field} must be a sanitized reference")
    return value


class IntelligenceState(str, Enum):
    REQUESTED = "requested"
    ANALYZING = "analyzing"
    PLANNED = "planned"
    APPROVAL_REQUIRED = "approval_required"
    ADMITTED = "admitted"
    OPTIMIZING = "optimizing"
    EXECUTING = "executing"
    OBSERVING = "observing"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    FAILED = "failed"


_TRANSITIONS = {
    IntelligenceState.REQUESTED: {IntelligenceState.ANALYZING, IntelligenceState.CANCELLED, IntelligenceState.BLOCKED},
    IntelligenceState.ANALYZING: {IntelligenceState.PLANNED, IntelligenceState.BLOCKED, IntelligenceState.FAILED},
    IntelligenceState.PLANNED: {IntelligenceState.APPROVAL_REQUIRED, IntelligenceState.ADMITTED, IntelligenceState.BLOCKED},
    IntelligenceState.APPROVAL_REQUIRED: {IntelligenceState.ADMITTED, IntelligenceState.CANCELLED, IntelligenceState.BLOCKED},
    IntelligenceState.ADMITTED: {IntelligenceState.OPTIMIZING, IntelligenceState.CANCELLED},
    IntelligenceState.OPTIMIZING: {IntelligenceState.EXECUTING, IntelligenceState.OBSERVING, IntelligenceState.EXHAUSTED, IntelligenceState.FAILED},
    IntelligenceState.EXECUTING: {IntelligenceState.OBSERVING, IntelligenceState.RECOVERING, IntelligenceState.PARTIALLY_COMPLETED, IntelligenceState.COMPLETED, IntelligenceState.FAILED},
    IntelligenceState.OBSERVING: {IntelligenceState.OPTIMIZING, IntelligenceState.RECOVERING, IntelligenceState.PARTIALLY_COMPLETED, IntelligenceState.COMPLETED, IntelligenceState.EXHAUSTED},
    IntelligenceState.RECOVERING: {IntelligenceState.OBSERVING, IntelligenceState.EXHAUSTED, IntelligenceState.BLOCKED, IntelligenceState.FAILED},
}


def validate_lifecycle(states: Tuple[IntelligenceState, ...]) -> Tuple[IntelligenceState, ...]:
    if not states or states[0] is not IntelligenceState.REQUESTED:
        raise ValueError("intelligence lifecycle must begin requested")
    for current, following in zip(states, states[1:]):
        if following not in _TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid intelligence lifecycle transition: {current.value} -> {following.value}")
    return states


class IntelligenceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = INTELLIGENCE_SCHEMA_VERSION
    optimization_id: str
    project_id: str
    task_or_workflow_id: str
    objective: str
    workload_class: str
    priority: int = Field(..., ge=0, le=1000)
    latency_target: int = Field(..., ge=0)
    required_capabilities: Tuple[str, ...] = ()
    quality_target: int = Field(..., ge=0, le=1000)
    reliability_target: int = Field(..., ge=0, le=1000)
    context_requirements: Tuple[str, ...] = ()
    memory_requirements: Tuple[str, ...] = ()
    execution_budget_micros: int = Field(..., ge=0)
    compute_budget: int = Field(..., ge=0)
    concurrency_limit: int = Field(..., ge=1)
    maximum_optimization_iterations: int = Field(..., ge=1, le=100)
    maximum_recovery_iterations: int = Field(default=3, ge=0, le=100)
    governance_policy_reference: str
    routing_decision_references: Tuple[str, ...] = ()
    execution_evidence_references: Tuple[str, ...] = ()
    runtime_supervision_references: Tuple[str, ...] = ()
    recovery_references: Tuple[str, ...] = ()
    requested_at: int = Field(..., ge=0)
    deadline_at: Optional[int] = Field(default=None, ge=0)
    idempotency_key: str

    @field_validator("optimization_id", "project_id", "task_or_workflow_id", "objective", "workload_class", "idempotency_key")
    @classmethod
    def _safe_fields(cls, value: str, info) -> str:
        return _safe(value, info.field_name)

    @field_validator("governance_policy_reference")
    @classmethod
    def _policy_ref(cls, value: str) -> str:
        return _ref(value, "governance_policy_reference")

    @field_validator("context_requirements", "memory_requirements", "routing_decision_references", "execution_evidence_references", "runtime_supervision_references", "recovery_references")
    @classmethod
    def _references(cls, values: Tuple[str, ...], info) -> Tuple[str, ...]:
        return tuple(sorted({_ref(value, info.field_name) for value in values}))

    @model_validator(mode="after")
    def _consistent(self) -> "IntelligenceRequest":
        if self.schema_version != INTELLIGENCE_SCHEMA_VERSION:
            raise ValueError("unsupported intelligence schema version")
        if self.deadline_at is not None and self.deadline_at < self.requested_at:
            raise ValueError("intelligence deadline predates request")
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in encoded for marker in _FORBIDDEN):
            raise ValueError("intelligence request contains forbidden sensitive content")
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class BudgetAccounting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    authorized_budget_micros: int = Field(..., ge=0)
    committed_budget_micros: int = Field(..., ge=0)
    consumed_budget_micros: int = Field(..., ge=0)
    observed_at: int = Field(..., ge=0)
    stale_after: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "BudgetAccounting":
        if self.consumed_budget_micros > self.committed_budget_micros or self.committed_budget_micros > self.authorized_budget_micros:
            raise ValueError("contradictory budget accounting")
        return self


class BudgetPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    authorized_budget_micros: int = Field(..., ge=0)
    committed_budget_micros: int = Field(..., ge=0)
    consumed_budget_micros: int = Field(..., ge=0)
    remaining_budget_micros: int = Field(..., ge=0)
    estimated_next_action_cost_micros: int = Field(..., ge=0)
    worst_case_fallback_cost_micros: int = Field(..., ge=0)
    reserved_recovery_budget_micros: int = Field(..., ge=0)
    projected_total_cost_micros: int = Field(..., ge=0)
    estimated_savings_micros: int = Field(..., ge=0)
    budget_pressure: bool
    exhausted: bool
    approval_required_for_increase: bool
    reason_codes: Tuple[str, ...]


def plan_budget(accounting: BudgetAccounting, *, timestamp: int, next_cost_micros: int, fallback_cost_micros: int, recovery_reserve_micros: int, baseline_cost_micros: int = 0) -> BudgetPlan:
    values = (timestamp, next_cost_micros, fallback_cost_micros, recovery_reserve_micros, baseline_cost_micros)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("budget governance requires non-negative integers")
    if timestamp > accounting.stale_after or timestamp < accounting.observed_at:
        raise ValueError("budget accounting is missing or stale")
    remaining = accounting.authorized_budget_micros - accounting.committed_budget_micros
    projected = accounting.committed_budget_micros + next_cost_micros + fallback_cost_micros + recovery_reserve_micros
    pressure = projected > accounting.authorized_budget_micros
    return BudgetPlan(authorized_budget_micros=accounting.authorized_budget_micros, committed_budget_micros=accounting.committed_budget_micros, consumed_budget_micros=accounting.consumed_budget_micros, remaining_budget_micros=remaining, estimated_next_action_cost_micros=next_cost_micros, worst_case_fallback_cost_micros=fallback_cost_micros, reserved_recovery_budget_micros=recovery_reserve_micros, projected_total_cost_micros=projected, estimated_savings_micros=max(0, baseline_cost_micros - projected), budget_pressure=pressure, exhausted=remaining == 0 or next_cost_micros > remaining, approval_required_for_increase=pressure, reason_codes=("budget_pressure", "budget_increase_requires_approval") if pressure else ("within_authorized_budget",))


class RouteRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    routing_decision_id: str
    selected_provider_id: Optional[str]
    selected_model_id: Optional[str]
    fallback_reference: Optional[str]
    approval_required: bool
    blocked: bool
    reason_codes: Tuple[str, ...]


def recommend_route(decision: RoutingDecision, *, performance: Tuple[PerformanceRecord, ...] = (), remaining_budget_micros: int, minimum_trust_tier: TrustTier = TrustTier.RESTRICTED) -> RouteRecommendation:
    if remaining_budget_micros < 0:
        raise ValueError("remaining budget cannot be negative")
    if decision.policy_outcome is RoutingPolicyOutcome.NO_ROUTE:
        return RouteRecommendation(routing_decision_id=decision.decision_id, selected_provider_id=None, selected_model_id=None, fallback_reference=None, approval_required=False, blocked=True, reason_codes=("step26_no_route", "execution_prohibited"))
    perf = {item.subject_id: item for item in performance}
    eligible = [item for item in decision.candidates if item.disposition is CandidateDisposition.ELIGIBLE and item.estimated_cost_micros <= remaining_budget_micros and (item.trust_factor or 0) >= int(minimum_trust_tier) * 25]
    eligible.sort(key=lambda item: (-(perf.get(item.model_id).composite_performance_score if item.model_id in perf else int(item.score or 0)), item.estimated_cost_micros, item.provider_id, item.model_id))
    if not eligible:
        return RouteRecommendation(routing_decision_id=decision.decision_id, selected_provider_id=None, selected_model_id=None, fallback_reference=None, approval_required=False, blocked=True, reason_codes=("no_step26_eligible_route_within_budget",))
    selected = eligible[0]
    fallback = next((ref for ref in decision.fallback_chain if ref.endswith(f"/{selected.model_id}") is False), None)
    return RouteRecommendation(routing_decision_id=decision.decision_id, selected_provider_id=selected.provider_id, selected_model_id=selected.model_id, fallback_reference=fallback, approval_required=decision.policy_outcome is RoutingPolicyOutcome.APPROVAL_REQUIRED, blocked=False, reason_codes=("step26_eligible", "integer_performance_rank", "step27_execution_required"))


class FailureSignal(str, Enum):
    REPEATED_TIMEOUT = "repeated_provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EXCESSIVE_FALLBACK = "excessive_fallback"
    BUDGET_PRESSURE = "budget_pressure"
    REPEATED_WORKFLOW_FAILURE = "repeated_workflow_failure"
    DEGRADED_RELIABILITY = "degraded_reliability"
    STALE_EXECUTION = "stale_execution"
    IDEMPOTENCY_CONFLICT = "conflicting_idempotency"
    MISSING_USAGE = "missing_usage_data"
    RECOVERY_LOOP_RISK = "recovery_loop_risk"
    REPEATED_AGENT_FAILURE = "repeated_agent_failure"
    RESOURCE_SATURATION = "resource_saturation"
    INVALID_CONTEXT = "invalid_context_plan"
    POLICY_REJECTION = "policy_rejection"


class RecoveryAction(str, Enum):
    RETRY_SAME_ROUTE = "retry_same_route"
    USE_STEP26_FALLBACK = "use_step26_fallback"
    REROUTE_STEP26 = "reroute_through_step26"
    REDUCE_PARALLELISM = "reduce_parallelism"
    REDUCE_CONTEXT = "reduce_context"
    PAUSE_TASK = "pause_task"
    REQUEST_APPROVAL = "request_approval"
    QUARANTINE_PROVIDER = "quarantine_provider"
    QUARANTINE_AGENT_STRATEGY = "quarantine_agent_strategy"
    INVOKE_RUNTIME_RECOVERY = "invoke_existing_runtime_recovery"
    ESCALATE_OPERATOR = "escalate_to_operator"
    STOP_EXECUTION = "stop_execution"


class RecoveryPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    signals: Tuple[FailureSignal, ...]
    actions: Tuple[RecoveryAction, ...]
    recovery_iteration: int = Field(..., ge=0)
    automatic_application_permitted: bool
    approval_required: bool
    reason_codes: Tuple[str, ...]


def recommend_recovery(signals: Iterable[FailureSignal], *, recovery_iteration: int, maximum_recovery_iterations: int) -> RecoveryPlan:
    ordered = tuple(sorted(set(signals), key=lambda item: item.value))
    if recovery_iteration < 0 or maximum_recovery_iterations < 0:
        raise ValueError("invalid recovery loop counters")
    if recovery_iteration >= maximum_recovery_iterations:
        return RecoveryPlan(signals=ordered, actions=(RecoveryAction.STOP_EXECUTION, RecoveryAction.ESCALATE_OPERATOR), recovery_iteration=recovery_iteration, automatic_application_permitted=False, approval_required=True, reason_codes=("recovery_loop_limit",))
    if FailureSignal.POLICY_REJECTION in ordered or FailureSignal.IDEMPOTENCY_CONFLICT in ordered:
        return RecoveryPlan(signals=ordered, actions=(RecoveryAction.STOP_EXECUTION, RecoveryAction.ESCALATE_OPERATOR), recovery_iteration=recovery_iteration, automatic_application_permitted=False, approval_required=True, reason_codes=("non_retryable_governance_failure",))
    actions = []
    if FailureSignal.REPEATED_TIMEOUT in ordered:
        actions.extend((RecoveryAction.USE_STEP26_FALLBACK, RecoveryAction.QUARANTINE_PROVIDER))
    if FailureSignal.PROVIDER_UNAVAILABLE in ordered:
        actions.extend((RecoveryAction.REROUTE_STEP26, RecoveryAction.QUARANTINE_PROVIDER))
    if FailureSignal.STALE_EXECUTION in ordered:
        actions.append(RecoveryAction.INVOKE_RUNTIME_RECOVERY)
    if FailureSignal.BUDGET_PRESSURE in ordered:
        actions.extend((RecoveryAction.REDUCE_CONTEXT, RecoveryAction.REDUCE_PARALLELISM, RecoveryAction.PAUSE_TASK))
    if FailureSignal.REPEATED_AGENT_FAILURE in ordered or FailureSignal.REPEATED_WORKFLOW_FAILURE in ordered:
        actions.extend((RecoveryAction.QUARANTINE_AGENT_STRATEGY, RecoveryAction.ESCALATE_OPERATOR))
    if not actions:
        actions.append(RecoveryAction.RETRY_SAME_ROUTE)
    unique = tuple(dict.fromkeys(actions))
    approval = any(action in {RecoveryAction.QUARANTINE_PROVIDER, RecoveryAction.QUARANTINE_AGENT_STRATEGY, RecoveryAction.INVOKE_RUNTIME_RECOVERY, RecoveryAction.ESCALATE_OPERATOR} for action in unique)
    return RecoveryPlan(signals=ordered, actions=unique, recovery_iteration=recovery_iteration, automatic_application_permitted=not approval, approval_required=approval, reason_codes=("governed_recovery_recommendation", "admission_still_required"))


class IntelligenceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = INTELLIGENCE_SCHEMA_VERSION
    optimization_id: str
    idempotency_key: str
    request_fingerprint: str = Field(..., min_length=64, max_length=64)
    project_id: str
    task_or_workflow_id: str
    objective: str
    lifecycle_state: IntelligenceState
    lifecycle: Tuple[IntelligenceState, ...]
    observed_evidence_references: Tuple[str, ...]
    baseline_metrics: Tuple[PerformanceRecord, ...]
    candidate_plans: Tuple[CandidatePlan, ...]
    rejected_plans: Tuple[CandidatePlan, ...]
    selected_plan: Optional[CandidatePlan]
    reason_codes: Tuple[str, ...]
    model_provider_scoring_summaries: Tuple[PerformanceRecord, ...]
    agent_workflow_scoring_summaries: Tuple[PerformanceRecord, ...]
    context_plan: ContextPlan
    scheduling_plan: SchedulingPlan
    budget_plan: BudgetPlan
    recovery_plan: RecoveryPlan
    route_recommendation: RouteRecommendation
    expected_quality_impact: int = Field(..., ge=-1000, le=1000)
    expected_reliability_impact: int = Field(..., ge=-1000, le=1000)
    estimated_cost_impact_micros: int
    estimated_latency_impact: int
    confidence: int = Field(..., ge=0, le=1000)
    policy_disposition: str
    approval_requirements: Tuple[str, ...]
    automatic_application_permitted: bool
    application_result: str
    created_at: int = Field(..., ge=0)
    completed_at: int = Field(..., ge=0)
    evidence_id: str

    @model_validator(mode="after")
    def _consistent(self) -> "IntelligenceEvidence":
        if self.schema_version != INTELLIGENCE_SCHEMA_VERSION:
            raise ValueError("unsupported intelligence schema version")
        validate_lifecycle(self.lifecycle)
        if self.lifecycle[-1] is not self.lifecycle_state:
            raise ValueError("intelligence lifecycle terminal mismatch")
        if self.automatic_application_permitted and (self.selected_plan is None or not self.selected_plan.automatic_application_permitted):
            raise ValueError("automatic application exceeds policy")
        identity = self.model_dump(mode="json", exclude={"evidence_id"})
        expected = f"intelligence_evidence_{_digest(identity)[:24]}"
        if self.evidence_id != expected:
            raise ValueError("intelligence evidence identity mismatch")
        encoded = json.dumps(identity, sort_keys=True).lower()
        if any(marker in encoded for marker in _FORBIDDEN):
            raise ValueError("intelligence evidence contains forbidden sensitive content")
        return self


class IntelligenceStoreProtocol(Protocol):
    def get(self, optimization_id: str) -> Optional[IntelligenceEvidence]: ...
    def find_by_idempotency_key(self, key: str) -> Optional[IntelligenceEvidence]: ...
    def save(self, evidence: IntelligenceEvidence) -> IntelligenceEvidence: ...


class GovernedIntelligenceEngine:
    def __init__(self, store: IntelligenceStoreProtocol) -> None:
        self._store = store

    def optimize(self, request: IntelligenceRequest, *, routing_decision: RoutingDecision, context_plan: ContextPlan, scheduling_plan: SchedulingPlan, budget_plan: BudgetPlan, recovery_plan: RecoveryPlan, candidate_plans: Tuple[CandidatePlan, ...], performance: Tuple[PerformanceRecord, ...] = (), optimization_iteration: int = 1, timestamp: int) -> IntelligenceEvidence:
        prior = self._store.get(request.optimization_id)
        keyed = self._store.find_by_idempotency_key(request.idempotency_key)
        if prior or keyed:
            current = prior or keyed
            if current.request_fingerprint == request.fingerprint:
                return current
            raise ValueError("conflicting intelligence replay")
        if timestamp < request.requested_at:
            raise ValueError("optimization timestamp predates request")
        if optimization_iteration > request.maximum_optimization_iterations:
            selection = CandidateSelection(selected_plan=None, eligible_plans=(), rejected_plans=candidate_plans, reason_codes=("optimization_loop_limit",))
        else:
            selection = select_candidate(candidate_plans)
        route = recommend_route(routing_decision, performance=performance, remaining_budget_micros=budget_plan.remaining_budget_micros)
        blocked = route.blocked or not context_plan.valid or selection.selected_plan is None or budget_plan.exhausted
        approval = bool(selection.selected_plan and selection.selected_plan.requires_approval) or route.approval_required or budget_plan.approval_required_for_increase or recovery_plan.approval_required
        terminal = IntelligenceState.BLOCKED if blocked else (IntelligenceState.APPROVAL_REQUIRED if approval else IntelligenceState.COMPLETED)
        lifecycle = (IntelligenceState.REQUESTED, IntelligenceState.ANALYZING, IntelligenceState.PLANNED, terminal) if terminal in {IntelligenceState.BLOCKED, IntelligenceState.APPROVAL_REQUIRED} else (IntelligenceState.REQUESTED, IntelligenceState.ANALYZING, IntelligenceState.PLANNED, IntelligenceState.ADMITTED, IntelligenceState.OPTIMIZING, IntelligenceState.OBSERVING, IntelligenceState.COMPLETED)
        selected = selection.selected_plan
        automatic = bool(selected and selected.automatic_application_permitted and not blocked and not approval)
        approvals = tuple(code for condition, code in ((route.approval_required, "step26_route_approval"), (budget_plan.approval_required_for_increase, "budget_increase"), (recovery_plan.approval_required, "governed_recovery"), (bool(selected and selected.requires_approval), "candidate_plan")) if condition)
        values = dict(schema_version=INTELLIGENCE_SCHEMA_VERSION, optimization_id=request.optimization_id, idempotency_key=request.idempotency_key, request_fingerprint=request.fingerprint, project_id=request.project_id, task_or_workflow_id=request.task_or_workflow_id, objective=request.objective, lifecycle_state=terminal, lifecycle=lifecycle, observed_evidence_references=tuple(sorted(set(request.routing_decision_references + request.execution_evidence_references + request.runtime_supervision_references + request.recovery_references))), baseline_metrics=performance, candidate_plans=tuple(sorted(candidate_plans, key=lambda item: item.plan_id)), rejected_plans=selection.rejected_plans, selected_plan=selected, reason_codes=selection.reason_codes + route.reason_codes + budget_plan.reason_codes + recovery_plan.reason_codes, model_provider_scoring_summaries=tuple(item for item in performance if item.subject_type.value in {"model", "provider"}), agent_workflow_scoring_summaries=tuple(item for item in performance if item.subject_type.value in {"agent", "workflow"}), context_plan=context_plan, scheduling_plan=scheduling_plan, budget_plan=budget_plan, recovery_plan=recovery_plan, route_recommendation=route, expected_quality_impact=0 if selected is None else selected.predicted_quality - request.quality_target, expected_reliability_impact=0 if selected is None else selected.predicted_reliability - request.reliability_target, estimated_cost_impact_micros=0 if selected is None else selected.predicted_cost_micros - budget_plan.committed_budget_micros, estimated_latency_impact=0 if selected is None else selected.predicted_latency - request.latency_target, confidence=0 if selected is None else selected.evidence_confidence, policy_disposition="blocked" if blocked else ("approval_required" if approval else "permitted"), approval_requirements=approvals, automatic_application_permitted=automatic, application_result="simulated" if automatic else "not_applied", created_at=request.requested_at, completed_at=timestamp)
        draft = IntelligenceEvidence.model_construct(**values, evidence_id="")
        identity = draft.model_dump(mode="json", exclude={"evidence_id"})
        evidence = IntelligenceEvidence(
            **values,
            evidence_id=f"intelligence_evidence_{_digest(identity)[:24]}",
        )
        return self._store.save(evidence)
