from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.desktop_governance import (
    DesktopUseOutcome,
    DesktopUsePolicy,
    DesktopUseRejectionCode,
    DesktopUseRequest,
    evaluate_and_publish_desktop_use,
    evaluate_desktop_use_request,
)
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.operator_approval import (
    ApprovalChannel,
    OperatorApproval,
    OperatorApprovalReplayStore,
    OperatorApprovalScope,
)


def _now() -> int:
    return int(time.time())


def _runtime(tmp_path: Path) -> FleetRuntime:
    return FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="desktop-test",
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )


def _register_mac(runtime: FleetRuntime, *, now: int) -> None:
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="req-mac",
            natural_key="mac",
            role=FleetNodeRole.MAC,
            declared_capabilities=("desktop_use",),
            endpoint="http://mac.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key="mac",
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )


def _policy(**overrides) -> DesktopUsePolicy:
    fields = dict(allowed_apps=("Finder", "Safari"), max_timeout_seconds=30)
    fields.update(overrides)
    return DesktopUsePolicy(**fields)


def _request(**overrides) -> DesktopUseRequest:
    fields = dict(request_id="req-1", action="click", app="Finder", requested_at=_now())
    fields.update(overrides)
    return DesktopUseRequest(**fields)


def _grant_approval(runtime: FleetRuntime, request: DesktopUseRequest, *, now: int) -> OperatorApproval:
    node = runtime.get_node("mac")
    return OperatorApproval.grant(
        scope=OperatorApprovalScope.DESKTOP_USE,
        action_id=request.action_id,
        subject_identity_id=node.identity_id,
        operator_identity="telegram:99999",
        channel=ApprovalChannel.TELEGRAM,
        granted_at=now,
        evidence_ref="evidence://desktop-approval",
    )


def test_unknown_action_is_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        _request(action="delete_everything")


def test_capture_never_requires_approval_and_is_admitted_when_node_is_healthy(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    decision = evaluate_desktop_use_request(
        _request(action="capture", app=None),
        _policy(),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.ADMITTED


def test_capture_is_denied_when_node_is_not_dispatchable(tmp_path) -> None:
    runtime = _runtime(tmp_path)  # mac never registered
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    decision = evaluate_desktop_use_request(
        _request(action="capture", app=None),
        _policy(),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=_now(),
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.NODE_NOT_DISPATCHABLE


def test_click_without_approval_is_denied(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    decision = evaluate_desktop_use_request(
        _request(action="click", app="Finder"),
        _policy(),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.APPROVAL_REQUIRED


def test_click_with_matching_fresh_approval_is_admitted(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    request = _request(action="click", app="Finder")
    approval = _grant_approval(runtime, request, now=now)

    decision = evaluate_desktop_use_request(
        request.model_copy(update={"operator_approval": approval}),
        _policy(),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.ADMITTED


def test_approval_cannot_be_reused_across_two_evaluations(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    request = _request(action="click", app="Finder")
    approval = _grant_approval(runtime, request, now=now)
    gated_request = request.model_copy(update={"operator_approval": approval})

    first = evaluate_desktop_use_request(
        gated_request, _policy(), fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now,
    )
    assert first.outcome == DesktopUseOutcome.ADMITTED

    second = evaluate_desktop_use_request(
        gated_request, _policy(), fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now + 1,
    )
    assert second.outcome == DesktopUseOutcome.DENIED
    assert second.rejection_code == DesktopUseRejectionCode.APPROVAL_INVALID
    assert second.approval_detail == "replayed"


def test_approval_for_a_different_action_does_not_authorize_this_one(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    approved_for_click = _grant_approval(runtime, _request(action="click", app="Finder"), now=now)
    attempted_type_request = _request(action="type", app="Finder").model_copy(
        update={"operator_approval": approved_for_click}
    )

    decision = evaluate_desktop_use_request(
        attempted_type_request, _policy(), fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.APPROVAL_INVALID
    assert decision.approval_detail == "action_mismatch"


def test_app_outside_workspace_scope_is_denied_even_with_approval(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    request = _request(action="click", app="System Preferences")
    approval = _grant_approval(runtime, request, now=now)
    decision = evaluate_desktop_use_request(
        request.model_copy(update={"operator_approval": approval}),
        _policy(),  # allowed_apps = ("Finder", "Safari") — System Preferences not included
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.APP_NOT_IN_WORKSPACE_SCOPE


def test_timeout_beyond_policy_bound_is_denied(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    decision = evaluate_desktop_use_request(
        _request(action="capture", app=None, timeout_seconds=60),
        _policy(max_timeout_seconds=30),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.TIMEOUT_OUT_OF_BOUNDS


def test_evaluate_and_publish_records_mission_control_telemetry(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_mac(runtime, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")

    decision = evaluate_and_publish_desktop_use(
        _request(action="capture", app=None),
        _policy(),
        fleet_runtime=runtime,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
        replay_store=replay_store,
        now=now,
    )
    assert decision.outcome == DesktopUseOutcome.ADMITTED
    events = runtime.visibility._mission_control.get_events("desktop-test")
    assert any(e.event_type == "prime_desktop_use_decided" for e in events)
