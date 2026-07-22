from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    MODEL_ROUTING_EVENT,
    CandidateDisposition,
    GovernedModelRouter,
    LatencyClass,
    ModelRecord,
    ModelRegistry,
    ModelRoutingVisibilityService,
    ProviderRecord,
    RoutingPolicy,
    RoutingPolicyOutcome,
    RoutingRequest,
    RoutingWeights,
    TrustTier,
)
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def model(
    model_id: str,
    *,
    provider_id: str = "provider-a",
    capabilities: tuple[str, ...] = ("code",),
    task_types: tuple[str, ...] = ("engineering",),
    quality: int = 90,
    reliability: int = 90,
    latency: LatencyClass = LatencyClass.INTERACTIVE,
    cost: int = 0,
    enabled: bool = True,
    available: bool = True,
    trust: TrustTier = TrustTier.TRUSTED,
) -> ModelRecord:
    return ModelRecord(
        model_id=model_id,
        provider_id=provider_id,
        display_name=model_id,
        capabilities=capabilities,
        task_types=task_types,
        context_limit=128_000,
        estimated_cost_micros=cost,
        latency_class=latency,
        quality_score=quality,
        reliability_score=reliability,
        enabled=enabled,
        available=available,
        trust_tier=trust,
    )


def registry(*models: ModelRecord, provider_available: bool = True) -> ModelRegistry:
    provider_ids = sorted({item.provider_id for item in models} or {"provider-a"})
    return ModelRegistry(
        providers=tuple(
            ProviderRecord(
                provider_id=provider_id,
                display_name=provider_id,
                available=provider_available,
            )
            for provider_id in provider_ids
        ),
        models=models,
    )


def request(**changes: object) -> RoutingRequest:
    values = {
        "request_id": "route-1",
        "task_type": "engineering",
        "required_capabilities": ("code",),
        "minimum_quality": 80,
        "maximum_latency_class": LatencyClass.STANDARD,
        "budget_limit_micros": 10_000,
        "paid_routing_requires_approval": True,
    }
    values.update(changes)
    return RoutingRequest(**values)


def route(items: ModelRegistry, **changes: object):
    return GovernedModelRouter(items).route(request(**changes), timestamp=100)


def rejected_reason(decision, model_id: str) -> tuple[str, ...]:
    return next(item for item in decision.candidates if item.model_id == model_id).rejection_reasons


def test_registry_validates_stable_unique_and_known_identifiers():
    with pytest.raises(ValidationError, match="lowercase"):
        ProviderRecord(provider_id="Not Stable", display_name="bad")
    with pytest.raises(ValidationError, match="duplicate provider"):
        ModelRegistry(
            providers=(ProviderRecord(provider_id="p", display_name="P"),) * 2,
            models=(),
        )
    with pytest.raises(ValidationError, match="unknown providers"):
        registry(model("m", provider_id="missing"), provider_available=True).model_copy(
            update={"providers": ()}
        ).model_validate(
            {"providers": (), "models": (model("m", provider_id="missing"),)}
        )
    with pytest.raises(ValidationError, match="duplicate model"):
        registry(model("m"), model("m"))


def test_request_rejects_contradictory_provider_policy_and_invalid_values():
    with pytest.raises(ValidationError, match="preferred and excluded"):
        request(preferred_providers=("provider-a",), excluded_providers=("provider-a",))
    with pytest.raises(ValidationError):
        request(budget_limit_micros=-1)


def test_capability_and_task_type_filtering_is_complete():
    decision = route(registry(model("missing", capabilities=("text",)), model("wrong-task", task_types=("chat",))))
    assert rejected_reason(decision, "missing") == ("required_capability_missing",)
    assert rejected_reason(decision, "wrong-task") == ("task_type_unsupported",)
    assert decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE


@pytest.mark.parametrize(
    ("item", "provider_available", "reason"),
    [
        (model("disabled", enabled=False), True, "model_disabled"),
        (model("unavailable", available=False), True, "model_unavailable"),
        (model("provider-down"), False, "provider_unavailable"),
        (model("untrusted", trust=TrustTier.UNTRUSTED), True, "trust_tier_insufficient"),
    ],
)
def test_disabled_unavailable_and_untrusted_models_are_rejected(item, provider_available, reason):
    decision = route(registry(item, provider_available=provider_available))
    assert reason in rejected_reason(decision, item.model_id)


def test_quality_latency_and_budget_are_hard_filters():
    items = registry(
        model("low-quality", quality=79),
        model("too-slow", latency=LatencyClass.BATCH),
        model("too-costly", cost=10_001),
    )
    decision = route(items)
    assert rejected_reason(decision, "low-quality") == ("quality_below_minimum",)
    assert rejected_reason(decision, "too-slow") == ("latency_limit_exceeded",)
    assert rejected_reason(decision, "too-costly") == ("budget_exceeded",)


def test_free_route_selection_never_requires_approval():
    decision = route(registry(model("free")))
    assert decision.selected_model_id == "free"
    assert decision.estimated_cost_micros == 0
    assert decision.policy_outcome == RoutingPolicyOutcome.FREE
    assert not decision.approval_required


def test_paid_route_is_explicitly_preapproved_when_request_policy_allows_it():
    decision = route(
        registry(model("paid", cost=5_000)),
        paid_routing_requires_approval=False,
    )
    assert decision.policy_outcome == RoutingPolicyOutcome.PREAPPROVED_PAID
    assert not decision.approval_required


def test_paid_route_returns_approval_required_without_execution():
    decision = route(registry(model("paid", cost=5_000)))
    assert decision.selected_model_id == "paid"
    assert decision.policy_outcome == RoutingPolicyOutcome.APPROVAL_REQUIRED
    assert decision.approval_required


def test_paid_models_can_be_blocked_by_global_policy():
    router = GovernedModelRouter(
        registry(model("paid", cost=1)), RoutingPolicy(allow_paid_models=False)
    )
    decision = router.route(request(), timestamp=100)
    assert decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE
    assert rejected_reason(decision, "paid") == ("paid_route_policy_blocked",)


def test_provider_and_model_exclusions_are_reported():
    items = registry(
        model("one"), model("two", provider_id="provider-b")
    )
    decision = route(
        items,
        excluded_models=("one",),
        excluded_providers=("provider-b",),
    )
    assert rejected_reason(decision, "one") == ("model_excluded",)
    assert rejected_reason(decision, "two") == ("provider_excluded",)


def test_scoring_is_deterministic_explicit_and_preference_sensitive():
    items = registry(
        model("a", provider_id="provider-a"),
        model("b", provider_id="provider-b"),
    )
    first = route(items, preferred_providers=("provider-b",))
    second = route(items, preferred_providers=("provider-b",))
    assert first == second
    assert first.selected_model_id == "b"
    selected = next(item for item in first.candidates if item.model_id == "b")
    assert selected.preference_factor == 100
    assert selected.score == 9_180
    assert RoutingWeights().total == 100


def test_tie_breaking_and_fallback_order_are_stable():
    items = registry(model("z"), model("a"), model("m", reliability=80))
    decision = route(items)
    assert decision.selected_model_id == "a"
    assert decision.fallback_chain == ("provider-a/z", "provider-a/m")


def test_no_route_retains_all_rejection_evidence_and_no_fallback():
    decision = route(registry(model("disabled", enabled=False)))
    assert decision.selected_model_id is None
    assert decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE
    assert decision.fallback_chain == ()
    assert len(decision.candidates) == 1
    assert decision.candidates[0].disposition == CandidateDisposition.REJECTED


def test_decision_identifier_is_stable_for_identical_evaluations_and_unique_per_record():
    router = GovernedModelRouter(registry(model("free")))
    first = router.route(request(), timestamp=100)
    repeated = router.route(request(), timestamp=100)
    later = router.route(request(), timestamp=200)
    other = router.route(request(request_id="route-2"), timestamp=100)
    assert first.decision_id == repeated.decision_id
    assert first.decision_id != later.decision_id
    assert first.decision_id != other.decision_id
    assert first.created_at != later.created_at


def test_registry_and_evidence_forbid_secret_fields_and_sensitive_content():
    with pytest.raises(ValidationError):
        ModelRecord(
            **model("m").model_dump(),
            api_key="forbidden",
        )
    with pytest.raises(ValidationError, match="sensitive"):
        ProviderRecord(provider_id="p", display_name="token=credential")
    decision = route(registry(model("free")))
    encoded = json.dumps(decision.model_dump(mode="json")).lower()
    for forbidden in ("prompt", "api_key", "authorization", "bearer", "password", "secret"):
        assert forbidden not in encoded


def test_mission_control_event_registration_and_visibility_formatting(tmp_path):
    decision = route(
        registry(model("primary", cost=100), model("fallback", cost=200))
    )
    service = ModelRoutingVisibilityService(
        MissionControlService(MissionControlStore(root=tmp_path / "mc"))
    )
    record = service.publish("project-1", decision)
    assert record.selected_model == "provider-a/primary"
    assert record.approval_status == "required"
    assert record.budget_disposition == "within_budget"
    assert record.fallback_available
    assert record.fallback_count == 1
    assert not record.no_route
    assert service.list_records("project-1") == (record,)

    event = TelemetryEvent(
        event_id="registered", event_type=MODEL_ROUTING_EVENT,
        project_id="project-1",
    )
    assert event.event_type == "model_routing_recorded"


def test_no_route_visibility_is_concise(tmp_path):
    decision = route(registry(model("off", enabled=False)))
    service = ModelRoutingVisibilityService(
        MissionControlService(MissionControlStore(root=tmp_path / "mc"))
    )
    record = service.publish("project-1", decision)
    assert record.selected_model is None
    assert record.approval_status == "not_applicable"
    assert record.budget_disposition == "no_route"
    assert record.no_route
    assert not record.fallback_available


def test_no_route_visibility_identifies_budget_disposition(tmp_path):
    decision = route(registry(model("costly", cost=11_000)))
    service = ModelRoutingVisibilityService(
        MissionControlService(MissionControlStore(root=tmp_path / "mc"))
    )
    record = service.publish("project-1", decision)
    assert record.no_route
    assert record.budget_disposition == "over_budget"


def test_public_exports_are_regression_safe():
    import hermes_cli.agent_roles as public

    for name in (
        "GovernedModelRouter", "ModelRegistry", "RoutingRequest",
        "RoutingDecision", "ModelRoutingVisibilityService", "MODEL_ROUTING_EVENT",
    ):
        assert name in public.__all__
        assert getattr(public, name) is not None
