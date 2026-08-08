from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.health import (
    DependencyHealth,
    HealthFinding,
    HealthReport,
    LivenessState,
    QuarantineState,
    ReadinessState,
    evaluate_health,
    health_from_hermes_link_status,
    health_from_sigil_fleet_node_health,
    is_usable_for_admission,
)


def _now() -> int:
    return int(time.time())


def _fresh_report(**overrides) -> HealthReport:
    now = _now()
    fields = dict(
        report_id="health_1",
        subject_identity_id="fid_node_x",
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    fields.update(overrides)
    return HealthReport(**fields)


def test_fresh_healthy_report_has_no_findings() -> None:
    report = _fresh_report()
    assert evaluate_health(report, now=report.observed_at) == ()
    assert is_usable_for_admission(report, now=report.observed_at)


def test_missing_health_report_fails_closed() -> None:
    findings = evaluate_health(None, now=_now())
    assert findings == (HealthFinding.UNKNOWN_SUBJECT,)
    assert is_usable_for_admission(None, now=_now()) is False


def test_stale_health_fails_closed() -> None:
    now = _now()
    report = _fresh_report(observed_at=now - 10_000, expires_at=now + 100_000)
    findings = evaluate_health(report, now=now, max_age_seconds=180)
    assert HealthFinding.STALE in findings
    assert is_usable_for_admission(report, now=now) is False


def test_expired_health_fails_closed() -> None:
    now = _now()
    report = _fresh_report(observed_at=now - 500, expires_at=now - 10)
    findings = evaluate_health(report, now=now)
    assert HealthFinding.EXPIRED in findings
    assert is_usable_for_admission(report, now=now) is False


def test_unsupported_protocol_version_fails_safely_at_construction() -> None:
    """Matches the mission_control TelemetryEvent convention: an unsupported
    schema/protocol version is rejected at the earliest possible point
    (construction), rather than being allowed to exist and only caught
    later. evaluate_health additionally re-checks the version dynamically
    (see its docstring) as defense in depth for future multi-version
    scenarios, but today the two checks necessarily agree."""
    with pytest.raises(ValidationError):
        _fresh_report(protocol_version=99)


def test_quarantined_report_is_never_usable_even_if_otherwise_healthy() -> None:
    report = _fresh_report(quarantine=QuarantineState.QUARANTINED)
    assert HealthFinding.QUARANTINED in evaluate_health(report, now=report.observed_at)
    assert is_usable_for_admission(report, now=report.observed_at) is False


def test_not_alive_fails_closed() -> None:
    report = _fresh_report(liveness=LivenessState.DEAD)
    assert HealthFinding.NOT_ALIVE in evaluate_health(report, now=report.observed_at)


def test_not_ready_fails_closed() -> None:
    report = _fresh_report(readiness=ReadinessState.NOT_READY)
    assert HealthFinding.NOT_READY in evaluate_health(report, now=report.observed_at)


def test_healthy_state_alone_is_not_authority() -> None:
    """A perfectly healthy report carries no authority-related fields at all."""
    report = _fresh_report()
    assert not hasattr(report, "admitted")
    assert not hasattr(report, "execution_authorized")
    assert not hasattr(report, "authorized")


def test_expiry_before_observation_is_rejected_at_construction() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        HealthReport(
            report_id="health_bad",
            subject_identity_id="fid_node_x",
            observed_at=now,
            expires_at=now - 1,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
        )


class _FakeStatus:
    node_id = "mac-01"

    class _Presence:
        value = "online"

    class _Component:
        def __init__(self, value: str) -> None:
            self.value = value

    presence = _Presence()
    nursery_state = _Component("healthy")
    ollama_health = _Component("healthy")
    finbert_health = _Component("unknown")
    memory_index_health = _Component("degraded")
    degraded_components = ("finbert",)
    evidence_timestamp = 0


def test_adapter_from_hermes_link_status_never_maps_unknown_to_healthy() -> None:
    status = _FakeStatus()
    status.evidence_timestamp = _now()
    report = health_from_hermes_link_status(status, "fid_node_mac01")
    assert report.dependency_health["finbert"] == DependencyHealth.UNKNOWN
    assert report.readiness == ReadinessState.NOT_READY  # degraded_components non-empty


class _FakeNodeHealth:
    def __init__(
        self, freshness_value: str, maintenance: bool = False, draining: bool = False
    ) -> None:
        self._freshness_value = freshness_value
        self.maintenance = maintenance
        self.draining = draining

    def freshness(self, *, coordinator_time: str) -> str:
        return self._freshness_value


def test_adapter_from_sigil_fleet_node_health_stale_is_not_alive() -> None:
    node_health = _FakeNodeHealth("stale")
    now = _now()
    report = health_from_sigil_fleet_node_health(
        node_health,
        "fid_node_titan01",
        coordinator_time="2026-01-01T00:00:00+00:00",
        observed_at_epoch=now,
    )
    assert report.liveness == LivenessState.UNKNOWN
    assert report.readiness == ReadinessState.NOT_READY
    assert report.reason_codes == ("stale",)


def test_adapter_from_sigil_fleet_node_health_current_is_ready() -> None:
    node_health = _FakeNodeHealth("current")
    now = _now()
    report = health_from_sigil_fleet_node_health(
        node_health,
        "fid_node_titan01",
        coordinator_time="2026-01-01T00:00:00+00:00",
        observed_at_epoch=now,
    )
    assert report.liveness == LivenessState.ALIVE
    assert report.readiness == ReadinessState.READY
