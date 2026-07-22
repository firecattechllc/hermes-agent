from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    INTELLIGENCE_EVENT_TYPES,
    BudgetAccounting,
    CandidatePlan,
    ContextItem,
    FailureSignal,
    GovernedIntelligenceEngine,
    GovernedModelRouter,
    InMemoryIntelligenceStore,
    IntelligenceRequest,
    IntelligenceState,
    IntelligenceStore,
    IntelligenceVisibilityService,
    LatencyClass,
    ModelRecord,
    ModelRegistry,
    OptimizationAction,
    PerformanceObservation,
    PerformanceSubject,
    ProviderRecord,
    RecoveryAction,
    RoutingRequest,
    SchedulingUnit,
    TrustTier,
    aggregate_performance,
    plan_budget,
    plan_context,
    recommend_recovery,
    recommend_route,
    schedule_units,
    select_candidate,
    validate_lifecycle,
)
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def routing_decision(*, cost: int = 0, available: bool = True, approval: bool = False):
    registry = ModelRegistry(
        providers=(ProviderRecord(provider_id="provider-a", display_name="Provider A", available=available),),
        models=(
            ModelRecord(model_id="model-a", provider_id="provider-a", display_name="Model A", capabilities=("code",), task_types=("engineering",), context_limit=10_000, estimated_cost_micros=cost, latency_class=LatencyClass.INTERACTIVE, quality_score=90, reliability_score=90, trust_tier=TrustTier.TRUSTED),
            ModelRecord(model_id="model-b", provider_id="provider-a", display_name="Model B", capabilities=("code",), task_types=("engineering",), context_limit=10_000, estimated_cost_micros=max(0, cost - 1), latency_class=LatencyClass.STANDARD, quality_score=89, reliability_score=95, trust_tier=TrustTier.TRUSTED),
        ),
    )
    return GovernedModelRouter(registry).route(RoutingRequest(request_id="route-28", task_type="engineering", required_capabilities=("code",), minimum_quality=80, maximum_latency_class=LatencyClass.STANDARD, budget_limit_micros=1000, paid_routing_requires_approval=approval), timestamp=10)


def request(**changes):
    values = dict(optimization_id="optimization-28", project_id="project-1", task_or_workflow_id="task-1", objective="improve governed execution", workload_class="engineering", priority=800, latency_target=100, required_capabilities=("code",), quality_target=850, reliability_target=850, context_requirements=("context://current",), memory_requirements=("memory://governed",), execution_budget_micros=1000, compute_budget=100, concurrency_limit=2, maximum_optimization_iterations=3, maximum_recovery_iterations=2, governance_policy_reference="policy://step28/default", routing_decision_references=("routing://route-28",), execution_evidence_references=("execution://history",), runtime_supervision_references=("supervision://current",), recovery_references=("recovery://history",), requested_at=20, deadline_at=100, idempotency_key="idem-28")
    values.update(changes)
    return IntelligenceRequest(**values)


def context_plan():
    return plan_context((ContextItem(reference="context://governance", units=20, confidence=1000, authoritative=True, governance_required=True), ContextItem(reference="context://stale", units=30, confidence=900, stale=True), ContextItem(reference="context://useful", units=60, confidence=800)), maximum_units=70)


def scheduling_plan():
    return schedule_units((SchedulingUnit(unit_id="a", estimated_cost_micros=10), SchedulingUnit(unit_id="b", estimated_cost_micros=10), SchedulingUnit(unit_id="c", dependencies=("a",))), concurrency_limit=2, budget_limit_micros=100)


def budget_plan(*, authorized=1000, committed=100, consumed=50, next_cost=10):
    return plan_budget(BudgetAccounting(authorized_budget_micros=authorized, committed_budget_micros=committed, consumed_budget_micros=consumed, observed_at=10, stale_after=100), timestamp=20, next_cost_micros=next_cost, fallback_cost_micros=10, recovery_reserve_micros=10, baseline_cost_micros=200)


def candidate(**changes):
    values = dict(plan_id="plan-a", action=OptimizationAction.REDUCE_CONTEXT, policy_compliant=True, predicted_quality=860, predicted_reliability=870, predicted_cost_micros=50, predicted_latency=80, recovery_risk=100, context_efficiency=900, parallelism_efficiency=700, evidence_confidence=800, operator_preference=500, reversibility=1000, tradeoffs=("optional context reduced",))
    values.update(changes)
    return CandidatePlan(**values)


def run_engine(store=None, **changes):
    values = dict(request=request(), routing_decision=routing_decision(), context_plan=context_plan(), scheduling_plan=scheduling_plan(), budget_plan=budget_plan(), recovery_plan=recommend_recovery((), recovery_iteration=0, maximum_recovery_iterations=2), candidate_plans=(candidate(),), timestamp=30)
    values.update(changes)
    return GovernedIntelligenceEngine(store or InMemoryIntelligenceStore()).optimize(**values)


def observation(ref: str, *, success=True, quality=900):
    return PerformanceObservation(evidence_ref=ref, succeeded=success, failed=not success, quality_outcome_score=quality, estimated_cost_micros=100, actual_cost_micros=80, latency_units=100)


def test_request_is_immutable_sanitized_and_has_stable_fingerprint():
    item = request()
    assert item.fingerprint == request().fingerprint
    with pytest.raises(ValidationError):
        item.objective = "changed"
    with pytest.raises(ValidationError, match="sensitive"):
        request(objective="raw_prompt: private")
    with pytest.raises(ValidationError, match="sanitized reference"):
        request(context_requirements=("raw content",))
    with pytest.raises(ValidationError, match="schema version"):
        request(schema_version=99)


def test_lifecycle_transitions_are_explicit_and_invalid_edges_fail():
    assert validate_lifecycle((IntelligenceState.REQUESTED, IntelligenceState.ANALYZING, IntelligenceState.PLANNED, IntelligenceState.ADMITTED))
    with pytest.raises(ValueError, match="invalid intelligence lifecycle transition"):
        validate_lifecycle((IntelligenceState.REQUESTED, IntelligenceState.COMPLETED))


def test_performance_aggregation_is_integer_bounded_and_shrunk():
    record = aggregate_performance(PerformanceSubject.MODEL, "model-a", (observation("execution://1"),))
    assert 500 < record.reliability_score < 1000
    assert 0 <= record.composite_performance_score <= 1000
    assert record.confidence == 100
    assert isinstance(record.actual_cost_micros, int)
    assert record == aggregate_performance(PerformanceSubject.MODEL, "model-a", (observation("execution://1"),))


def test_performance_aggregation_supports_all_subjects():
    for subject in PerformanceSubject:
        assert aggregate_performance(subject, "subject", (observation("execution://1"),)).subject_type is subject


def test_route_recommendation_preserves_step26_governance():
    route = routing_decision(cost=100, approval=True)
    recommendation = recommend_route(route, remaining_budget_micros=1000)
    assert recommendation.approval_required
    assert "step27_execution_required" in recommendation.reason_codes
    no_route = routing_decision(cost=2000)
    assert recommend_route(no_route, remaining_budget_micros=1000).blocked
    unavailable = routing_decision(available=False)
    assert recommend_route(unavailable, remaining_budget_micros=1000).selected_model_id is None


def test_context_excludes_stale_and_low_confidence_but_preserves_governance():
    plan = plan_context((ContextItem(reference="context://required", units=30, confidence=100, governance_required=True), ContextItem(reference="context://stale", units=20, confidence=900, stale=True), ContextItem(reference="context://weak", units=20, confidence=100)), maximum_units=40)
    assert "context://required" in plan.included_references
    assert plan.excluded_references == ("context://stale", "context://weak")
    assert plan.planned_units <= 40 and plan.valid


def test_context_fails_when_required_governance_exceeds_limit():
    plan = plan_context((ContextItem(reference="context://required", units=50, confidence=1000, governance_required=True),), maximum_units=40)
    assert not plan.valid


def test_independent_scheduling_parallelizes_and_dependencies_order():
    plan = scheduling_plan()
    assert plan.waves[0].unit_ids == ("a", "b")
    assert plan.waves[1].unit_ids == ("c",)
    assert plan.maximum_parallel_width == 2


def test_high_risk_conflicts_and_approval_gates_never_parallelize():
    plan = schedule_units((SchedulingUnit(unit_id="approve", approval_gated=True), SchedulingUnit(unit_id="risk", high_risk=True), SchedulingUnit(unit_id="safe"), SchedulingUnit(unit_id="conflict-a", conflict_domains=("repo",)), SchedulingUnit(unit_id="conflict-b", conflict_domains=("repo",))), concurrency_limit=5, budget_limit_micros=100)
    assert "approve" in plan.blocked_unit_ids
    assert all(len(wave.unit_ids) == 1 for wave in plan.waves if "risk" in wave.unit_ids)
    assert not any({"conflict-a", "conflict-b"} <= set(wave.unit_ids) for wave in plan.waves)


def test_budget_is_integer_only_fail_closed_and_never_increases_authority():
    plan = budget_plan(authorized=100, committed=80, consumed=70, next_cost=30)
    assert plan.remaining_budget_micros == 20
    assert plan.budget_pressure and plan.approval_required_for_increase
    assert plan.authorized_budget_micros == 100
    with pytest.raises(ValueError, match="non-negative integers"):
        plan_budget(BudgetAccounting(authorized_budget_micros=100, committed_budget_micros=0, consumed_budget_micros=0, observed_at=0, stale_after=100), timestamp=1, next_cost_micros=1.5, fallback_cost_micros=0, recovery_reserve_micros=0)
    with pytest.raises(ValidationError, match="contradictory"):
        BudgetAccounting(authorized_budget_micros=100, committed_budget_micros=50, consumed_budget_micros=60, observed_at=0, stale_after=100)
    with pytest.raises(ValueError, match="stale"):
        plan_budget(BudgetAccounting(authorized_budget_micros=100, committed_budget_micros=0, consumed_budget_micros=0, observed_at=0, stale_after=1), timestamp=2, next_cost_micros=0, fallback_cost_micros=0, recovery_reserve_micros=0)


def test_recovery_is_bounded_and_policy_failures_do_not_retry():
    timeout = recommend_recovery((FailureSignal.REPEATED_TIMEOUT,), recovery_iteration=0, maximum_recovery_iterations=2)
    assert RecoveryAction.USE_STEP26_FALLBACK in timeout.actions
    assert RecoveryAction.QUARANTINE_PROVIDER in timeout.actions
    policy = recommend_recovery((FailureSignal.POLICY_REJECTION,), recovery_iteration=0, maximum_recovery_iterations=2)
    assert policy.actions == (RecoveryAction.STOP_EXECUTION, RecoveryAction.ESCALATE_OPERATOR)
    exhausted = recommend_recovery((FailureSignal.STALE_EXECUTION,), recovery_iteration=2, maximum_recovery_iterations=2)
    assert exhausted.reason_codes == ("recovery_loop_limit",)


def test_candidate_policy_dominates_score_and_ties_are_stable():
    forbidden = candidate(plan_id="fast", action=OptimizationAction.CHANGE_POLICY, policy_compliant=False, predicted_quality=1000, predicted_cost_micros=0)
    safe_b = candidate(plan_id="b")
    safe_a = candidate(plan_id="a")
    selection = select_candidate((forbidden, safe_b, safe_a))
    assert selection.selected_plan.plan_id == "a"
    assert selection.rejected_plans == (forbidden,)


@pytest.mark.parametrize("action", [OptimizationAction.INCREASE_BUDGET, OptimizationAction.ACTIVATE_PROVIDER, OptimizationAction.CHANGE_POLICY, OptimizationAction.HIGH_RISK_ACTION])
def test_high_authority_actions_cannot_be_automatic(action):
    assert not candidate(action=action).automatic_application_permitted


def test_engine_produces_stable_sanitized_evidence_and_simulates_only():
    evidence = run_engine()
    assert evidence.lifecycle_state is IntelligenceState.COMPLETED
    assert evidence.automatic_application_permitted
    assert evidence.application_result == "simulated"
    assert evidence.evidence_id.startswith("intelligence_evidence_")
    assert "prompt" not in json.dumps(evidence.model_dump(mode="json")).lower()


def test_approval_and_no_route_remain_non_executing():
    approval = run_engine(routing_decision=routing_decision(cost=100, approval=True))
    assert approval.lifecycle_state is IntelligenceState.APPROVAL_REQUIRED
    assert not approval.automatic_application_permitted
    blocked = run_engine(request=request(optimization_id="blocked", idempotency_key="blocked"), routing_decision=routing_decision(cost=2000))
    assert blocked.lifecycle_state is IntelligenceState.BLOCKED


def test_optimization_loop_limit_blocks_selection():
    evidence = run_engine(optimization_iteration=4)
    assert evidence.lifecycle_state is IntelligenceState.BLOCKED
    assert "optimization_loop_limit" in evidence.reason_codes


def test_memory_and_durable_stores_are_idempotent_and_reject_conflicts(tmp_path):
    memory = InMemoryIntelligenceStore()
    first = run_engine(memory)
    assert run_engine(memory) == first
    with pytest.raises(ValueError, match="conflicting intelligence replay"):
        run_engine(memory, request=request(objective="different"))
    durable = IntelligenceStore(tmp_path)
    saved = run_engine(durable)
    assert IntelligenceStore(tmp_path).get(saved.optimization_id) == saved
    path = durable.journal_path
    path.write_text(path.read_text().replace(saved.optimization_id, "corrupted", 1))
    with pytest.raises(ValueError, match="corrupt intelligence journal"):
        IntelligenceStore(tmp_path).list()


def test_mission_control_events_registered_sanitized_and_idempotent(tmp_path):
    evidence = run_engine()
    service = IntelligenceVisibilityService(MissionControlService(MissionControlStore(tmp_path)))
    first = service.publish(evidence)
    second = service.publish(evidence)
    assert first == second
    assert first.optimization_id == evidence.optimization_id
    assert first.parallel_width == 2
    assert len(INTELLIGENCE_EVENT_TYPES) == 7
    for event_type in INTELLIGENCE_EVENT_TYPES:
        TelemetryEvent(event_id=f"event-{event_type}", event_type=event_type, project_id="project-1", timestamp=1)
