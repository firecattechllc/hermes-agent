from __future__ import annotations

import pytest

from runway.classification import DataClassification
from runway.discovery import DiscoveryCandidate, candidate_to_provider
from runway.lifecycle import InvalidLifecycleTransition, LifecycleState, transition
from runway.pricing import UsageEstimate
from runway.qualification import QualificationError, QualificationHistoryStore, QualificationService
from runway.scoring import RouteOutcome, RouteRequest, RouteScorer
from runway.trust import TrustTier, data_classification_allowed

from .conftest import TASK_TYPE, healthy_probe, make_model, make_provider, make_registry


def _request(**overrides) -> RouteRequest:
    fields = dict(
        request_id="req-1", task_type=TASK_TYPE, data_classification=DataClassification.INTERNAL,
        usage_estimate=UsageEstimate(input_tokens=1000, output_tokens=500),
    )
    fields.update(overrides)
    return RouteRequest(**fields)


def test_discovered_provider_is_not_routable(generous_budget, performance_store):
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "provider_not_approved" in decision.candidates[0].rejection_reasons


def test_tier_d_provider_cannot_receive_restricted_data(generous_budget, performance_store):
    provider = make_provider(
        "relay-x", trust_tier=TrustTier.EXPERIMENTAL_UNKNOWN, lifecycle_state=LifecycleState.APPROVED,
    )
    model = make_model(model_key="model@relay-x", provider_id="relay-x")
    registry = make_registry(providers=[provider], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    restricted = scorer.route(_request(data_classification=DataClassification.RESTRICTED), timestamp=0)
    assert restricted.outcome == RouteOutcome.NO_ROUTE
    assert "data_classification_not_eligible_for_trust_tier" in restricted.candidates[0].rejection_reasons

    synthetic = scorer.route(_request(data_classification=DataClassification.SYNTHETIC), timestamp=0)
    assert synthetic.outcome == RouteOutcome.ROUTED


def test_quarantine_immediately_blocks_routing(generous_budget, performance_store):
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.QUARANTINED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])
    scorer = RouteScorer(
        registry, health_probe=healthy_probe(registry), budget_ledger=generous_budget,
        performance_store=performance_store,
    )
    decision = scorer.route(_request(), timestamp=0)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "provider_not_approved" in decision.candidates[0].rejection_reasons


def test_authorization_transitions_validated(tmp_path):
    history = QualificationHistoryStore.open(path=tmp_path / "qual.db")
    from runway.flags import RunwayFeatureFlags
    service = QualificationService(history, flags=RunwayFeatureFlags(synthetic_qualification_enabled=True))

    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    registry = make_registry(providers=[provider])

    with pytest.raises(QualificationError):
        service.authorize(registry, "vendor-x", now=0, authorized_by="ops")

    qualified_registry = registry.with_provider(provider.model_copy(update={"lifecycle_state": LifecycleState.QUALIFIED}))
    with pytest.raises(QualificationError):
        service.authorize(qualified_registry, "vendor-x", now=0, authorized_by="")  # empty identity rejected

    approved_registry = service.authorize(qualified_registry, "vendor-x", now=0, authorized_by="ops")
    assert approved_registry.provider("vendor-x").lifecycle_state == LifecycleState.APPROVED

    entries = history.lifecycle_history("vendor-x")
    assert entries[-1]["to_state"] == "approved"
    assert entries[-1]["reason"] == "authorized_by:ops"

    with pytest.raises(InvalidLifecycleTransition):
        transition(LifecycleState.DISCOVERED, LifecycleState.APPROVED)


def test_unknown_provider_defaults_fail_closed():
    candidate = DiscoveryCandidate(
        candidate_id="cand-1", source="fixture:pricing-feed", suggested_provider_id="mystery-relay",
        suggested_display_name="Definitely Official Direct Provider", discovered_at=0,
        notes="claims to be tier a, first-party, officially endorsed",
    )
    provider = candidate_to_provider(candidate)
    assert provider.lifecycle_state == LifecycleState.DISCOVERED
    assert provider.trust_tier == TrustTier.EXPERIMENTAL_UNKNOWN
    assert data_classification_allowed(provider.trust_tier, DataClassification.RESTRICTED) is False
    assert data_classification_allowed(provider.trust_tier, DataClassification.SYNTHETIC) is True
