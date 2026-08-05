from __future__ import annotations

import time

import pytest

from hermes_cli.prime.fleet_registry import (
    FleetNodeConnectionState,
    FleetNodeRegistrationRequest,
    FleetNodeRegistry,
    FleetNodeRole,
    FleetRegistryStore,
)
from hermes_cli.prime.health import (
    DEFAULT_MAX_REPORT_AGE_SECONDS,
    DependencyHealth,
    HealthCheck,
    LivenessState,
    ReadinessState,
)
from hermes_cli.prime.heartbeat import (
    HealthReportStore,
    HeartbeatOutcome,
    HeartbeatRejectionCode,
    HeartbeatService,
    HeartbeatSubmission,
)


def _now() -> int:
    return int(time.time())


def _setup(tmp_path):
    state_root = tmp_path / "prime"
    registry = FleetNodeRegistry(store=FleetRegistryStore(state_root=state_root))
    registry.register(
        FleetNodeRegistrationRequest(
            request_id="req-1",
            natural_key="titan",
            role=FleetNodeRole.TITAN,
            declared_capabilities=("worker_heartbeat",),
            endpoint="http://titan.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=_now(),
        ),
        now=_now(),
    )
    heartbeats = HeartbeatService(
        registry, health_store=HealthReportStore(state_root=state_root)
    )
    return registry, heartbeats


def _healthy_submission(**overrides) -> HeartbeatSubmission:
    fields = dict(
        natural_key="titan",
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
        submitted_at=_now(),
    )
    fields.update(overrides)
    return HeartbeatSubmission(**fields)


def test_healthy_heartbeat_transitions_to_connected(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    result = heartbeats.ingest(_healthy_submission(submitted_at=now), now=now)
    assert result.outcome == HeartbeatOutcome.ACCEPTED
    assert result.connection_state == FleetNodeConnectionState.CONNECTED
    assert result.previous_connection_state == FleetNodeConnectionState.UNKNOWN
    assert result.transitioned is True
    assert heartbeats.is_usable_for_dispatch("titan", now=now) is True


def test_unknown_node_heartbeat_is_rejected(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    submission = HeartbeatSubmission(
        natural_key="ghost-node",
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
        submitted_at=now,
    )
    result = heartbeats.ingest(submission, now=now)
    assert result.outcome == HeartbeatOutcome.REJECTED
    assert result.rejection_code == HeartbeatRejectionCode.UNKNOWN_NODE
    assert heartbeats.is_usable_for_dispatch("ghost-node", now=now) is False


def test_revoked_node_heartbeat_is_rejected(tmp_path) -> None:
    registry, heartbeats = _setup(tmp_path)
    now = _now()
    heartbeats.ingest(_healthy_submission(submitted_at=now), now=now)
    registry.revoke("titan", now=now, reason="compromised")

    result = heartbeats.ingest(_healthy_submission(submitted_at=now + 5), now=now + 5)
    assert result.outcome == HeartbeatOutcome.REJECTED
    assert result.rejection_code == HeartbeatRejectionCode.NODE_REVOKED
    assert heartbeats.is_usable_for_dispatch("titan", now=now + 5) is False


def test_future_submission_is_rejected(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    result = heartbeats.ingest(_healthy_submission(submitted_at=now + 1_000_000), now=now)
    assert result.outcome == HeartbeatOutcome.REJECTED
    assert result.rejection_code == HeartbeatRejectionCode.SUBMITTED_IN_FUTURE


def test_node_ages_into_stale_without_a_new_heartbeat(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    heartbeats.ingest(_healthy_submission(submitted_at=now), now=now)
    assert heartbeats.current_connection_state("titan", now=now) == FleetNodeConnectionState.CONNECTED

    later = now + DEFAULT_MAX_REPORT_AGE_SECONDS + 1
    assert heartbeats.current_connection_state("titan", now=later) == FleetNodeConnectionState.STALE
    assert heartbeats.is_usable_for_dispatch("titan", now=later) is False


def test_not_alive_heartbeat_is_disconnected(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    result = heartbeats.ingest(
        _healthy_submission(
            liveness=LivenessState.DEAD, readiness=ReadinessState.NOT_READY, submitted_at=now
        ),
        now=now,
    )
    assert result.connection_state == FleetNodeConnectionState.DISCONNECTED
    assert heartbeats.is_usable_for_dispatch("titan", now=now) is False


def test_degraded_dependency_heartbeat_is_degraded(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    result = heartbeats.ingest(
        _healthy_submission(
            checks=(HealthCheck(check_id="ollama_reachable", passed=False),),
            submitted_at=now,
        ),
        now=now,
    )
    assert result.connection_state == FleetNodeConnectionState.DEGRADED
    assert heartbeats.is_usable_for_dispatch("titan", now=now) is False


def test_never_heartbeated_node_is_unknown_and_unusable(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    assert heartbeats.current_connection_state("titan", now=now) == FleetNodeConnectionState.UNKNOWN
    assert heartbeats.is_usable_for_dispatch("titan", now=now) is False


def test_model_inventory_is_updated_from_heartbeat(tmp_path) -> None:
    registry, heartbeats = _setup(tmp_path)
    now = _now()
    heartbeats.ingest(
        _healthy_submission(
            reported_model_inventory=("hermes-llama3.2:3b-64k", "finbert-local"),
            submitted_at=now,
        ),
        now=now,
    )
    record = registry.get("titan")
    assert record.model_inventory == ("hermes-llama3.2:3b-64k", "finbert-local")


def test_recovery_transitions_back_to_connected(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    now = _now()
    heartbeats.ingest(
        _healthy_submission(liveness=LivenessState.DEAD, submitted_at=now), now=now
    )
    recovered = heartbeats.ingest(_healthy_submission(submitted_at=now + 5), now=now + 5)
    assert recovered.previous_connection_state == FleetNodeConnectionState.DISCONNECTED
    assert recovered.connection_state == FleetNodeConnectionState.CONNECTED
    assert recovered.transitioned is True


def test_clock_is_fully_injectable_and_deterministic(tmp_path) -> None:
    _, heartbeats = _setup(tmp_path)
    fixed_now = 1_700_000_000
    result = heartbeats.ingest(_healthy_submission(submitted_at=fixed_now), now=fixed_now)
    assert result.decided_at == fixed_now
    assert heartbeats.latest_health("titan").observed_at == fixed_now
