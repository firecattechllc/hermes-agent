from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    MODEL_EXECUTION_EVENT,
    ApprovalEvidence,
    BudgetAuthorization,
    DeterministicModelAdapter,
    GovernedModelExecutionService,
    GovernedModelRouter,
    LatencyClass,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
    ModelExecutionStore,
    InMemoryModelExecutionStore,
    ModelExecutionVisibilityService,
    ModelRecord,
    ModelRegistry,
    ProviderExecutionResult,
    ProviderRecord,
    ProviderUsage,
    RoutingRequest,
    TrustTier,
)
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def model(model_id: str, *, cost: int = 0, quality: int = 90) -> ModelRecord:
    return ModelRecord(
        model_id=model_id, provider_id="provider-a", display_name=model_id,
        capabilities=("code",), task_types=("engineering",), context_limit=1000,
        estimated_cost_micros=cost, latency_class=LatencyClass.INTERACTIVE,
        quality_score=quality, reliability_score=90, trust_tier=TrustTier.TRUSTED,
    )


def decision(*items: ModelRecord, approval: bool = False, budget: int = 1000):
    registry = ModelRegistry(
        providers=(ProviderRecord(provider_id="provider-a", display_name="Provider A"),),
        models=items,
    )
    request = RoutingRequest(
        request_id="route-1", task_type="engineering",
        required_capabilities=("code",), budget_limit_micros=budget,
        paid_routing_requires_approval=approval,
    )
    return GovernedModelRouter(registry).route(request, timestamp=10)


def authorization(route, *, amount=1000, revoked=False, expires=100, approval_id=None,
                  execution_id="execution-1", idempotency_key="idem-1"):
    return BudgetAuthorization(
        authorization_id="budget-1", execution_id=execution_id,
        idempotency_key=idempotency_key, routing_decision_id=route.decision_id,
        request_id=route.request_id, authorized_cost_micros=amount,
        issued_at=10, expires_at=expires, revoked=revoked, approval_id=approval_id,
    )


def approval(route, *, amount=1000, revoked=False, expires=100,
             execution_id="execution-1", idempotency_key="idem-1"):
    return ApprovalEvidence(
        approval_id="approval-1", execution_id=execution_id,
        idempotency_key=idempotency_key, routing_decision_id=route.decision_id,
        request_id=route.request_id, authorized_cost_micros=amount,
        issued_at=10, expires_at=expires, revoked=revoked,
    )


def request(route, **changes):
    values = dict(
        execution_id="execution-1", idempotency_key="idem-1",
        project_id="project-1", task_id="task-1", request_id=route.request_id,
        routing_decision=route, selected_provider_id=route.selected_provider_id,
        selected_model_id=route.selected_model_id,
        input_reference="artifact://inputs/sha256-abc", requested_at=20,
        maximum_attempts=3,
    )
    values.update(changes)
    return ModelExecutionRequest(**values)


def success(cost=0, ref="artifact://outputs/sha256-def"):
    return ProviderExecutionResult(
        output_reference=ref,
        usage=ProviderUsage(input_units=10, output_units=5, actual_cost_micros=cost),
    )


def failure(kind):
    return ProviderExecutionResult(error_classification=kind)


def service(outcomes, store=None):
    adapter = DeterministicModelAdapter("provider-a", outcomes)
    return GovernedModelExecutionService(
        (adapter,), store or InMemoryModelExecutionStore()
    ), adapter


def test_valid_free_route_executes_successfully_without_spending_approval():
    route = decision(model("free"))
    executor, adapter = service({"free": (success(),)})
    evidence = executor.execute(request(route), timestamp=20)
    assert evidence.state == ModelExecutionState.SUCCEEDED
    assert evidence.approval_disposition == "not_required"
    assert evidence.authorized_cost_micros == evidence.actual_cost_micros == 0
    assert evidence.input_units == 10 and evidence.output_units == 5
    assert evidence.lifecycle == (
        ModelExecutionState.PREPARED, ModelExecutionState.ADMITTED,
        ModelExecutionState.RUNNING, ModelExecutionState.SUCCEEDED,
    )
    assert adapter.calls == ["free"]


def test_valid_preapproved_paid_execution_is_bounded():
    route = decision(model("paid", cost=100))
    evidence = service({"paid": (success(90),)})[0].execute(
        request(route, budget_authorization=authorization(route, amount=100)), timestamp=20
    )
    assert evidence.state == ModelExecutionState.SUCCEEDED
    assert evidence.approval_disposition == "preapproved"
    assert (evidence.authorized_cost_micros, evidence.actual_cost_micros) == (100, 90)


def test_approval_required_route_blocks_then_executes_with_matching_approval():
    route = decision(model("paid", cost=100), approval=True)
    blocked_budget = authorization(
        route, amount=100, approval_id="approval-1",
        execution_id="blocked", idempotency_key="blocked",
    )
    executor, adapter = service({"paid": (success(80),)})
    blocked = executor.execute(
        request(route, execution_id="blocked", idempotency_key="blocked", budget_authorization=blocked_budget),
        timestamp=20,
    )
    assert blocked.state == ModelExecutionState.APPROVAL_REQUIRED
    assert blocked.error_classification == ModelExecutionErrorClass.APPROVAL_MISSING
    assert adapter.calls == []
    budget = authorization(
        route, amount=100, approval_id="approval-1",
        execution_id="admitted", idempotency_key="admitted",
    )
    admitted = executor.execute(
        request(route, execution_id="admitted", idempotency_key="admitted",
                budget_authorization=budget,
                approval=approval(route, execution_id="admitted", idempotency_key="admitted")),
        timestamp=20
    )
    assert admitted.state == ModelExecutionState.SUCCEEDED
    assert admitted.approval_disposition == "approved"


def test_no_route_and_mismatched_route_never_execute():
    route = decision(model("too-costly", cost=2000), budget=1000)
    executor, adapter = service({})
    blocked = executor.execute(request(route), timestamp=20)
    assert blocked.error_classification == ModelExecutionErrorClass.POLICY_BLOCKED
    assert adapter.calls == []
    with pytest.raises(ValidationError, match="does not match"):
        request(route, selected_model_id="different")


@pytest.mark.parametrize("expired,revoked", [(True, False), (False, True)])
def test_expired_or_revoked_authorization_is_rejected(expired, revoked):
    route = decision(model("paid", cost=100))
    auth = authorization(route, amount=100, expires=19 if expired else 100, revoked=revoked)
    executor, adapter = service({"paid": (success(50),)})
    evidence = executor.execute(request(route, budget_authorization=auth), timestamp=20)
    assert evidence.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
    assert adapter.calls == []


def test_invalid_expired_or_revoked_approval_is_rejected():
    route = decision(model("paid", cost=100), approval=True)
    for number, evidence in enumerate((
        approval(route, expires=19, execution_id="execution-0", idempotency_key="idem-0"),
        approval(route, revoked=True, execution_id="execution-1", idempotency_key="idem-1"),
        approval(route, execution_id="execution-2", idempotency_key="idem-2").model_copy(
            update={"routing_decision_id": "wrong"}
        ),
    )):
        budget = authorization(
            route, amount=100, approval_id="approval-1",
            execution_id=f"execution-{number}", idempotency_key=f"idem-{number}",
        )
        executor, adapter = service({"paid": (success(50),)})
        result = executor.execute(
            request(route, execution_id=f"execution-{number}", idempotency_key=f"idem-{number}",
                    budget_authorization=budget, approval=evidence), timestamp=20
        )
        assert result.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
        assert adapter.calls == []


def test_budget_authorization_and_actual_cost_are_enforced():
    route = decision(model("paid", cost=100))
    underfunded = service({"paid": (success(50),)})[0].execute(
        request(route, execution_id="under", idempotency_key="under",
                budget_authorization=authorization(
                    route, amount=99, execution_id="under", idempotency_key="under"
                )), timestamp=20
    )
    assert underfunded.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
    over = service({"paid": (success(101),)})[0].execute(
        request(route, execution_id="over", idempotency_key="over",
                budget_authorization=authorization(
                    route, amount=100, execution_id="over", idempotency_key="over"
                )), timestamp=20
    )
    assert over.state == ModelExecutionState.FAILED
    assert over.error_classification == ModelExecutionErrorClass.BUDGET_EXCEEDED
    assert over.output_reference is None


def test_timeout_and_retryability_classification_are_explicit():
    route = decision(model("only"))
    evidence = service({"only": (failure(ModelExecutionErrorClass.TIMEOUT),)})[0].execute(
        request(route), timestamp=20
    )
    assert evidence.state == ModelExecutionState.EXHAUSTED
    assert evidence.error_classification == ModelExecutionErrorClass.TIMEOUT
    assert ModelExecutionErrorClass.TIMEOUT.retryable
    assert ModelExecutionErrorClass.TIMEOUT.fallback_eligible
    assert not ModelExecutionErrorClass.POLICY_BLOCKED.retryable
    assert ModelExecutionErrorClass.PERMANENT_PROVIDER_ERROR.fallback_eligible


def test_transient_and_permanent_provider_failures_follow_fallback_order():
    route = decision(model("first", quality=95), model("second", quality=90), model("third", quality=85))
    for kind in (
        ModelExecutionErrorClass.TRANSIENT_PROVIDER_ERROR,
        ModelExecutionErrorClass.PERMANENT_PROVIDER_ERROR,
    ):
        executor, adapter = service({"first": (failure(kind),), "second": (success(),)})
        evidence = executor.execute(
            request(route, execution_id=f"exec-{kind.value}", idempotency_key=f"idem-{kind.value}"),
            timestamp=20,
        )
        assert evidence.state == ModelExecutionState.SUCCEEDED
        assert evidence.attempted_models == ("provider-a/first", "provider-a/second")
        assert evidence.fallback_progression == ("provider-a/second",)
        assert ModelExecutionState.FALLBACK_PENDING in evidence.lifecycle
        assert ModelExecutionState.FALLBACK_RUNNING in evidence.lifecycle
        assert adapter.calls == ["first", "second"]


def test_policy_errors_do_not_fallback_and_maximum_attempts_exhaust():
    route = decision(model("first", quality=95), model("second", quality=90))
    executor, adapter = service({
        "first": (failure(ModelExecutionErrorClass.POLICY_BLOCKED),),
        "second": (success(),),
    })
    evidence = executor.execute(request(route), timestamp=20)
    assert evidence.state == ModelExecutionState.FAILED
    assert adapter.calls == ["first"]

    executor, adapter = service({
        "first": (failure(ModelExecutionErrorClass.RATE_LIMITED),),
        "second": (success(),),
    })
    exhausted = executor.execute(
        request(route, execution_id="limit", idempotency_key="limit", maximum_attempts=1),
        timestamp=20,
    )
    assert exhausted.state == ModelExecutionState.EXHAUSTED
    assert adapter.calls == ["first"]


def test_fallback_exhaustion_and_cancellation_are_terminal():
    route = decision(model("first", quality=95), model("second", quality=90))
    executor, adapter = service({
        "first": (failure(ModelExecutionErrorClass.PROVIDER_UNAVAILABLE),),
        "second": (failure(ModelExecutionErrorClass.TIMEOUT),),
    })
    exhausted = executor.execute(request(route), timestamp=20)
    assert exhausted.state == ModelExecutionState.EXHAUSTED
    assert len(exhausted.attempts) == 2
    cancelled = executor.execute(
        request(route, execution_id="cancel", idempotency_key="cancel", cancelled=True),
        timestamp=20,
    )
    assert cancelled.state == ModelExecutionState.CANCELLED
    assert len(adapter.calls) == 2


def test_missing_usage_fails_closed_without_fallback():
    route = decision(model("first", quality=95), model("second", quality=90))
    malformed = ProviderExecutionResult.model_construct(output_reference="artifact://out")
    executor, adapter = service({"first": (malformed,), "second": (success(),)})
    evidence = executor.execute(request(route), timestamp=20)
    assert evidence.error_classification == ModelExecutionErrorClass.OUTPUT_VALIDATION_FAILED
    assert adapter.calls == ["first"]


def test_evidence_is_sanitized_stable_and_replay_is_idempotent(tmp_path):
    route = decision(model("free"))
    store = ModelExecutionStore(tmp_path / "executions")
    executor, adapter = service({"free": (success(),)}, store)
    item = request(route)
    first = executor.execute(item, timestamp=20)
    replay = executor.execute(item, timestamp=999)
    assert replay == first and adapter.calls == ["free"]
    assert first.evidence_id.startswith("model_execution_")
    assert ModelExecutionStore(tmp_path / "executions").get(first.execution_id) == first
    encoded = json.dumps(first.model_dump(mode="json")).lower()
    for forbidden in ("prompt", "api_key", "bearer ", "password", "secret"):
        assert forbidden not in encoded


def test_conflicting_execution_or_idempotency_reuse_is_rejected():
    route = decision(model("free"))
    executor, _ = service({"free": (success(), success())})
    executor.execute(request(route), timestamp=20)
    with pytest.raises(ValueError, match="conflicting"):
        executor.execute(request(route, input_reference="artifact://different"), timestamp=20)
    with pytest.raises(ValueError, match="conflicting"):
        executor.execute(
            request(route, execution_id="different", input_reference="artifact://different"),
            timestamp=20,
        )


def test_in_memory_store_remains_available_for_isolated_adapters():
    assert InMemoryModelExecutionStore().get("missing") is None


def test_prompt_secret_and_unknown_fields_are_rejected():
    route = decision(model("free"))
    with pytest.raises(ValidationError, match="sensitive"):
        request(route, input_reference="raw prompt: do the secret task")
    with pytest.raises(ValidationError):
        ModelExecutionRequest(**request(route).model_dump(), prompt="raw")


def test_mission_control_registration_and_visibility_formatting(tmp_path):
    route = decision(model("first", quality=95), model("second", quality=90))
    evidence = service({
        "first": (failure(ModelExecutionErrorClass.TIMEOUT),), "second": (success(),),
    })[0].execute(request(route), timestamp=20)
    visibility = ModelExecutionVisibilityService(
        MissionControlService(MissionControlStore(root=tmp_path / "mc"))
    )
    record = visibility.publish(evidence)
    assert record.selected_model == "provider-a/first"
    assert record.active_model == "provider-a/second"
    assert record.execution_state == record.terminal_outcome == "succeeded"
    assert record.approval_state == "not_required"
    assert record.attempt_count == 2 and record.fallback_state == "used"
    assert record.error_classification is None
    assert visibility.list_records("project-1") == (record,)
    assert TelemetryEvent(
        event_id="registered", event_type=MODEL_EXECUTION_EVENT, project_id="project-1"
    ).event_type == "model_execution_recorded"


def test_public_exports_are_regression_safe():
    import hermes_cli.agent_roles as public
    for name in (
        "GovernedModelExecutionService", "ModelExecutionRequest",
        "ModelExecutionEvidence", "ModelProviderAdapter",
        "DeterministicModelAdapter", "ModelExecutionVisibilityService",
        "MODEL_EXECUTION_EVENT",
    ):
        assert name in public.__all__
        assert getattr(public, name) is not None
