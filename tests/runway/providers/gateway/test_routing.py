from __future__ import annotations

from runway.classification import DataClassification
from runway.health import FakeHealthProbe, HealthStatus
from runway.pricing import CostModel, UsageEstimate
from runway.providers.gateway.models import GatewayProtocol
from runway.providers.gateway.registry import ChannelRegistry
from runway.providers.gateway.routing import GatewayRouter
from runway.scoring import RouteOutcome, RouteRequest

from ...conftest import TASK_TYPE, make_model, make_provider as make_decide_provider
from .conftest import make_channel, make_provider


def _decide_registry(*models):
    from runway.registry import Registry
    providers = tuple({m.provider_id for m in models})
    return Registry(providers=tuple(make_decide_provider(p) for p in providers), models=models)


def _request(**overrides) -> RouteRequest:
    fields = dict(
        request_id="r1", task_type=TASK_TYPE, data_classification=DataClassification.INTERNAL,
        usage_estimate=UsageEstimate(input_tokens=1000, output_tokens=500),
    )
    fields.update(overrides)
    return RouteRequest(**fields)


def test_cheaper_ineligible_rail_never_wins_over_eligible_pricier_one(generous_budget, performance_store):
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x",
        cost=CostModel(snapshot_at=0, input_micros_per_million=100_000, output_micros_per_million=100_000),
    )
    pricier = make_model(
        model_key="pricier@vendor-y", provider_id="vendor-y",
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    decide_registry = _decide_registry(cheap, pricier)

    from runway.flags import RunwayFeatureFlags
    flags = RunwayFeatureFlags.model_construct(external_execution_enabled=True)

    cheap_channel = make_channel("chan-x", "vendor-x", enabled=True)
    pricier_channel = make_channel("chan-y", "vendor-y", enabled=True)
    channels = ChannelRegistry(
        providers=(make_provider("vendor-x"), make_provider("vendor-y")),
        channels=(cheap_channel, pricier_channel),
        model_channel_map={"cheap@vendor-x": "chan-x", "pricier@vendor-y": "chan-y"},
    ).authorize_channel("chan-y")  # deliberately NOT authorizing the cheap one

    health = FakeHealthProbe(now=0)
    health.set_provider_health("chan-x", HealthStatus.HEALTHY)
    health.set_provider_health("chan-y", HealthStatus.HEALTHY)
    for model in (cheap, pricier):
        health.set_model_health(model.model_key, HealthStatus.HEALTHY)

    router = GatewayRouter(
        decide_registry, channels, flags=flags, health_probe=health,
        budget_ledger=generous_budget, performance_store=performance_store,
        require_known_balance=False,
    )
    result = router.route(_request(), timestamp=0)
    assert result.route_decision.outcome == RouteOutcome.ROUTED
    assert result.route_decision.selected_model_key == "pricier@vendor-y"

    cheap_eligibility = next(e for e in result.channel_eligibility if e.channel_id == "chan-x")
    assert cheap_eligibility.eligible is False
    assert "channel_not_authorized" in cheap_eligibility.reasons


def test_cheapest_eligible_rail_wins_reusing_existing_scorer_unchanged(generous_budget, performance_store):
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x",
        cost=CostModel(snapshot_at=0, input_micros_per_million=100_000, output_micros_per_million=100_000),
    )
    pricier = make_model(
        model_key="pricier@vendor-y", provider_id="vendor-y",
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    decide_registry = _decide_registry(cheap, pricier)

    from runway.flags import RunwayFeatureFlags
    flags = RunwayFeatureFlags.model_construct(external_execution_enabled=True)

    cheap_channel = make_channel("chan-x", "vendor-x", enabled=True)
    pricier_channel = make_channel("chan-y", "vendor-y", enabled=True)
    channels = ChannelRegistry(
        providers=(make_provider("vendor-x"), make_provider("vendor-y")),
        channels=(cheap_channel, pricier_channel),
        model_channel_map={"cheap@vendor-x": "chan-x", "pricier@vendor-y": "chan-y"},
    ).authorize_channel("chan-x").authorize_channel("chan-y")

    health = FakeHealthProbe(now=0)
    health.set_provider_health("chan-x", HealthStatus.HEALTHY)
    health.set_provider_health("chan-y", HealthStatus.HEALTHY)
    for model in (cheap, pricier):
        health.set_model_health(model.model_key, HealthStatus.HEALTHY)

    router = GatewayRouter(
        decide_registry, channels, flags=flags, health_probe=health,
        budget_ledger=generous_budget, performance_store=performance_store,
        require_known_balance=False,
    )
    result = router.route(_request(), timestamp=0)
    # Both channel-eligible and both roughly equal quality/reliability priors
    # (make_model defaults) -> the existing, unmodified RouteScorer picks
    # by expected_cost_to_success, which favors the cheaper one here.
    assert result.route_decision.selected_model_key == "cheap@vendor-x"


def test_unhealthy_cheap_rail_never_wins(generous_budget, performance_store):
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x",
        cost=CostModel(snapshot_at=0, input_micros_per_million=100_000, output_micros_per_million=100_000),
    )
    pricier = make_model(
        model_key="pricier@vendor-y", provider_id="vendor-y",
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    decide_registry = _decide_registry(cheap, pricier)
    from runway.flags import RunwayFeatureFlags
    flags = RunwayFeatureFlags.model_construct(external_execution_enabled=True)

    channels = ChannelRegistry(
        providers=(make_provider("vendor-x"), make_provider("vendor-y")),
        channels=(make_channel("chan-x", "vendor-x", enabled=True), make_channel("chan-y", "vendor-y", enabled=True)),
        model_channel_map={"cheap@vendor-x": "chan-x", "pricier@vendor-y": "chan-y"},
    ).authorize_channel("chan-x").authorize_channel("chan-y")

    health = FakeHealthProbe(now=0)
    health.set_provider_health("chan-x", HealthStatus.UNHEALTHY)  # the cheap rail is down
    health.set_provider_health("chan-y", HealthStatus.HEALTHY)
    for model in (cheap, pricier):
        health.set_model_health(model.model_key, HealthStatus.HEALTHY)

    router = GatewayRouter(
        decide_registry, channels, flags=flags, health_probe=health,
        budget_ledger=generous_budget, performance_store=performance_store,
        require_known_balance=False,
    )
    result = router.route(_request(), timestamp=0)
    assert result.route_decision.selected_model_key == "pricier@vendor-y"


def test_restricted_rail_never_leaks_into_generic_routing(generous_budget, performance_store):
    from runway.capabilities import Capability
    cheap = make_model(
        model_key="cheap@vendor-x", provider_id="vendor-x",
        cost=CostModel(snapshot_at=0, input_micros_per_million=100_000, output_micros_per_million=100_000),
    )
    pricier = make_model(
        model_key="pricier@vendor-y", provider_id="vendor-y",
        cost=CostModel(snapshot_at=0, input_micros_per_million=5_000_000, output_micros_per_million=15_000_000),
    )
    decide_registry = _decide_registry(cheap, pricier)
    from runway.flags import RunwayFeatureFlags
    flags = RunwayFeatureFlags.model_construct(external_execution_enabled=True)

    restricted_channel = make_channel("chan-x", "vendor-x", enabled=True, allowed_client_classes=("codex_pool",))
    channels = ChannelRegistry(
        providers=(make_provider("vendor-x"), make_provider("vendor-y")),
        channels=(restricted_channel, make_channel("chan-y", "vendor-y", enabled=True)),
        model_channel_map={"cheap@vendor-x": "chan-x", "pricier@vendor-y": "chan-y"},
    ).authorize_channel("chan-x").authorize_channel("chan-y")

    health = FakeHealthProbe(now=0)
    health.set_provider_health("chan-x", HealthStatus.HEALTHY)
    health.set_provider_health("chan-y", HealthStatus.HEALTHY)
    for model in (cheap, pricier):
        health.set_model_health(model.model_key, HealthStatus.HEALTHY)

    router = GatewayRouter(
        decide_registry, channels, flags=flags, health_probe=health,
        budget_ledger=generous_budget, performance_store=performance_store,
        require_known_balance=False,
    )
    # A generic (unclassified) request must not receive the restricted-cheap rail
    result = router.route(_request(), timestamp=0)
    assert result.route_decision.selected_model_key == "pricier@vendor-y"
