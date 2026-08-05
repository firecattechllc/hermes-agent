from __future__ import annotations

import time
from pathlib import Path

from hermes_cli.agent_roles.model_execution import (
    GovernedModelExecutionService,
    InMemoryModelExecutionStore,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
)
from hermes_cli.agent_roles.model_routing import (
    GovernedModelRouter,
    LatencyClass,
    ModelRecord,
    ModelRegistry,
    ProviderRecord,
    RoutingRequest,
    TrustTier,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.dispatch_gate import (
    CertificationSnapshot,
    InMemoryReferenceStore,
    PrimeGovernedProviderAdapter,
)
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.ollama_node import OllamaGenerateOutcome


def _now() -> int:
    return int(time.time())


class FakeUnderlying:
    """Stands in for OllamaNodeProviderAdapter.generate without real HTTP."""

    def __init__(self, outcome: OllamaGenerateOutcome) -> None:
        self._outcome = outcome
        self.calls = []

    def generate(self, *, alias, input_text, timeout_seconds):
        self.calls.append((alias, input_text, timeout_seconds))
        return self._outcome


def _runtime(tmp_path: Path) -> FleetRuntime:
    return FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="dispatch-test",
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )


def _register_and_heartbeat(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int):
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id=f"req-{natural_key}",
            natural_key=natural_key,
            role=role,
            declared_capabilities=("worker_heartbeat", "local_model_inference"),
            endpoint=f"http://{natural_key}.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key=natural_key,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )


def _certified() -> CertificationSnapshot:
    return CertificationSnapshot(status=CertificationStatus.CERTIFIED, evidence_ref="evidence://cert")


def _build_adapter(runtime, natural_key, provider_id, *, outcome, resolver, certification=None):
    underlying = FakeUnderlying(outcome)
    adapter = PrimeGovernedProviderAdapter(
        provider_id=provider_id,
        natural_key=natural_key,
        fleet_runtime=runtime,
        underlying=underlying,
        certification_provider=certification or _certified,
        input_resolver=resolver,
        clock=lambda: _now(),
    )
    return adapter, underlying


def _route(provider_models, *, budget=1000):
    registry = ModelRegistry(
        providers=tuple(ProviderRecord(provider_id=p, display_name=p) for p, _ in provider_models),
        models=tuple(
            ModelRecord(
                model_id=m, provider_id=p, display_name=m, capabilities=("code",),
                task_types=("engineering",), context_limit=1000, estimated_cost_micros=0,
                latency_class=LatencyClass.INTERACTIVE, quality_score=90, reliability_score=90,
                trust_tier=TrustTier.TRUSTED,
            )
            for p, m in provider_models
        ),
    )
    request = RoutingRequest(
        request_id="route-1", task_type="engineering",
        required_capabilities=("code",), budget_limit_micros=budget,
    )
    return GovernedModelRouter(registry).route(request, timestamp=_now())


def _execution_request(route, **overrides):
    values = dict(
        execution_id="exec-1", idempotency_key="idem-1", project_id="proj-1",
        task_id="task-1", request_id=route.request_id, routing_decision=route,
        selected_provider_id=route.selected_provider_id, selected_model_id=route.selected_model_id,
        input_reference="input://request-1", requested_at=_now(), maximum_attempts=3,
    )
    values.update(overrides)
    return ModelExecutionRequest(**values)


def test_dispatch_succeeds_against_admitted_healthy_certified_node(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    store = InMemoryReferenceStore()
    store.put("input://request-1", "summarize this")
    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="a summary"),
        resolver=store.resolve,
    )

    route = _route([("titan-ollama", "lightweight")])
    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route), timestamp=_now())

    assert evidence.state == ModelExecutionState.SUCCEEDED
    assert evidence.output_reference is not None
    assert underlying.calls == [("lightweight", "summarize this", 60)]


def test_dispatch_refuses_unregistered_node_without_calling_underlying(tmp_path) -> None:
    runtime = _runtime(tmp_path)  # titan never registered
    store = InMemoryReferenceStore()
    store.put("input://request-1", "summarize this")
    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"),
        resolver=store.resolve,
    )

    route = _route([("titan-ollama", "lightweight")])
    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route), timestamp=_now())

    assert evidence.state == ModelExecutionState.FAILED
    assert evidence.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
    assert underlying.calls == []


def test_dispatch_refuses_revoked_node(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    runtime.revoke_node("titan", now=now, reason="rotation")

    store = InMemoryReferenceStore()
    store.put("input://request-1", "x")
    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="unreachable"),
        resolver=store.resolve,
    )
    route = _route([("titan-ollama", "lightweight")])
    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route), timestamp=_now())

    assert evidence.state == ModelExecutionState.FAILED
    assert evidence.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
    assert underlying.calls == []


def test_dispatch_refuses_stale_node(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    store = InMemoryReferenceStore()
    store.put("input://request-1", "x")
    far_future = now + 100_000
    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="unreachable"),
        resolver=store.resolve,
    )
    adapter._clock = lambda: far_future  # simulate time passing without a new heartbeat

    route = _route([("titan-ollama", "lightweight")])
    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route, requested_at=far_future), timestamp=far_future)

    assert evidence.state == ModelExecutionState.FAILED
    assert evidence.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID
    assert underlying.calls == []


def test_empty_model_id_is_rejected_before_any_admission_check(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    store = InMemoryReferenceStore()
    store.put("input://request-1", "x")
    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"),
        resolver=store.resolve,
    )
    result = adapter.execute(model_id="", input_reference="input://request-1", timeout_seconds=30)
    assert result.error_classification == ModelExecutionErrorClass.INVALID_REQUEST
    assert underlying.calls == []


def test_unresolvable_input_reference_is_rejected(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    adapter, underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"),
        resolver=lambda ref: None,
    )
    result = adapter.execute(model_id="lightweight", input_reference="input://missing-ref", timeout_seconds=30)
    assert result.error_classification == ModelExecutionErrorClass.INVALID_REQUEST
    assert underlying.calls == []


def test_unavailable_endpoint_falls_back_to_a_different_admitted_node(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)

    store = InMemoryReferenceStore()
    store.put("input://request-1", "x")

    # Candidates tie on score/cost, so the router's deterministic tie-break
    # (provider_id, model_id) selects "mac-ollama" first and "titan-ollama"
    # as the fallback — the unavailable outcome goes on the node that is
    # actually selected primary.
    titan_adapter, titan_underlying = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="titan produced this"),
        resolver=store.resolve,
    )
    mac_adapter, mac_underlying = _build_adapter(
        runtime, "mac", "mac-ollama",
        outcome=OllamaGenerateOutcome(succeeded=False, error="connection refused", retryable=True),
        resolver=store.resolve,
    )

    route = _route([("titan-ollama", "lightweight"), ("mac-ollama", "primary_reasoning")])
    service = GovernedModelExecutionService((titan_adapter, mac_adapter), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route), timestamp=_now())

    assert evidence.state == ModelExecutionState.SUCCEEDED
    assert len(titan_underlying.calls) == 1
    assert len(mac_underlying.calls) == 1


def test_never_falls_back_to_an_adapter_absent_from_the_governed_set(tmp_path) -> None:
    """A route naming a provider that was never wrapped/registered as a governed
    adapter must fail, not silently dispatch through some other path."""
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    store = InMemoryReferenceStore()
    store.put("input://request-1", "x")
    titan_adapter, _ = _build_adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=False, error="down", retryable=True),
        resolver=store.resolve,
    )
    # "mac-ollama" is a real candidate in the route, but no adapter for it was
    # ever constructed/registered — it must not be reachable.
    route = _route([("titan-ollama", "lightweight"), ("mac-ollama", "primary_reasoning")])
    service = GovernedModelExecutionService((titan_adapter,), InMemoryModelExecutionStore())
    evidence = service.execute(_execution_request(route), timestamp=_now())

    assert evidence.state != ModelExecutionState.SUCCEEDED
