from __future__ import annotations

import pytest
from pydantic import ValidationError

from runway.classification import DataClassification
from runway.flags import RunwayFeatureFlags
from runway.outcomes import HistoricalPerformanceStore, TaskOutcome
from runway.pricing import UsageEstimate
from runway.router import RunwayRouter
from runway.scoring import RouteRequest
from runway.telemetry import RunwayEvent, RunwayEventType, RunwayTelemetryLog

from .conftest import TASK_TYPE, healthy_probe, make_model, make_provider, make_registry


def test_route_explanation_produced(generous_budget, performance_store, tmp_path):
    provider = make_provider("vendor-x")
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])
    telemetry = RunwayTelemetryLog(path=tmp_path / "telemetry.jsonl")
    router = RunwayRouter(
        registry, flags=RunwayFeatureFlags(runway_enabled=True), health_probe=healthy_probe(registry),
        budget_ledger=generous_budget, performance_store=performance_store, telemetry=telemetry,
    )
    router.route(RouteRequest(
        request_id="r1", task_type=TASK_TYPE, data_classification=DataClassification.INTERNAL,
        usage_estimate=UsageEstimate(input_tokens=100, output_tokens=50),
    ), timestamp=0)

    events = telemetry.read_all()
    types = [event.event_type for event in events]
    assert RunwayEventType.ROUTE_CANDIDATES_EVALUATED in types
    assert RunwayEventType.ROUTE_SELECTED in types
    assert all(event.correlation_id == "r1" for event in events)


def test_sensitive_content_absent_from_logs():
    with pytest.raises(ValidationError):
        RunwayEvent(
            event_id="evt-1", event_type=RunwayEventType.ROUTE_SELECTED, timestamp=0,
            correlation_id="r1", payload={"api_key": "sk-should-never-be-here"},
        )
    with pytest.raises(ValidationError):
        RunwayEvent(
            event_id="evt-2", event_type=RunwayEventType.ROUTE_SELECTED, timestamp=0,
            correlation_id="r1", payload={"note": "user token=abc123 embedded in free text"},
        )
    # a normal, non-sensitive payload is accepted
    event = RunwayEvent(
        event_id="evt-3", event_type=RunwayEventType.ROUTE_SELECTED, timestamp=0,
        correlation_id="r1", payload={"provider_id": "vendor-x", "estimated_cost_micros": 123},
    )
    assert event.payload["provider_id"] == "vendor-x"


def test_cost_success_metrics_generated(tmp_path):
    store = HistoricalPerformanceStore.open(path=tmp_path / "perf.db")
    for i in range(10):
        store.record(TaskOutcome(
            task_type=TASK_TYPE, model_key="model@vendor-x", provider_id="vendor-x", timestamp=i,
            estimated_cost_micros=100, verifier_passed=(i < 6),
        ))
    aggregate = store.aggregate(task_type=TASK_TYPE, model_key="model@vendor-x")
    assert aggregate.attempts == 10
    assert aggregate.successes == 6
    assert aggregate.success_rate == 0.6
    assert aggregate.avg_cost_micros == 100
    assert aggregate.cost_per_success_micros == 100


def test_escalation_recorded(tmp_path):
    store = HistoricalPerformanceStore.open(path=tmp_path / "perf.db")
    store.record(TaskOutcome(
        task_type=TASK_TYPE, model_key="cheap@vendor-x", provider_id="vendor-x", timestamp=0,
        verifier_passed=False, escalated_to="premium@vendor-y",
    ))
    store.record(TaskOutcome(
        task_type=TASK_TYPE, model_key="cheap@vendor-x", provider_id="vendor-x", timestamp=1,
        verifier_passed=True,
    ))
    aggregate = store.aggregate(task_type=TASK_TYPE, model_key="cheap@vendor-x")
    assert aggregate.escalations == 1
    assert aggregate.escalation_rate == 0.5
