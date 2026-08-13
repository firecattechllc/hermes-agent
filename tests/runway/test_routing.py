from __future__ import annotations

import pytest

from runway.budget import BudgetLedger, BudgetPolicy
from runway.capabilities import Capability
from runway.classification import DataClassification
from runway.health import HealthStatus
from runway.lifecycle import LifecycleState
from runway.outcomes import TaskOutcome
from runway.pricing import CostModel, UsageEstimate
from runway.registry import LatencyClass
from runway.scoring import CandidateDisposition, RouteOutcome, RouteRequest, RouteScorer
from runway.trust import TrustTier

from .conftest import TASK_TYPE, healthy_probe, make_model, make_provider, make_registry


def _request(**overrides) -> RouteRequest:
    fields = dict(
        request_id="req-1", task_type=TASK_TYPE, data_classification=DataClassification.INTERNAL,
        usage_estimate=UsageEstimate(input_tokens=1000, output_tokens=500),
    )
    fields.update(overrides)
    return RouteRequest(**fields)


def test_cheapest_route_is_not_always_selected(generous_budget, performance_store):
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x", quality_score=5,
        cost=CostModel(snapshot_at=0, input_micros_per_million=800_000, output_micros_per_million=2_000_000),
    )
    expensive = make_model(
        model_key="premium@vendor-y", provider_id="vendor-y", quality_score=95,
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    registry = make_registry(
        providers=[make_provider("vendor-x"), make_provider("vendor-y")], models=[cheap, expensive],
    )
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    assert decision.selected_model_key == "premium@vendor-y"
    cheap_candidate = next(c for c in decision.candidates if c.model_key == "cheap@vendor-x")
    assert cheap_candidate.estimated_cost_micros < decision.estimated_cost_micros


def test_high_failure_rate_increases_effective_cost(generous_budget, performance_store):
    model = make_model(model_key="flaky@vendor-x", provider_id="vendor-x", quality_score=80)
    registry = make_registry(providers=[make_provider("vendor-x")], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    before = scorer.route(_request(), timestamp=0)
    ecs_before = before.expected_cost_to_success_micros

    for i in range(20):
        performance_store.record(TaskOutcome(
            task_type=TASK_TYPE, model_key=model.model_key, provider_id="vendor-x",
            timestamp=i, verifier_passed=False,
        ))

    after = scorer.route(_request(), timestamp=100)
    assert after.expected_cost_to_success_micros > ecs_before


def test_high_trust_beats_suspiciously_cheap_untrusted_when_required(generous_budget, performance_store):
    trusted = make_model(
        model_key="trusted@vendor-a", provider_id="vendor-a", quality_score=80,
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    suspicious = make_model(
        model_key="suspicious@vendor-b", provider_id="vendor-b", quality_score=80,
        cost=CostModel(snapshot_at=0, input_micros_per_million=1_000, output_micros_per_million=1_000),
    )
    registry = make_registry(
        providers=[
            make_provider("vendor-a", trust_tier=TrustTier.DIRECT_OFFICIAL),
            make_provider("vendor-b", trust_tier=TrustTier.VETTED_AGGREGATOR),
        ],
        models=[trusted, suspicious],
    )
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(minimum_trust_tier=TrustTier.ESTABLISHED_CLOUD), timestamp=0)
    assert decision.selected_model_key == "trusted@vendor-a"
    rejected = next(c for c in decision.candidates if c.model_key == "suspicious@vendor-b")
    assert rejected.disposition == CandidateDisposition.REJECTED
    assert "trust_tier_insufficient" in rejected.rejection_reasons


def test_hard_task_skips_cheap_model_with_poor_track_record(generous_budget, performance_store):
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x", quality_score=70,
        cost=CostModel(snapshot_at=0, input_micros_per_million=500_000, output_micros_per_million=1_500_000),
    )
    premium = make_model(
        model_key="premium@vendor-y", provider_id="vendor-y", quality_score=70,
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    registry = make_registry(
        providers=[make_provider("vendor-x"), make_provider("vendor-y")], models=[cheap, premium],
    )
    for i in range(100):
        performance_store.record(TaskOutcome(
            task_type=TASK_TYPE, model_key=cheap.model_key, provider_id="vendor-x",
            timestamp=i, verifier_passed=False,  # the cheap model never succeeds on this hard task
        ))
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=100)
    assert decision.selected_model_key == "premium@vendor-y"


def test_insufficient_context_route_rejected(generous_budget, performance_store):
    model = make_model(model_key="tight@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[make_provider("vendor-x")], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(usage_estimate=UsageEstimate(input_tokens=999_999, output_tokens=1)), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    rejected = decision.candidates[0]
    assert "context_window_insufficient" in rejected.rejection_reasons


def test_unsupported_capability_rejected(generous_budget, performance_store):
    model = make_model(model_key="textonly@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[make_provider("vendor-x")], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(required_capabilities=(Capability.VISION,)), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "required_capability_missing" in decision.candidates[0].rejection_reasons


def test_unhealthy_route_rejected(generous_budget, performance_store):
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[make_provider("vendor-x")], models=[model])
    probe = healthy_probe(registry)
    probe.set_provider_health("vendor-x", HealthStatus.UNHEALTHY)
    scorer = RouteScorer(
        registry, health_probe=probe, budget_ledger=generous_budget, performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "provider_unhealthy" in decision.candidates[0].rejection_reasons


def test_exhausted_budget_rejected(performance_store, tmp_path):
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[make_provider("vendor-x")], models=[model])
    tiny_budget = BudgetLedger.open(BudgetPolicy(per_task_max_micros=1), path=tmp_path / "tiny.db")
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=tiny_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "budget_exceeded" in decision.candidates[0].rejection_reasons


def test_cached_route_can_become_economically_preferred(generous_budget, performance_store):
    no_cache_discount = make_model(
        model_key="nodiscount@vendor-x", provider_id="vendor-x", quality_score=80,
        cost=CostModel(
            snapshot_at=0, input_micros_per_million=4_000_000, output_micros_per_million=1_000_000,
            cached_input_micros_per_million=4_000_000,
        ),
    )
    cache_friendly = make_model(
        model_key="cachefriendly@vendor-y", provider_id="vendor-y", quality_score=80,
        cost=CostModel(
            snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=1_000_000,
            cached_input_micros_per_million=500_000,
        ),
    )
    registry = make_registry(
        providers=[make_provider("vendor-x"), make_provider("vendor-y")],
        models=[no_cache_discount, cache_friendly],
    )
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    heavy_cache_usage = UsageEstimate(input_tokens=10_000, cached_input_tokens=9_000, output_tokens=200)
    decision = scorer.route(_request(usage_estimate=heavy_cache_usage), timestamp=0)
    assert decision.selected_model_key == "cachefriendly@vendor-y"


def test_same_model_via_two_providers_scored_independently(generous_budget, performance_store):
    via_a = make_model(
        model_key="shared-model@provider-a", model_family="shared-model", provider_id="provider-a",
        cost=CostModel(snapshot_at=0, input_micros_per_million=1_000_000, output_micros_per_million=2_000_000),
    )
    via_b = make_model(
        model_key="shared-model@provider-b", model_family="shared-model", provider_id="provider-b",
        cost=CostModel(snapshot_at=0, input_micros_per_million=9_000_000, output_micros_per_million=20_000_000),
    )
    registry = make_registry(
        providers=[make_provider("provider-a"), make_provider("provider-b")], models=[via_a, via_b],
    )
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    a = next(c for c in decision.candidates if c.model_key == "shared-model@provider-a")
    b = next(c for c in decision.candidates if c.model_key == "shared-model@provider-b")
    assert a.estimated_cost_micros != b.estimated_cost_micros
    assert decision.selected_model_key == "shared-model@provider-a"
