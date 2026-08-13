"""Synthetic baseline comparison (spec section 25): all-premium routing vs.
Runway-optimized routing, over a small fixture world of fake providers with
different price/success characteristics.

Entirely synthetic — fixture registry, deterministic seeded RNG standing in
for real-world success/failure, and isolated temp-file SQLite stores (never
the real ``~/.hermes``). No network, no real providers, safe to run anytime.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from runway.budget import BudgetLedger, BudgetPolicy
from runway.capabilities import Capability, ModelCapabilityProfile
from runway.classification import DataClassification
from runway.flags import RunwayFeatureFlags
from runway.health import FakeHealthProbe, HealthStatus
from runway.lifecycle import LifecycleState
from runway.outcomes import HistoricalPerformanceStore, TaskOutcome
from runway.pricing import CostModel, UsageEstimate, estimate_cost_micros
from runway.registry import (
    CanaryStatus, EndpointRecord, EndpointRole, LatencyClass, ModelRecord, ProviderRecord, Registry,
)
from runway.router import RunwayRouter
from runway.scoring import RouteRequest
from runway.trust import TrustTier

_TASK_TYPE = "code_generation"
_USAGE = UsageEstimate(input_tokens=4000, output_tokens=1200)

#: Ground-truth success probability per model — the simulation's "reality",
#: never visible to the router (which only ever sees `quality_score` priors
#: and, as the run progresses, observed history).
_GROUND_TRUTH_SUCCESS = {
    "frontier-model@acme-frontier": 0.97,
    "budget-model@budget-cloud": 0.80,
    "discount-model@discount-relay": 0.55,
}


def _capability_profile() -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        capabilities=(Capability.TEXT, Capability.CODING, Capability.TOOL_CALLING),
        max_context_tokens=128_000, max_output_tokens=8_000,
        supports_tool_calling=True,
    )


def build_fixture_registry() -> Registry:
    providers = (
        ProviderRecord(
            provider_id="acme-frontier", display_name="Acme Frontier Labs",
            trust_tier=TrustTier.DIRECT_OFFICIAL, lifecycle_state=LifecycleState.APPROVED,
            endpoints=(EndpointRecord(endpoint_id="primary", role=EndpointRole.PRIMARY),),
        ),
        ProviderRecord(
            provider_id="budget-cloud", display_name="Budget Inference Cloud",
            trust_tier=TrustTier.ESTABLISHED_CLOUD, lifecycle_state=LifecycleState.APPROVED,
            endpoints=(EndpointRecord(endpoint_id="primary", role=EndpointRole.PRIMARY),),
        ),
        ProviderRecord(
            provider_id="discount-relay", display_name="Discount Model Relay",
            trust_tier=TrustTier.VETTED_AGGREGATOR, lifecycle_state=LifecycleState.APPROVED,
            endpoints=(EndpointRecord(endpoint_id="primary", role=EndpointRole.PRIMARY),),
        ),
    )
    models = (
        ModelRecord(
            model_key="frontier-model@acme-frontier", model_family="frontier-model",
            provider_id="acme-frontier", display_name="Frontier Model",
            capability_profile=_capability_profile(), task_types=(_TASK_TYPE,),
            cost=CostModel(snapshot_at=0, input_micros_per_million=15_000_000, output_micros_per_million=45_000_000),
            latency_class=LatencyClass.STANDARD, quality_score=95,
            canary_status=CanaryStatus.PASSED, canary_pass_rate=100,
        ),
        ModelRecord(
            model_key="budget-model@budget-cloud", model_family="budget-model",
            provider_id="budget-cloud", display_name="Budget Model",
            capability_profile=_capability_profile(), task_types=(_TASK_TYPE,),
            cost=CostModel(snapshot_at=0, input_micros_per_million=500_000, output_micros_per_million=1_500_000),
            latency_class=LatencyClass.STANDARD, quality_score=70,
            canary_status=CanaryStatus.PASSED, canary_pass_rate=100,
        ),
        ModelRecord(
            model_key="discount-model@discount-relay", model_family="discount-model",
            provider_id="discount-relay", display_name="Discount Model",
            capability_profile=_capability_profile(), task_types=(_TASK_TYPE,),
            cost=CostModel(snapshot_at=0, input_micros_per_million=200_000, output_micros_per_million=600_000),
            latency_class=LatencyClass.STANDARD, quality_score=55,
            canary_status=CanaryStatus.PASSED, canary_pass_rate=100,
        ),
    )
    return Registry(providers=providers, models=models)


class ArmReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tasks: int
    successes: int
    retries: int
    escalations: int
    total_cost_micros: int

    @property
    def avg_cost_per_task_micros(self) -> int:
        return self.total_cost_micros // self.tasks if self.tasks else 0

    @property
    def cost_per_success_micros(self) -> int | None:
        return self.total_cost_micros // self.successes if self.successes else None


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    all_premium: ArmReport
    runway: ArmReport

    @property
    def savings_micros_per_success(self) -> int | None:
        base, opt = self.all_premium.cost_per_success_micros, self.runway.cost_per_success_micros
        if base is None or opt is None:
            return None
        return base - opt

    @property
    def savings_percent(self) -> float | None:
        base = self.all_premium.cost_per_success_micros
        savings = self.savings_micros_per_success
        if base in (None, 0) or savings is None:
            return None
        return (savings / base) * 100.0


def _attempt(model_key: str, rng: random.Random) -> bool:
    return rng.random() < _GROUND_TRUTH_SUCCESS[model_key]


def _run_all_premium(registry: Registry, *, num_tasks: int, rng: random.Random) -> ArmReport:
    model = next(item for item in registry.models if item.provider_id == "acme-frontier")
    cost_per_attempt = estimate_cost_micros(model.cost, _USAGE)
    successes = retries = 0
    total_cost = 0
    for _ in range(num_tasks):
        total_cost += cost_per_attempt
        ok = _attempt(model.model_key, rng)
        if not ok:
            retries += 1
            total_cost += cost_per_attempt
            ok = _attempt(model.model_key, rng)
        if ok:
            successes += 1
    return ArmReport(name="all_premium", tasks=num_tasks, successes=successes, retries=retries,
                      escalations=0, total_cost_micros=total_cost)


def _run_runway(registry: Registry, *, num_tasks: int, rng: random.Random, tmp_dir: Path, seed: int) -> ArmReport:
    budget = BudgetLedger.open(
        BudgetPolicy(per_task_max_micros=10_000_000, per_hour_max_micros=10_000_000_000,
                     per_day_max_micros=100_000_000_000, per_month_max_micros=1_000_000_000_000),
        path=tmp_dir / f"budget-{seed}.db",
    )
    performance = HistoricalPerformanceStore.open(path=tmp_dir / f"perf-{seed}.db")
    health = FakeHealthProbe(now=0)
    for provider in registry.providers:
        health.set_provider_health(provider.provider_id, HealthStatus.HEALTHY)
    for model in registry.models:
        health.set_model_health(model.model_key, HealthStatus.HEALTHY)

    router = RunwayRouter(
        registry, flags=RunwayFeatureFlags(runway_enabled=True),
        health_probe=health, budget_ledger=budget, performance_store=performance,
    )
    frontier_key = "frontier-model@acme-frontier"
    frontier_cost = estimate_cost_micros(
        next(item for item in registry.models if item.model_key == frontier_key).cost, _USAGE
    )

    successes = retries = escalations = 0
    total_cost = 0
    for task_index in range(num_tasks):
        request = RouteRequest(
            request_id=f"task-{seed}-{task_index}", task_type=_TASK_TYPE,
            data_classification=DataClassification.INTERNAL, usage_estimate=_USAGE,
        )
        decision = router.route(request, timestamp=task_index)
        if decision.selected_model_key is None:
            continue
        model_key = decision.selected_model_key
        cost = decision.estimated_cost_micros or 0
        total_cost += cost
        ok = _attempt(model_key, rng)
        if not ok:
            retries += 1
            total_cost += cost
            ok = _attempt(model_key, rng)
        if not ok and model_key != frontier_key:
            escalations += 1
            total_cost += frontier_cost
            ok = _attempt(frontier_key, rng)
            model_key = frontier_key
        performance.record(TaskOutcome(
            task_type=_TASK_TYPE, model_key=model_key, provider_id=model_key.split("@")[1],
            timestamp=task_index, input_tokens=_USAGE.input_tokens, output_tokens=_USAGE.output_tokens,
            estimated_cost_micros=cost, verifier_passed=ok, retried=(not ok or retries > 0),
            escalated_to=frontier_key if escalations and model_key == frontier_key else None,
        ))
        if ok:
            successes += 1

    return ArmReport(name="runway", tasks=num_tasks, successes=successes, retries=retries,
                      escalations=escalations, total_cost_micros=total_cost)


def run_benchmark(*, num_tasks: int = 200, seed: int = 1337) -> BenchmarkReport:
    registry = build_fixture_registry()
    with tempfile.TemporaryDirectory(prefix="runway-benchmark-") as tmp:
        tmp_dir = Path(tmp)
        all_premium = _run_all_premium(registry, num_tasks=num_tasks, rng=random.Random(seed))
        runway = _run_runway(registry, num_tasks=num_tasks, rng=random.Random(seed), tmp_dir=tmp_dir, seed=seed)
    return BenchmarkReport(all_premium=all_premium, runway=runway)


if __name__ == "__main__":
    report = run_benchmark()
    for arm in (report.all_premium, report.runway):
        print(f"{arm.name}: tasks={arm.tasks} successes={arm.successes} retries={arm.retries} "
              f"escalations={arm.escalations} total_cost_micros={arm.total_cost_micros} "
              f"avg_cost_per_task_micros={arm.avg_cost_per_task_micros} "
              f"cost_per_success_micros={arm.cost_per_success_micros}")
    print(f"savings_per_success_micros={report.savings_micros_per_success} "
          f"savings_percent={report.savings_percent}")
