from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.health import HealthReport, LivenessState, ReadinessState
from hermes_cli.prime.identity import FleetIdentity, IdentityKind, IdentitySource
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
def visibility(
    mission_control: MissionControlService, evidence_store: PrimeEvidenceStore
) -> PrimeVisibilityService:
    return PrimeVisibilityService(mission_control, evidence_store)


def test_publish_identity_appends_event_and_evidence(
    visibility: PrimeVisibilityService,
    mission_control: MissionControlService,
    evidence_store: PrimeEvidenceStore,
) -> None:
    identity = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="titan-01",
        source=IdentitySource.NATIVE,
        source_reference="native:titan-01",
        registered_at=_now(),
    )
    event, evidence = visibility.publish_identity("proj1", identity)
    assert event.event_type == "prime_identity_registered"
    stored_events = mission_control.get_events("proj1")
    assert len(stored_events) == 1
    assert stored_events[0].event_id == event.event_id
    assert evidence_store.verify_chain()
    assert len(evidence_store.read_all()) == 1


def test_publish_is_idempotent_on_repeat(
    visibility: PrimeVisibilityService,
    mission_control: MissionControlService,
) -> None:
    identity = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="titan-01",
        source=IdentitySource.NATIVE,
        source_reference="native:titan-01",
        registered_at=_now(),
    )
    visibility.publish_identity("proj1", identity)
    visibility.publish_identity("proj1", identity)
    assert len(mission_control.get_events("proj1")) == 1


def test_publish_admission_denial_is_visible_with_warning_severity(
    visibility: PrimeVisibilityService,
    mission_control: MissionControlService,
) -> None:
    now = _now()
    decision = AdmissionDecision(
        decision_id="padm_1",
        request_id="req1",
        subject_identity_id="fid_node_x",
        outcome=AdmissionOutcome.DENIED,
        reason_codes=("missing_health_report",),
        policy_version="prime-admission-policy-v1",
        decided_at=now,
        revalidate_after=now + 300,
    )
    event, _ = visibility.publish_admission("proj1", decision)
    assert event.severity == "warning"
    assert event.payload["decision"]["outcome"] == "denied"


def test_unknown_event_type_is_rejected_by_mission_control() -> None:
    """Arbitrary, unregistered event types can never enter the trusted
    event stream — this is the existing mission_control guarantee that
    Prime's new event types were added to, not bypassed."""
    with pytest.raises(ValidationError):
        TelemetryEvent(
            event_id="tevt_bad",
            event_type="prime_totally_made_up_event",
            project_id="proj1",
        )


def test_unsupported_schema_version_is_rejected_by_mission_control() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent(
            event_id="tevt_bad2",
            event_type="prime_identity_registered",
            project_id="proj1",
            schema_version=99,
        )


def test_arbitrary_dict_cannot_become_a_trusted_event() -> None:
    """A plain dict payload must be validated through TelemetryEvent's
    pydantic model before it can enter the mission_control journal —
    unknown top-level fields are simply dropped by TelemetryEvent's own
    permissive BaseModel unless explicitly modeled, and unknown event types
    are rejected outright as shown above. Prime never writes to the
    mission_control journal file directly."""
    with pytest.raises(ValidationError):
        TelemetryEvent(**{
            "event_type": "prime_identity_registered"
        })  # missing required fields
