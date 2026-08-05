from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai import (
    ORCHESTRATION_WORKFLOW,
    Capability,
    CostClass,
    CPUClass,
    DeviceClass,
    DurableFleetStore,
    FleetConflictError,
    FleetExecutionCoordinator,
    FleetModelInventory,
    FleetNodeHealth,
    FleetNodeIdentity,
    FleetNodeRegistration,
    FleetNodeRole,
    FleetNodeState,
    FleetRegistry,
    FleetRoutingRequest,
    FleetSpecialistStepExecutor,
    FleetStoreError,
    FleetValidationError,
    GovernedFleetRouter,
    GovernedFleetTransport,
    GovernedOrchestrationRequest,
    GovernedRemoteResult,
    MemoryClass,
    NoEligibleFleetNodeError,
    OrchestrationStepStatus,
    PrivacyTier,
    ProviderHealth,
    RemoteTaskState,
    Responsibility,
    TrustTier,
    WorkerTaskType,
    build_orchestration_plan,
    fleet_evidence,
)
from sigil.ai.inspection import ai_status
from sigil.ai.models import ExecutionLocation
from sigil.ai.registry import canonical_digest

DIGEST = "sha256:" + "a" * 64
REGISTRY_DIGEST = "sha256:" + "b" * 64
NOW = "2026-08-01T18:00:00+00:00"


def model(role: FleetNodeRole, capability: Capability = Capability.REASONING):
    return FleetModelInventory(
        f"{role.value}-provider",
        f"{role.value}-model",
        f"{role.value}-tokenizer",
        frozenset({capability}),
        768 if capability == Capability.SEMANTIC_RETRIEVAL else None,
        DIGEST if capability == Capability.SEMANTIC_RETRIEVAL else None,
    )


def registration(role: FleetNodeRole, **changes) -> FleetNodeRegistration:
    identity = FleetNodeIdentity(
        f"node-{role.value}",
        role.value,
        role,
        DeviceClass.SERVER if role != FleetNodeRole.MAC else DeviceClass.WORKSTATION,
        "darwin" if role == FleetNodeRole.MAC else "linux",
        "arm64",
        "governed-os",
        TrustTier.TRUSTED,
        PrivacyTier.LOCAL_ONLY,
        ExecutionLocation.FLEET,
        f"tailnet:{role.value}",
        f"identity-ref:{role.value}",
        NOW,
        NOW,
        True,
        True,
    )
    values = {
        "identity": identity,
        "models": (model(role),),
        "supported_task_types": frozenset({WorkerTaskType.RESEARCH_PREPARATION}),
        "memory_class": MemoryClass.LARGE if role == FleetNodeRole.MAC else MemoryClass.MEDIUM,
        "cpu_class": CPUClass.HIGH if role == FleetNodeRole.MAC else CPUClass.STANDARD,
        "accelerator_class": None,
        "maximum_concurrency": 2,
        "maximum_task_duration_ms": 10_000,
        "maximum_input_chars": 2_000,
        "maximum_output_chars": 2_000,
        "resource_enforcement_verified": True,
        "enabled": True,
        "health": ProviderHealth.HEALTHY,
    }
    values.update(changes)
    return FleetNodeRegistration(**values)


def heartbeat(node: FleetNodeRegistration, **changes) -> FleetNodeHealth:
    values = {
        "node_id": node.identity.node_id,
        "authenticated_identity_ref": node.identity.authenticated_identity_ref,
        "observed_at": NOW,
        "node_timestamp": NOW,
        "state": FleetNodeState.HEALTHY,
        "available_capabilities": node.capabilities,
        "available_model_ids": tuple(item.model_id for item in node.models),
        "current_load": 10,
        "active_tasks": 0,
        "queue_depth": 0,
        "memory_pressure": "normal",
        "disk_pressure": "normal",
        "thermal_state": "normal",
        "transport_health": ProviderHealth.HEALTHY,
        "maintenance": False,
        "draining": False,
    }
    values.update(changes)
    return FleetNodeHealth(**values)


def routing_request(**changes) -> FleetRoutingRequest:
    values = {
        "fleet_request_id": "fleet-request-001",
        "orchestration_id": "orchestration-fleet-001",
        "step_id": "orchestration-step-" + "c" * 64,
        "task_correlation_id": "fleet-task-001",
        "required_capability": Capability.REASONING,
        "responsibility": Responsibility.RESEARCH_ANALYSIS,
        "required_provider_id": None,
        "required_model_id": None,
        "required_tokenizer_id": None,
        "required_vector_dimension": None,
        "required_corpus_revision": None,
        "privacy_requirement": PrivacyTier.LOCAL_ONLY,
        "minimum_trust_tier": TrustTier.TRUSTED,
        "preferred_node_roles": (
            FleetNodeRole.TITAN,
            FleetNodeRole.MAC,
            FleetNodeRole.PRIME,
        ),
        "excluded_node_ids": (),
        "maximum_latency_ms": 5_000,
        "maximum_duration_ms": 5_000,
        "maximum_memory_class": MemoryClass.MEDIUM,
        "minimum_cpu_class": CPUClass.STANDARD,
        "maximum_cost_class": CostClass.FREE,
        "fallback_permission": False,
        "escalation_permission": True,
        "cancellation_policy": "query_before_retry",
        "maximum_retries": 1,
        "maximum_remote_steps": 1,
        "input_digests": (DIGEST,),
        "evidence_context_digests": (DIGEST,),
        "requested_at": NOW,
    }
    values.update(changes)
    return FleetRoutingRequest(**values)


class FakeAdapter:
    def __init__(self, registration: FleetNodeRegistration, mode: str = "success") -> None:
        self.registration = registration
        self.mode = mode
        self.tasks = []
        self.results = {}

    def dispatch(self, task):
        self.tasks.append(task)
        if self.mode == "timeout":
            raise TimeoutError
        payload = (("finding", f"bounded {self.registration.identity.node_role.value} result"),)
        state = RemoteTaskState.SUCCEEDED if self.mode == "success" else RemoteTaskState.FAILED
        output = (
            f"sha256:{canonical_digest(payload)}" if state == RemoteTaskState.SUCCEEDED else None
        )
        result = GovernedRemoteResult(
            f"remote-result-{canonical_digest(task.remote_task_id)[:64]}",
            task.remote_task_id,
            task.node_id,
            self.registration.models[0].provider_id,
            self.registration.models[0].model_id,
            NOW,
            NOW,
            state,
            payload if state == RemoteTaskState.SUCCEEDED else (),
            f"sha256:{canonical_digest(task.input_digests)}",
            output,
            (("duration_ms", "10"),),
            "not_requested",
            ("Advisory only.",),
            f"sha256:{canonical_digest({'task': task.remote_task_id, 'state': state.value})}",
            None if state == RemoteTaskState.SUCCEEDED else "provider_unavailable",
        )
        if self.mode == "corrupt":
            result = replace(result, output_digest="sha256:" + "f" * 64)
        if self.mode == "spoof":
            result = replace(result, node_id="node-spoof")
        self.results[task.remote_task_id] = result
        return result

    def cancel(self, task_id, cancellation_token_id):
        return RemoteTaskState.CANCELLED

    def query(self, task_id):
        return self.results.get(task_id)


def fleet(*nodes):
    registry = FleetRegistry(nodes)
    router = GovernedFleetRouter(registry)
    health = {node.identity.node_id: heartbeat(node) for node in nodes}
    return registry, router, health


def test_node_registration_authentication_and_security_defaults() -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, _, _ = fleet(titan)
    assert registry.authenticate(titan.identity.node_id, "identity-ref:titan") == titan
    with pytest.raises(FleetValidationError):
        registry.authenticate(titan.identity.node_id, "identity-ref:spoof")
    with pytest.raises(FleetConflictError):
        FleetRegistry((titan, titan))
    with pytest.raises(FleetValidationError):
        replace(titan, shell_allowed=True)
    with pytest.raises(FleetValidationError):
        replace(titan, credentials_available=True)
    with pytest.raises(FleetValidationError):
        replace(titan.identity, transport_identity="http://renderer-defined")


def test_authenticated_worker_only_node_can_register_without_fabricated_model() -> None:
    prime = registration(FleetNodeRole.PRIME, models=())
    assert prime.models == ()
    assert prime.supported_task_types == frozenset({WorkerTaskType.RESEARCH_PREPARATION})
    with pytest.raises(FleetValidationError):
        replace(prime, supported_task_types=frozenset())
    _, router, health = fleet(prime)
    decision = router.route(
        routing_request(required_task_type=WorkerTaskType.RESEARCH_PREPARATION),
        health,
        decided_at=NOW,
    )
    assert decision.selected_node_id == "node-prime"
    assert decision.selected_model_id is None and decision.selected_provider_id is None
    constrained = router.route(
        routing_request(
            required_task_type=WorkerTaskType.RESEARCH_PREPARATION,
            required_model_id="gemma-3-12b",
            fallback_permission=True,
        ),
        health,
        decided_at=NOW,
    )
    assert constrained.selected_node_id is None
    assert constrained.considered_nodes[0].reasons == ("capability_or_model_mismatch",)


def test_unknown_or_unauthenticated_node_fails_closed() -> None:
    identity = replace(
        registration(FleetNodeRole.TITAN).identity, enabled=False, authenticated=False
    )
    node = registration(FleetNodeRole.TITAN, identity=identity, enabled=False)
    _, router, health = fleet(node)
    with pytest.raises(NoEligibleFleetNodeError):
        router.route(routing_request(), health, decided_at=NOW)


def test_titan_local_first_and_load_aware_stable_tie_breaking() -> None:
    titan = registration(FleetNodeRole.TITAN)
    mac = registration(FleetNodeRole.MAC)
    prime = registration(FleetNodeRole.PRIME)
    _, router, health = fleet(titan, mac, prime)
    first = router.route(routing_request(), health, decided_at=NOW)
    second = router.route(routing_request(), health, decided_at=NOW)
    assert first == second
    assert first.selected_node_id == "node-titan"
    assert first.execution_authorized is False and first.broker_submission is False


def test_mac_escalation_for_high_cpu_and_prime_backup() -> None:
    titan = registration(FleetNodeRole.TITAN)
    mac = registration(FleetNodeRole.MAC)
    prime = registration(
        FleetNodeRole.PRIME, cpu_class=CPUClass.HIGH, memory_class=MemoryClass.LARGE
    )
    _, router, health = fleet(titan, mac, prime)
    heavy = routing_request(maximum_memory_class=MemoryClass.LARGE, minimum_cpu_class=CPUClass.HIGH)
    assert router.route(heavy, health, decided_at=NOW).selected_node_id == "node-mac"
    health["node-mac"] = heartbeat(mac, state=FleetNodeState.UNAVAILABLE)
    assert router.route(heavy, health, decided_at=NOW).selected_node_id == "node-prime"


@pytest.mark.parametrize(
    "field,value",
    [("required_model_id", "wrong-model"), ("required_tokenizer_id", "wrong-tokenizer")],
)
def test_exact_model_and_tokenizer_compatibility(field, value) -> None:
    titan = registration(FleetNodeRole.TITAN)
    _, router, health = fleet(titan)
    with pytest.raises(NoEligibleFleetNodeError):
        router.route(routing_request(**{field: value}), health, decided_at=NOW)


def test_vector_dimension_and_corpus_revision_compatibility() -> None:
    titan = registration(
        FleetNodeRole.TITAN, models=(model(FleetNodeRole.TITAN, Capability.SEMANTIC_RETRIEVAL),)
    )
    _, router, health = fleet(titan)
    request = routing_request(
        required_capability=Capability.SEMANTIC_RETRIEVAL,
        required_vector_dimension=768,
        required_corpus_revision=DIGEST,
    )
    assert router.route(request, health, decided_at=NOW).selected_node_id == "node-titan"
    with pytest.raises(NoEligibleFleetNodeError):
        router.route(replace(request, required_vector_dimension=1024), health, decided_at=NOW)


def test_exact_safe_ollama_model_path_is_preserved() -> None:
    value = FleetModelInventory(
        "ollama",
        "qllama/bge-small-en-v1.5:latest",
        None,
        frozenset({Capability.SEMANTIC_RETRIEVAL}),
        384,
        DIGEST,
    )
    assert value.model_id == "qllama/bge-small-en-v1.5:latest"
    with pytest.raises(FleetValidationError):
        replace(value, model_id="../../unsafe")


def test_privacy_trust_maintenance_draining_and_resource_filters() -> None:
    titan = registration(FleetNodeRole.TITAN)
    for changed in (
        replace(titan, maintenance=True),
        replace(titan, draining=True),
        replace(titan, resource_enforcement_verified=False),
    ):
        _, candidate_router, candidate_health = fleet(changed)
        request = (
            routing_request(maximum_memory_class=MemoryClass.LARGE)
            if not changed.resource_enforcement_verified
            else routing_request()
        )
        with pytest.raises(NoEligibleFleetNodeError):
            candidate_router.route(request, candidate_health, decided_at=NOW)
    restricted_identity = replace(
        titan.identity, privacy_tier=PrivacyTier.GOVERNED_REMOTE, trust_tier=TrustTier.RESTRICTED
    )
    restricted = replace(titan, identity=restricted_identity)
    _, restricted_router, restricted_health = fleet(restricted)
    with pytest.raises(NoEligibleFleetNodeError):
        restricted_router.route(routing_request(), restricted_health, decided_at=NOW)


def test_stale_future_clock_skew_and_spoofed_health_are_rejected() -> None:
    titan = registration(FleetNodeRole.TITAN)
    _, router, _ = fleet(titan)
    variants = (
        heartbeat(
            titan,
            observed_at="2026-08-01T17:50:00+00:00",
            node_timestamp="2026-08-01T17:50:00+00:00",
        ),
        heartbeat(
            titan,
            observed_at="2026-08-01T18:01:00+00:00",
            node_timestamp="2026-08-01T18:01:00+00:00",
        ),
        heartbeat(titan, node_timestamp="2026-08-01T17:55:00+00:00"),
        heartbeat(titan, authenticated_identity_ref="identity-ref:spoof"),
    )
    for variant in variants:
        with pytest.raises(NoEligibleFleetNodeError):
            router.route(routing_request(), {titan.identity.node_id: variant}, decided_at=NOW)


def test_transport_success_replay_integrity_and_spoof_rejection() -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, router, health = fleet(titan)
    adapter = FakeAdapter(titan)
    transport = GovernedFleetTransport(registry, {titan.identity.node_id: adapter})
    decision = router.route(routing_request(), health, decided_at=NOW)
    assert decision.selected_node_id == titan.identity.node_id
    assert transport.adapters[titan.identity.node_id] is adapter
    with pytest.raises(FleetValidationError):
        registry.authenticate(titan.identity.node_id, "wrong")


def test_coordinator_dispatch_failover_and_evidence(tmp_path: Path) -> None:
    titan = registration(FleetNodeRole.TITAN)
    mac = registration(FleetNodeRole.MAC)
    registry, router, health = fleet(titan, mac)
    transport = GovernedFleetTransport(
        registry, {"node-titan": FakeAdapter(titan, "failed"), "node-mac": FakeAdapter(mac)}
    )
    store = DurableFleetStore(tmp_path.resolve())
    decision, result = FleetExecutionCoordinator(router, transport, store).execute(
        replace(routing_request(), fallback_permission=True), health, completed_at=NOW
    )
    assert decision.selected_node_id == "node-mac"
    assert result is not None and result.state == RemoteTaskState.SUCCEEDED
    assert "failover" in {item.event_type for item in store.read()}


@pytest.mark.parametrize("mode", ("corrupt", "spoof"))
def test_transport_rejects_digest_mismatch_and_unauthenticated_response(
    tmp_path: Path, mode: str
) -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, router, health = fleet(titan)
    coordinator = FleetExecutionCoordinator(
        router,
        GovernedFleetTransport(registry, {"node-titan": FakeAdapter(titan, mode)}),
        DurableFleetStore(tmp_path.resolve()),
    )
    with pytest.raises(FleetValidationError):
        coordinator.execute(routing_request(), health, completed_at=NOW)


def test_completion_unknown_is_preserved_without_automatic_duplicate(tmp_path: Path) -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, router, health = fleet(titan)
    adapter = FakeAdapter(titan, "timeout")
    store = DurableFleetStore(tmp_path.resolve())
    decision, result = FleetExecutionCoordinator(
        router, GovernedFleetTransport(registry, {"node-titan": adapter}), store
    ).execute(replace(routing_request(), fallback_permission=True), health, completed_at=NOW)
    assert decision.selected_node_id == "node-titan" and result is None
    assert len(adapter.tasks) == 1
    assert store.read()[-1].state == RemoteTaskState.COMPLETION_UNKNOWN.value


def test_store_restart_truncation_corruption_and_duplicate(tmp_path: Path) -> None:
    store = DurableFleetStore(tmp_path.resolve())
    item = fleet_evidence(
        "heartbeat",
        "node-titan",
        node_id="node-titan",
        input_value=DIGEST,
        output_value="healthy",
        state="healthy",
        created_at=NOW,
    )
    store.append(item)
    assert DurableFleetStore(tmp_path.resolve()).read() == (item,)
    with pytest.raises(FleetConflictError):
        store.append(item)
    with store.path.open("ab") as stream:
        stream.write(b'{"truncated":')
    with pytest.raises(FleetStoreError):
        store.read(recover_truncated_tail=False)
    assert store.read() == (item,)
    store.path.write_text(store.path.read_text().replace('"store_version":1', '"store_version":2'))
    with pytest.raises(FleetStoreError):
        store.read(recover_truncated_tail=False)


def test_registry_persists_and_recovers_authenticated_nodes(tmp_path: Path) -> None:
    store = DurableFleetStore(tmp_path.resolve())
    original = FleetRegistry((registration(FleetNodeRole.TITAN), registration(FleetNodeRole.MAC)))
    original.persist(store, registered_at=NOW)
    recovered = FleetRegistry.recover(DurableFleetStore(tmp_path.resolve()))
    assert recovered.registrations == original.registrations
    assert recovered.revision == original.revision
    with pytest.raises(FleetConflictError):
        original.persist(store, registered_at=NOW)


def test_cancellation_is_exact_and_evidence_is_retained(tmp_path: Path) -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, router, _ = fleet(titan)
    adapter = FakeAdapter(titan)
    coordinator = FleetExecutionCoordinator(
        router,
        GovernedFleetTransport(registry, {"node-titan": adapter}),
        DurableFleetStore(tmp_path.resolve()),
    )
    from sigil.ai import GovernedRemoteTask

    task = GovernedRemoteTask(
        "remote-task-cancel",
        "fleet-request-001",
        "orchestration-fleet-001",
        "orchestration-step-" + "c" * 64,
        "node-titan",
        WorkerTaskType.RESEARCH_PREPARATION,
        Capability.REASONING,
        (DIGEST,),
        "sigil.ai.output.remote-specialist.v1",
        1000,
        MemoryClass.SMALL,
        CPUClass.LIGHT,
        100,
        100,
        PrivacyTier.LOCAL_ONLY,
        TrustTier.TRUSTED,
        "cancel-exact-001",
        NOW,
    )
    assert (
        coordinator.cancel(task, authenticated_identity_ref="identity-ref:titan", cancelled_at=NOW)
        == RemoteTaskState.CANCELLED
    )
    assert [item.event_type for item in coordinator.store.read()] == [
        "cancellation_requested",
        "cancellation_reconciled",
    ]
    with pytest.raises(FleetConflictError):
        coordinator.cancel(task, authenticated_identity_ref="identity-ref:titan", cancelled_at=NOW)


def test_phase8_adapter_returns_bounded_step_result(tmp_path: Path) -> None:
    titan = registration(FleetNodeRole.TITAN)
    registry, router, health = fleet(titan)
    coordinator = FleetExecutionCoordinator(
        router,
        GovernedFleetTransport(registry, {"node-titan": FakeAdapter(titan)}),
        DurableFleetStore(tmp_path.resolve()),
    )
    orchestration_request = GovernedOrchestrationRequest(
        "orchestration-fleet-001",
        "fleet-task-001",
        ORCHESTRATION_WORKFLOW,
        "Synthesize governed evidence for operator review.",
        frozenset({Capability.REASONING}),
        frozenset({Responsibility.RESEARCH_ANALYSIS}),
        (DIGEST,),
        PrivacyTier.LOCAL_ONLY,
        TrustTier.TRUSTED,
        CostClass.FREE,
        5000,
        1,
        1,
        False,
        False,
        NOW,
    )
    step = build_orchestration_plan(
        orchestration_request, registry_revision=REGISTRY_DIGEST, created_at=NOW
    ).steps[0]
    adapter = FleetSpecialistStepExecutor(
        coordinator,
        health,
        lambda step, request, attempt: routing_request(
            step_id=step.step_id,
            required_capability=step.capability,
            responsibility=step.responsibility,
        ),
    )
    result = adapter.execute(step, orchestration_request, attempt=1, completed_at=NOW)
    assert result.status == OrchestrationStepStatus.SUCCEEDED
    assert result.broker_submission is False and result.execution_authorized is False


def test_fleet_disabled_or_empty_is_startup_independent(tmp_path: Path) -> None:
    registry = FleetRegistry(())
    assert registry.registrations == ()
    assert DurableFleetStore(tmp_path.resolve()).read() == ()
    with pytest.raises(NoEligibleFleetNodeError):
        GovernedFleetRouter(registry).route(routing_request(), {}, decided_at=NOW)
    assert (
        ai_status({"SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve())})["fleet"]["health"]
        == "disabled"
    )


def test_fleet_inspection_is_sanitized_bounded_and_read_only(tmp_path: Path) -> None:
    store = DurableFleetStore(tmp_path.resolve())
    store.append(
        fleet_evidence(
            "node_registration",
            "node-titan",
            node_id="node-titan",
            input_value=DIGEST,
            output_value="registered",
            state="registered",
            created_at=NOW,
            details={"role": "titan"},
        )
    )
    store.append(
        fleet_evidence(
            "heartbeat",
            "node-titan",
            node_id="node-titan",
            input_value=DIGEST,
            output_value="healthy",
            state="healthy",
            created_at=NOW,
            details={"role": "titan", "capabilities": "reasoning.v1", "load": "10"},
        )
    )
    status = ai_status(
        {
            "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
            "SIGIL_AI_FLEET_ENABLED": "true",
            "SECRET_TOKEN": "must-not-appear",
        }
    )
    assert status["fleet"]["registered_node_count"] == 1
    assert status["fleet"]["healthy_node_count"] == 1
    assert status["fleet"]["nodes"]["titan"]["capabilities"] == ["reasoning.v1"]
    assert status["fleet"]["execution_authorized"] is False
    assert status["fleet"]["broker_submission"] is False
    assert "must-not-appear" not in str(status)
