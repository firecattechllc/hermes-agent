from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import AdmissionOutcome, CertificationStatus
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import (
    FleetNodeRegistrationRequest,
    FleetNodeRole,
    FleetRegistrationOutcome,
)
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatOutcome, HeartbeatSubmission


def _now() -> int:
    return int(time.time())


@pytest.fixture()
def runtime(tmp_path: Path) -> FleetRuntime:
    return FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="fleet-test",
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )


def _register(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int):
    return runtime.register_node(
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


def _heartbeat(runtime: FleetRuntime, natural_key: str, *, now: int, **overrides):
    fields = dict(
        natural_key=natural_key,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
        submitted_at=now,
    )
    fields.update(overrides)
    return runtime.ingest_heartbeat(HeartbeatSubmission(**fields), now=now)


def test_register_node_publishes_telemetry_and_evidence(runtime: FleetRuntime) -> None:
    now = _now()
    decision = _register(runtime, "titan", FleetNodeRole.TITAN, now=now)
    assert decision.outcome == FleetRegistrationOutcome.REGISTERED

    events = runtime.visibility._mission_control.get_events("fleet-test")
    types = [e.event_type for e in events]
    assert "prime_fleet_node_registered" in types
    assert runtime.visibility._evidence_store.verify_chain()


def test_rejected_registration_publishes_warning_telemetry(runtime: FleetRuntime) -> None:
    now = _now()
    decision = runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="req-bad",
            natural_key="unknown-attacker",
            role=FleetNodeRole.TITAN,
            endpoint="http://x.tailnet.internal",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    events = runtime.visibility._mission_control.get_events("fleet-test")
    rejected = [e for e in events if e.event_type == "prime_fleet_node_registration_rejected"]
    assert len(rejected) == 1
    assert rejected[0].severity == "warning"


def test_heartbeat_publishes_only_on_state_transition(runtime: FleetRuntime) -> None:
    now = _now()
    _register(runtime, "titan", FleetNodeRole.TITAN, now=now)

    _heartbeat(runtime, "titan", now=now)
    events_after_first = runtime.visibility._mission_control.get_events("fleet-test")
    connection_events = [
        e for e in events_after_first if e.event_type == "prime_fleet_node_connection_changed"
    ]
    assert len(connection_events) == 1  # UNKNOWN -> CONNECTED

    _heartbeat(runtime, "titan", now=now + 1)
    events_after_second = runtime.visibility._mission_control.get_events("fleet-test")
    connection_events_2 = [
        e for e in events_after_second if e.event_type == "prime_fleet_node_connection_changed"
    ]
    assert len(connection_events_2) == 1  # still just one — no transition occurred


def test_evaluate_admission_for_unknown_node_is_denied(runtime: FleetRuntime) -> None:
    now = _now()
    decision = runtime.evaluate_admission(
        "ghost",
        now=now,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://ref",
    )
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "identity_unknown_or_inactive" in decision.reason_codes


def test_is_dispatchable_requires_registration_heartbeat_and_certification(
    runtime: FleetRuntime,
) -> None:
    now = _now()
    assert (
        runtime.is_dispatchable(
            "titan", now=now, certification_status=CertificationStatus.CERTIFIED
        )
        is False
    )  # not even registered yet

    _register(runtime, "titan", FleetNodeRole.TITAN, now=now)
    assert (
        runtime.is_dispatchable(
            "titan", now=now, certification_status=CertificationStatus.CERTIFIED
        )
        is False
    )  # registered but never heartbeated

    _heartbeat(runtime, "titan", now=now)
    assert (
        runtime.is_dispatchable(
            "titan", now=now, certification_status=CertificationStatus.NOT_CERTIFIED
        )
        is False
    )  # healthy but not certified

    assert (
        runtime.is_dispatchable(
            "titan",
            now=now,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence://cert-ref",
        )
        is True
    )


def test_is_dispatchable_false_after_node_goes_stale(runtime: FleetRuntime) -> None:
    now = _now()
    _register(runtime, "titan", FleetNodeRole.TITAN, now=now)
    _heartbeat(runtime, "titan", now=now)
    assert (
        runtime.is_dispatchable(
            "titan",
            now=now,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence://cert-ref",
        )
        is True
    )

    much_later = now + 100_000
    assert (
        runtime.is_dispatchable(
            "titan",
            now=much_later,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence://cert-ref",
        )
        is False
    )


def test_is_dispatchable_false_for_revoked_node(runtime: FleetRuntime) -> None:
    now = _now()
    _register(runtime, "titan", FleetNodeRole.TITAN, now=now)
    _heartbeat(runtime, "titan", now=now)
    runtime.revoke_node("titan", now=now, reason="rotation")

    assert (
        runtime.is_dispatchable(
            "titan",
            now=now,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence://cert-ref",
        )
        is False
    )
    result = _heartbeat(runtime, "titan", now=now + 1)
    assert result.outcome == HeartbeatOutcome.REJECTED
