from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.service_registry import (
    EcosystemServiceRegistry,
    EcosystemServiceRegistryStore,
    ServiceRegistrationOutcome,
)
from hermes_cli.prime.visibility import PrimeVisibilityService


def _now() -> int:
    return int(time.time())


@pytest.fixture()
def mission_control(tmp_path: Path) -> MissionControlService:
    return MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))


@pytest.fixture()
def evidence_store(tmp_path: Path) -> PrimeEvidenceStore:
    return PrimeEvidenceStore(state_root=tmp_path / "evidence")


@pytest.fixture()
def visibility(mission_control, evidence_store) -> PrimeVisibilityService:
    return PrimeVisibilityService(mission_control, evidence_store)


@pytest.fixture()
def registry(tmp_path: Path) -> EcosystemServiceRegistry:
    return EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))


def _register_and_publish(registry, visibility, service_key, *, project_id, now):
    outcome, record, rejection = registry.register_known_service(service_key, now=now)
    visibility.publish_service_registration(
        project_id, service_key=service_key, outcome=outcome, record=record,
        rejection_code=rejection, now=now,
    )
    return outcome, record


def test_registered_service_appears_in_snapshot_as_present_disabled(
    registry, visibility, mission_control
) -> None:
    now = _now()
    _register_and_publish(registry, visibility, "paperclip", project_id="proj1", now=now)

    snapshot = mission_control.get_snapshot("proj1")
    entries = {s.service_key: s for s in snapshot.ecosystem_service_states}
    assert "paperclip" in entries
    entry = entries["paperclip"]
    assert entry.installation_status == "present_disabled"
    assert entry.certification_gate_met is False
    assert entry.dispatchable is False
    assert entry.is_operational() is False  # never represented as operational


def test_all_known_services_project_into_snapshot(registry, visibility, mission_control) -> None:
    from hermes_cli.prime.service_registry import KNOWN_ECOSYSTEM_SERVICES

    now = _now()
    for descriptor in KNOWN_ECOSYSTEM_SERVICES:
        _register_and_publish(registry, visibility, descriptor.service_key, project_id="proj1", now=now)

    snapshot = mission_control.get_snapshot("proj1")
    assert len(snapshot.ecosystem_service_states) == len(KNOWN_ECOSYSTEM_SERVICES)
    assert all(not s.is_operational() for s in snapshot.ecosystem_service_states)


def test_rejected_registration_does_not_appear_as_a_service_state(
    registry, visibility, mission_control
) -> None:
    now = _now()
    outcome, record, rejection = registry.register_known_service("not-a-real-service", now=now)
    visibility.publish_service_registration(
        "proj1", service_key="not-a-real-service", outcome=outcome, record=record,
        rejection_code=rejection, now=now,
    )
    assert outcome == ServiceRegistrationOutcome.REJECTED

    snapshot = mission_control.get_snapshot("proj1")
    assert all(s.service_key != "not-a-real-service" for s in snapshot.ecosystem_service_states)
    # But the rejection itself is still recorded as an event, for auditability.
    events = mission_control.get_events("proj1")
    assert any(e.event_type == "prime_service_registration_rejected" for e in events)


def test_revoked_service_state_is_reflected_after_reregistration_publish(
    registry, visibility, mission_control
) -> None:
    now = _now()
    _register_and_publish(registry, visibility, "buzznode", project_id="proj1", now=now)
    registry.revoke("buzznode", now=now, reason="superseded")
    # Re-publish a fresh view reflecting the now-revoked record (registry
    # itself is the source of truth; visibility re-publishes on request).
    record = registry.get("buzznode")
    visibility.publish_service_registration(
        "proj1", service_key="buzznode", outcome=ServiceRegistrationOutcome.UPDATED,
        record=record, now=now + 1,
    )
    snapshot = mission_control.get_snapshot("proj1")
    entry = next(s for s in snapshot.ecosystem_service_states if s.service_key == "buzznode")
    assert entry.revoked is True
    assert entry.is_operational() is False


def test_evidence_chain_remains_valid_after_service_registrations(
    registry, visibility, evidence_store
) -> None:
    from hermes_cli.prime.service_registry import KNOWN_ECOSYSTEM_SERVICES

    now = _now()
    for descriptor in KNOWN_ECOSYSTEM_SERVICES:
        _register_and_publish(registry, visibility, descriptor.service_key, project_id="proj1", now=now)
    assert evidence_store.verify_chain() is True
    assert len(evidence_store.read_all()) == len(KNOWN_ECOSYSTEM_SERVICES)
