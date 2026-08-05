from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.remote_maintenance import (
    ApprovalScope,
    CommandMode,
    RepairApproval,
    RepairProposal,
    RepairStep,
    RiskLevel,
)
from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.health import HealthReport, LivenessState, ReadinessState
from hermes_cli.prime.remote_maintenance_governance import (
    ApprovalRevocation,
    GovernedMaintenanceRequest,
    MaintenanceOutcome,
    MaintenanceWindow,
    evaluate_maintenance_request,
    missing_approval_scopes,
)


def _now() -> int:
    return int(time.time())


def _admitted(now: int, subject: str) -> AdmissionDecision:
    return AdmissionDecision(
        decision_id=f"padm_{subject}",
        request_id="req1",
        subject_identity_id=subject,
        outcome=AdmissionOutcome.ADMITTED,
        reason_codes=(),
        policy_version="prime-admission-policy-v1",
        decided_at=now,
        revalidate_after=now + 300,
    )


def _healthy(now: int, subject: str) -> HealthReport:
    return HealthReport(
        report_id=f"health_{subject}",
        subject_identity_id=subject,
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )


def _proposal() -> RepairProposal:
    step = RepairStep(
        step_id="s1",
        command_id="restart_tailscale",
        mode=CommandMode.CONNECTIVITY,
        required_approvals=(ApprovalScope.RESTART_TAILSCALE,),
        rollback_command_id="restart_tailscale",
    )
    return RepairProposal.build(
        target_id="hydra-live-01",
        risk=RiskLevel.LOW,
        expected_downtime="PT1M",
        finding_refs=(),
        steps=(step,),
        evidence_refs=(),
    )


def _approval(proposal: RepairProposal, now: int) -> RepairApproval:
    return RepairApproval(
        approval_id="a1",
        proposal_id=proposal.proposal_id,
        proposal_checksum=proposal.checksum,
        scopes=(ApprovalScope.RESTART_TAILSCALE,),
        actor_id="operator",
        approved_at=now,
        reason="routine",
    )


def _request(now: int, **overrides) -> GovernedMaintenanceRequest:
    proposal = overrides.pop("proposal", None) or _proposal()
    approvals = overrides.pop("approvals", None)
    approval_issued_at = overrides.pop("approval_issued_at", None)
    if approvals is None:
        approval = _approval(proposal, now)
        approvals = (approval,)
        approval_issued_at = (now,)
    fields = dict(
        request_id="mreq1",
        correlation_id="corr1",
        requester_identity_id="hermes-fleet",
        target_identity_id="hydra-live-01",
        proposal=proposal,
        approvals=approvals,
        approval_issued_at=approval_issued_at if approval_issued_at is not None else (),
        window=MaintenanceWindow(starts_at=now - 100, ends_at=now + 100),
        requested_at=now,
    )
    fields.update(overrides)
    return GovernedMaintenanceRequest(**fields)


def test_fully_valid_request_is_admitted() -> None:
    now = _now()
    request = _request(now)
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.ADMITTED


def test_no_approvals_denies_by_default() -> None:
    now = _now()
    request = _request(now, approvals=(), approval_issued_at=())
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "missing_required_approval_scopes" in decision.reason_codes


def test_expired_approval_denies() -> None:
    now = _now()
    proposal = _proposal()
    approval = _approval(proposal, now)
    request = _request(
        now,
        proposal=proposal,
        approvals=(approval,),
        approval_issued_at=(now - 100_000,),  # far older than default max age
    )
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "approval_expired" in decision.reason_codes


def test_outside_maintenance_window_denies() -> None:
    now = _now()
    request = _request(
        now, window=MaintenanceWindow(starts_at=now - 1000, ends_at=now - 500)
    )
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "outside_maintenance_window" in decision.reason_codes


def test_revoked_approval_denies_even_with_matching_scopes() -> None:
    now = _now()
    proposal = _proposal()
    approval = _approval(proposal, now)
    revocation = ApprovalRevocation(
        approval_id=approval.approval_id,
        revoked_at=now,
        revoked_by="security-team",
        reason="operator credential compromised",
    )
    request = _request(
        now,
        proposal=proposal,
        approvals=(approval,),
        approval_issued_at=(now,),
        revocations=(revocation,),
    )
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "approval_revoked" in decision.reason_codes


def test_requester_not_admitted_denies() -> None:
    now = _now()
    request = _request(now)
    decision = evaluate_maintenance_request(
        request,
        requester_admission=None,
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "requester_not_admitted" in decision.reason_codes


def test_target_health_not_usable_denies() -> None:
    now = _now()
    request = _request(now)
    stale = HealthReport(
        report_id="health_stale",
        subject_identity_id="hydra-live-01",
        observed_at=now - 10_000,
        expires_at=now + 100_000,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=stale,
        now=now,
    )
    assert decision.outcome == MaintenanceOutcome.DENIED
    assert "target_health_not_usable" in decision.reason_codes


def test_missing_approval_scopes_mirrors_executor_matching_semantics() -> None:
    proposal = _proposal()
    now = _now()
    wrong_checksum_approval = RepairApproval(
        approval_id="a2",
        proposal_id=proposal.proposal_id,
        proposal_checksum="0" * 64,  # does not match proposal.checksum
        scopes=(ApprovalScope.RESTART_TAILSCALE,),
        actor_id="operator",
        approved_at=now,
        reason="routine",
    )
    missing = missing_approval_scopes(proposal, (wrong_checksum_approval,))
    assert ApprovalScope.RESTART_TAILSCALE in missing


def test_window_requires_end_after_start() -> None:
    with pytest.raises(ValidationError):
        MaintenanceWindow(starts_at=100, ends_at=100)


def test_decision_grants_no_execution_authority_marker() -> None:
    now = _now()
    request = _request(now)
    decision = evaluate_maintenance_request(
        request,
        requester_admission=_admitted(now, "hermes-fleet"),
        requester_health=_healthy(now, "hermes-fleet"),
        target_admission=_admitted(now, "hydra-live-01"),
        target_health=_healthy(now, "hydra-live-01"),
        now=now,
    )
    assert decision.grants_no_execution_authority() is None
    assert not hasattr(decision, "executed")
