from __future__ import annotations

import pytest

from runway.canary import DeterministicFakeProvider
from runway.flags import RunwayFeatureFlags
from runway.lifecycle import LifecycleState
from runway.qualification import QualificationError, QualificationHistoryStore, QualificationService
from runway.registry import CanaryStatus

from .conftest import make_model, make_provider, make_registry


@pytest.fixture
def service(tmp_path):
    history = QualificationHistoryStore.open(path=tmp_path / "qual.db")
    flags = RunwayFeatureFlags(synthetic_qualification_enabled=True)
    return QualificationService(history, flags=flags), history


def test_fake_provider_can_pass(service):
    svc, _ = service
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x", canary_status=CanaryStatus.NOT_RUN)
    registry = make_registry(providers=[provider], models=[model])

    client = DeterministicFakeProvider(identity_fingerprint="fp-good", context_limit=64_000)
    registry, outcome = svc.run_qualification(registry, model, client, now=100)

    assert outcome.status == CanaryStatus.PASSED
    assert registry.provider("vendor-x").lifecycle_state == LifecycleState.QUALIFIED
    assert registry.models_for_provider("vendor-x")[0].canary_status == CanaryStatus.PASSED


def test_fake_provider_can_fail(service):
    svc, _ = service
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x", canary_status=CanaryStatus.NOT_RUN)
    registry = make_registry(providers=[provider], models=[model])

    client = DeterministicFakeProvider(identity_fingerprint="fp-good", context_limit=100)  # too small
    registry, outcome = svc.run_qualification(registry, model, client, now=100)

    assert outcome.status == CanaryStatus.FAILED
    assert registry.provider("vendor-x").lifecycle_state == LifecycleState.SYNTHETIC_TESTING
    assert registry.models_for_provider("vendor-x")[0].canary_status == CanaryStatus.FAILED


def test_canary_regression_causes_quarantine(service):
    svc, history = service
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.APPROVED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])

    client = DeterministicFakeProvider(identity_fingerprint="fp-good", context_limit=100)  # regressed
    registry, outcome = svc.run_qualification(registry, model, client, now=200)

    assert outcome.status == CanaryStatus.FAILED
    assert registry.provider("vendor-x").lifecycle_state == LifecycleState.QUARANTINED
    last = history.lifecycle_history("vendor-x")[-1]
    assert last["to_state"] == "quarantined"
    assert last["reason"] == "canary_regression"


def test_model_identity_mismatch_recorded(service):
    svc, history = service
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.APPROVED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])

    client = DeterministicFakeProvider(identity_fingerprint="fp-SUBSTITUTED", context_limit=64_000)
    registry, outcome = svc.run_qualification(
        registry, model, client, now=200, expected_identity_fingerprint="fp-original",
    )

    assert outcome.suspected_identity_mismatch is True
    assert registry.models_for_provider("vendor-x")[0].suspected_identity_mismatch is True
    last = history.lifecycle_history("vendor-x")[-1]
    assert last["reason"] == "identity_mismatch"


def test_qualification_does_not_imply_authorization(service, generous_budget, performance_store):
    svc, _ = service
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x", canary_status=CanaryStatus.NOT_RUN)
    registry = make_registry(providers=[provider], models=[model])

    client = DeterministicFakeProvider(identity_fingerprint="fp-good", context_limit=64_000)
    registry, outcome = svc.run_qualification(registry, model, client, now=100)
    assert outcome.status == CanaryStatus.PASSED
    assert registry.provider("vendor-x").lifecycle_state == LifecycleState.QUALIFIED

    from runway.classification import DataClassification
    from runway.health import FakeHealthProbe, HealthStatus
    from runway.pricing import UsageEstimate
    from runway.scoring import RouteOutcome, RouteRequest, RouteScorer

    probe = FakeHealthProbe(now=100)
    probe.set_provider_health("vendor-x", HealthStatus.HEALTHY)
    probe.set_model_health("model@vendor-x", HealthStatus.HEALTHY)
    scorer = RouteScorer(registry, health_probe=probe, budget_ledger=generous_budget, performance_store=performance_store)
    decision = scorer.route(RouteRequest(
        request_id="r1", task_type="code_generation", data_classification=DataClassification.INTERNAL,
        usage_estimate=UsageEstimate(input_tokens=100, output_tokens=100),
    ), timestamp=100)
    assert decision.outcome == RouteOutcome.NO_ROUTE
    assert "provider_not_approved" in decision.candidates[0].rejection_reasons


def test_run_qualification_requires_flag_enabled(tmp_path):
    history = QualificationHistoryStore.open(path=tmp_path / "qual.db")
    service_disabled = QualificationService(history, flags=RunwayFeatureFlags(synthetic_qualification_enabled=False))
    provider = make_provider("vendor-x", lifecycle_state=LifecycleState.DISCOVERED)
    model = make_model(model_key="model@vendor-x", provider_id="vendor-x")
    registry = make_registry(providers=[provider], models=[model])
    client = DeterministicFakeProvider(identity_fingerprint="fp", context_limit=64_000)
    with pytest.raises(QualificationError):
        service_disabled.run_qualification(registry, model, client, now=0)
