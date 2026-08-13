from __future__ import annotations

import pytest
from pydantic import ValidationError

from runway.classification import DataClassification
from runway.flags import RunwayFeatureFlags
from runway.pricing import UsageEstimate
from runway.router import RunwayDisabled, RunwayRouter
from runway.scoring import RouteRequest

from .conftest import TASK_TYPE, healthy_probe, make_model, make_provider, make_registry


def test_disabled_by_default():
    flags = RunwayFeatureFlags()
    assert flags.runway_enabled is False
    assert flags.discovery_enabled is False
    assert flags.synthetic_qualification_enabled is False
    assert flags.external_execution_enabled is False


def test_external_execution_enabled_cannot_be_set_true():
    with pytest.raises(ValidationError):
        RunwayFeatureFlags(external_execution_enabled=True)


def test_from_env_ignores_external_execution_env_var(monkeypatch):
    monkeypatch.setenv("RUNWAY_ENABLED", "1")
    monkeypatch.setenv("RUNWAY_EXTERNAL_EXECUTION_ENABLED", "1")  # not even a recognized var name
    flags = RunwayFeatureFlags.from_env()
    assert flags.runway_enabled is True
    assert flags.external_execution_enabled is False


def test_router_raises_when_runway_disabled(generous_budget, performance_store):
    provider = make_provider("vendor-x")
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])
    router = RunwayRouter(
        registry, flags=RunwayFeatureFlags(runway_enabled=False), health_probe=healthy_probe(registry),
        budget_ledger=generous_budget, performance_store=performance_store,
    )
    with pytest.raises(RunwayDisabled):
        router.route(RouteRequest(
            request_id="r1", task_type=TASK_TYPE, data_classification=DataClassification.INTERNAL,
            usage_estimate=UsageEstimate(input_tokens=10, output_tokens=10),
        ), timestamp=0)
