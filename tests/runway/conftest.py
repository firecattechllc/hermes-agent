from __future__ import annotations

import pytest

from runway.budget import BudgetLedger, BudgetPolicy
from runway.capabilities import Capability, ModelCapabilityProfile
from runway.health import FakeHealthProbe, HealthStatus
from runway.lifecycle import LifecycleState
from runway.outcomes import HistoricalPerformanceStore
from runway.pricing import CostModel
from runway.registry import (
    CanaryStatus, EndpointRecord, EndpointRole, LatencyClass, ModelRecord, ProviderRecord, Registry,
)
from runway.trust import TrustTier

TASK_TYPE = "code_generation"


def make_capability_profile(**overrides) -> ModelCapabilityProfile:
    fields = dict(
        capabilities=(Capability.TEXT, Capability.CODING),
        max_context_tokens=32_000, max_output_tokens=4_000,
    )
    fields.update(overrides)
    return ModelCapabilityProfile(**fields)


def make_provider(provider_id: str = "acme", **overrides) -> ProviderRecord:
    fields = dict(
        provider_id=provider_id, display_name=provider_id.replace("-", " ").title(),
        trust_tier=TrustTier.DIRECT_OFFICIAL, lifecycle_state=LifecycleState.APPROVED,
        endpoints=(EndpointRecord(endpoint_id="primary", role=EndpointRole.PRIMARY),),
    )
    fields.update(overrides)
    return ProviderRecord(**fields)


def make_model(model_key: str = "model-a@acme", provider_id: str = "acme", **overrides) -> ModelRecord:
    fields = dict(
        model_key=model_key, model_family=model_key.split("@")[0], provider_id=provider_id,
        display_name=model_key, capability_profile=make_capability_profile(),
        task_types=(TASK_TYPE,),
        cost=CostModel(snapshot_at=0, input_micros_per_million=1_000_000, output_micros_per_million=3_000_000),
        latency_class=LatencyClass.STANDARD, quality_score=80,
        canary_status=CanaryStatus.PASSED, canary_pass_rate=100,
    )
    fields.update(overrides)
    return ModelRecord(**fields)


def make_registry(providers=(), models=()) -> Registry:
    return Registry(providers=tuple(providers), models=tuple(models))


def healthy_probe(registry: Registry, *, now: int = 0) -> FakeHealthProbe:
    probe = FakeHealthProbe(now=now)
    for provider in registry.providers:
        probe.set_provider_health(provider.provider_id, HealthStatus.HEALTHY)
    for model in registry.models:
        probe.set_model_health(model.model_key, HealthStatus.HEALTHY)
    return probe


@pytest.fixture
def generous_budget(tmp_path) -> BudgetLedger:
    policy = BudgetPolicy(
        per_task_max_micros=1_000_000_000, per_hour_max_micros=1_000_000_000,
        per_day_max_micros=1_000_000_000, per_month_max_micros=1_000_000_000,
    )
    return BudgetLedger.open(policy, path=tmp_path / "budget.db")


@pytest.fixture
def performance_store(tmp_path) -> HistoricalPerformanceStore:
    return HistoricalPerformanceStore.open(path=tmp_path / "performance.db")
