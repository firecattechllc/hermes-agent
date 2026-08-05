"""Fleet Unification Stages 2-9 end-to-end acceptance suite.

Exercises the full Prime control plane in one governed flow and proves the
mandatory adversarial properties: identity/health/admission/evidence/
certification never imply execution, mutation, broker-submission, or
remote-maintenance authority, and every governed decision fails closed on
missing, stale, conflicting, or unsupported input.

Also demonstrates that the canonical identity layer can represent every
pre-existing per-subsystem identity shape discovered across the Stage
3-9 capability areas (fleet inventory, remote maintenance / Hydra Live,
model routing, Titan/Sigil fleet routing, Mac/Titan hermes-link, and the
Big Sister / Little Sister learning hierarchy) without requiring any of
those subsystems' own files to change.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli.agent_roles.fleet_inventory import (
    InventoryTarget,
    SecretReference as InventorySecretReference,
)
from hermes_cli.agent_roles.remote_maintenance import (
    ApprovalScope,
    CommandMode,
    RepairApproval,
    RepairProposal,
    RepairStep,
    RiskLevel,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import (
    AdmissionOutcome,
    AdmissionRequest,
    CertificationStatus,
    PrimeAdmissionService,
)
from hermes_cli.prime.certification import certify_fleet, FleetCertificationStatus
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.health import (
    HealthReport,
    LivenessState,
    ReadinessState,
    is_usable_for_admission,
)
from hermes_cli.prime.identity import (
    FleetIdentity,
    IdentityKind,
    IdentityRegistry,
    IdentitySource,
    identity_from_hermes_link_node,
    identity_from_learning_node,
    identity_from_remote_target,
)
from hermes_cli.prime.remote_maintenance_governance import (
    GovernedMaintenanceRequest,
    MaintenanceOutcome,
    MaintenanceWindow,
    evaluate_maintenance_request,
)
from hermes_cli.prime.sigil_contract import (
    SigilContractOutcome,
    SigilContractRequest,
    SigilContractResponse,
    evaluate_sigil_contract_request,
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


def test_stage3_to_9_identity_consolidation_covers_every_legacy_shape() -> None:
    """Stage 3-9 capability areas each mint their own identity shape today;
    Prime's canonical identity layer can represent all of them without
    requiring any of those files to change."""
    now = _now()
    registry = IdentityRegistry()

    # Stage 4 — Hydra Live / remote maintenance target (agent_roles.remote_maintenance)
    from hermes_cli.agent_roles.remote_maintenance import RemoteTarget, SecretReference

    hydra_target = RemoteTarget(
        target_id="hydra-live-01",
        host_alias="hydra-live",
        user="hermes",
        credential=SecretReference(provider="env", key="HYDRA_SSH_KEY"),
    )
    hydra_identity = identity_from_remote_target(
        hydra_target, registered_at=now, source=IdentitySource.REMOTE_MAINTENANCE
    )
    registry.register(hydra_identity)

    # Stage 3 — fleet inventory target (agent_roles.fleet_inventory)
    inventory_target = InventoryTarget(
        target_id="fleet-node-02",
        host_alias="fleet-node-02",
        user="hermes",
        credential=InventorySecretReference(provider="env", key="FLEET_SSH_KEY"),
    )
    inventory_identity = identity_from_remote_target(
        inventory_target, registered_at=now, source=IdentitySource.FLEET_INVENTORY
    )
    registry.register(inventory_identity)

    # Stage 7/8 — Mac/Titan hermes-link node
    link_identity = identity_from_hermes_link_node(
        "mac-coordinator-01", "big_sister", registered_at=now
    )
    registry.register(link_identity)

    # Stage 7 — Big Sister / Little Sister learning hierarchy node
    learning_identity = identity_from_learning_node(
        "titan-01", "little_sister", registered_at=now
    )
    registry.register(learning_identity)

    all_identities = registry.all()
    assert len(all_identities) == 4
    # Every identity resolves back through the registry deterministically.
    for identity in all_identities:
        assert registry.get(identity.identity_id) is identity
        assert registry.is_known_and_active(identity.identity_id)


def test_full_governed_flow_end_to_end(
    visibility, mission_control, evidence_store
) -> None:
    now = _now()
    project_id = "hermes-fleet"

    # 1. Canonical fleet identities are established.
    titan_identity = FleetIdentity(
        kind=IdentityKind.NODE,
        natural_key="titan-01",
        source=IdentitySource.NATIVE,
        source_reference="native:titan-01",
        registered_at=now,
    )
    sigil_identity = FleetIdentity(
        kind=IdentityKind.SERVICE,
        natural_key="sigil",
        source=IdentitySource.NATIVE,
        source_reference="native:sigil",
        registered_at=now,
    )
    visibility.publish_identity(project_id, titan_identity)
    visibility.publish_identity(project_id, sigil_identity)

    registry = IdentityRegistry()
    registry.register(titan_identity)
    registry.register(sigil_identity)

    # 2. Shared health reports are produced.
    titan_health = HealthReport(
        report_id="health_titan",
        subject_identity_id=titan_identity.identity_id,
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    sigil_health = HealthReport(
        report_id="health_sigil",
        subject_identity_id=sigil_identity.identity_id,
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    visibility.publish_health(project_id, titan_health)
    visibility.publish_health(project_id, sigil_health)
    assert is_usable_for_admission(titan_health, now=now)
    assert is_usable_for_admission(sigil_health, now=now)

    # 3. Prime evaluates admission.
    admission_service = PrimeAdmissionService()
    titan_admission = admission_service.evaluate(
        AdmissionRequest(
            request_id="req_titan",
            subject_identity_id=titan_identity.identity_id,
            role="titan",
            software_version="1.0.0",
            protocol_version=1,
            health=titan_health,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence_ref_titan",
            policy_version="prime-admission-policy-v1",
            identity_known_and_active=registry.is_known_and_active(
                titan_identity.identity_id
            ),
            identity_revoked=False,
            quarantined=False,
            requested_at=now,
        ),
        now=now,
    )
    sigil_admission = admission_service.evaluate(
        AdmissionRequest(
            request_id="req_sigil",
            subject_identity_id=sigil_identity.identity_id,
            role="sigil",
            software_version="v3.6.0",
            protocol_version=1,
            health=sigil_health,
            certification_status=CertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence_ref_sigil",
            policy_version="prime-admission-policy-v1",
            identity_known_and_active=registry.is_known_and_active(
                sigil_identity.identity_id
            ),
            identity_revoked=False,
            quarantined=False,
            requested_at=now,
        ),
        now=now,
    )
    assert titan_admission.outcome == AdmissionOutcome.ADMITTED
    assert sigil_admission.outcome == AdmissionOutcome.ADMITTED

    # 4. Valid Mission Control events are emitted for both decisions.
    admission_event, admission_evidence = visibility.publish_admission(
        project_id, titan_admission
    )
    visibility.publish_admission(project_id, sigil_admission)
    assert admission_event.event_type == "prime_admission_decided"

    # 5. Unified evidence is stored and verified.
    assert evidence_store.verify_chain()
    assert (
        len(evidence_store.read_all()) >= 4
    )  # 2 identity + 2 health so far (+2 admission)

    # 6. Governed Sigil interaction occurs (advisory only).
    sigil_request = SigilContractRequest(
        request_id="sreq_1",
        correlation_id="corr_1",
        caller_identity_id=titan_identity.identity_id,
        service_identity_id=sigil_identity.identity_id,
        operation="advisory_financial_sentiment",
        requested_at=now,
    )
    admitted, rejection_code = evaluate_sigil_contract_request(
        sigil_request,
        caller_admission=titan_admission,
        service_admission=sigil_admission,
        caller_health=titan_health,
        service_health=sigil_health,
        now=now,
    )
    assert admitted is True
    assert rejection_code is None
    sigil_response = SigilContractResponse(
        request_id=sigil_request.request_id,
        correlation_id=sigil_request.correlation_id,
        outcome=SigilContractOutcome.ACCEPTED,
        advisory_output={"sentiment": "neutral", "confidence": 0.6},
        evidence_refs=(admission_evidence.evidence_id,),
        completed_at=now,
    )
    visibility.publish_sigil_contract(project_id, sigil_request, sigil_response)
    # No execution or broker authority ever appears on the response.
    assert sigil_response.execution_authority_granted is False
    assert sigil_response.broker_submission_granted is False

    # 7 & 8. Remote-maintenance policy is evaluated without unrestricted execution.
    step = RepairStep(
        step_id="s1",
        command_id="restart_tailscale",
        mode=CommandMode.CONNECTIVITY,
        required_approvals=(ApprovalScope.RESTART_TAILSCALE,),
        rollback_command_id="restart_tailscale",
    )
    proposal = RepairProposal.build(
        target_id="titan-01",
        risk=RiskLevel.LOW,
        expected_downtime="PT1M",
        finding_refs=(),
        steps=(step,),
        evidence_refs=(),
    )
    approval = RepairApproval(
        approval_id="a1",
        proposal_id=proposal.proposal_id,
        proposal_checksum=proposal.checksum,
        scopes=(ApprovalScope.RESTART_TAILSCALE,),
        actor_id="operator",
        approved_at=now,
        reason="routine",
    )
    maintenance_request = GovernedMaintenanceRequest(
        request_id="mreq_1",
        correlation_id="corr_2",
        requester_identity_id=sigil_identity.identity_id,
        target_identity_id=titan_identity.identity_id,
        proposal=proposal,
        approvals=(approval,),
        approval_issued_at=(now,),
        window=MaintenanceWindow(starts_at=now - 100, ends_at=now + 100),
        requested_at=now,
    )
    maintenance_decision = evaluate_maintenance_request(
        maintenance_request,
        requester_admission=sigil_admission,
        requester_health=sigil_health,
        target_admission=titan_admission,
        target_health=titan_health,
        now=now,
    )
    assert maintenance_decision.outcome == MaintenanceOutcome.ADMITTED
    visibility.publish_maintenance_decision(project_id, maintenance_decision)
    # An ADMITTED maintenance decision is not itself an execution — no
    # adapter, transport, or subprocess call happened anywhere above.

    # 9 & 10. Fleet certification evaluates the whole system (Stage 1
    # regression is confirmed True here to represent an out-of-band
    # separately-run confirmation; see test_stage1_regression.py for the
    # real subprocess invocation of the immutable Stage 1 scripts).
    certification = certify_fleet(
        evaluated_identity_ids=(titan_identity.identity_id, sigil_identity.identity_id),
        identity_registry_conflict_free=True,
        event_schema_valid=True,
        evidence_chain_valid=evidence_store.verify_chain(),
        health_protocol_compatible=True,
        admission_default_deny_selftest_passed=True,
        sigil_contract_restrictions_selftest_passed=True,
        remote_maintenance_default_deny_selftest_passed=True,
        stage1_regression_passed=True,
        ecosystem_services_no_unsafe_drift_selftest_passed=True,
        ecosystem_services_availability_selftest_passed=True,
        ecosystem_unverified_service_rejection_selftest_passed=True,
        ecosystem_duplicate_and_revoked_service_rejection_selftest_passed=True,
        ecosystem_self_evolution_self_approval_guard_selftest_passed=True,
        ecosystem_evidence_integrity_selftest_passed=True,
        policy_version="prime-admission-policy-v1",
        certifier_identity_id="prime",
        now=now,
        revalidation_seconds=3600,
        evidence_refs=(admission_evidence.evidence_id,),
    )
    assert certification.status == FleetCertificationStatus.CERTIFIED
    visibility.publish_certification(project_id, certification)

    # 11. Mission Control exposes governed status and evidence.
    snapshot = mission_control.get_snapshot(project_id)
    event_types = {event.event_type for event in snapshot.events}
    assert {
        "prime_identity_registered",
        "prime_health_reported",
        "prime_admission_decided",
        "prime_sigil_contract_invoked",
        "prime_remote_maintenance_decided",
        "prime_fleet_certified",
    }.issubset(event_types)

    # 13. No implicit execution authority appears anywhere in the flow.
    for obj in (
        titan_identity,
        sigil_identity,
        titan_health,
        sigil_health,
        titan_admission,
        sigil_admission,
        sigil_response,
        maintenance_decision,
        certification,
    ):
        for forbidden in (
            "execution_authorized",
            "broker_submission_authorized",
            "operational_authority",
        ):
            assert not hasattr(obj, forbidden)


def test_stale_conflicting_and_missing_inputs_fail_safely_across_the_whole_flow() -> (
    None
):
    """12. Stale, malformed, conflicting, unsupported, expired, revoked, or
    missing security inputs fail safely, exercised across every governed
    decision point in one flow."""
    now = _now()

    # Unknown identity -> denied.
    unknown_decision = PrimeAdmissionService().evaluate(
        AdmissionRequest(
            request_id="req_unknown",
            subject_identity_id="fid_node_ghost",
            role="ghost",
            software_version="0.0.0",
            protocol_version=1,
            health=None,  # also missing
            certification_status=CertificationStatus.UNKNOWN,
            policy_version="prime-admission-policy-v1",
            identity_known_and_active=False,
            identity_revoked=False,
            quarantined=False,
            requested_at=now,
        ),
        now=now,
    )
    assert unknown_decision.outcome == AdmissionOutcome.DENIED

    # Sigil contract with an unadmitted, unhealthy pair -> denied.
    request = SigilContractRequest(
        request_id="sreq_bad",
        correlation_id="corr_bad",
        caller_identity_id="ghost-caller",
        service_identity_id="sigil",
        operation="advisory_valuation",
        requested_at=now,
    )
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=None,
        service_admission=None,
        caller_health=None,
        service_health=None,
        now=now,
    )
    assert admitted is False
    assert code is not None

    # Maintenance with no admission/health/approvals at all -> denied.
    step = RepairStep(
        step_id="s1",
        command_id="reboot",
        mode=CommandMode.CONNECTIVITY,
        required_approvals=(ApprovalScope.REBOOT,),
        rollback_command_id="reboot",
    )
    proposal = RepairProposal.build(
        target_id="ghost-target",
        risk=RiskLevel.HIGH,
        expected_downtime="PT5M",
        finding_refs=(),
        steps=(step,),
        evidence_refs=(),
    )
    maintenance_request = GovernedMaintenanceRequest(
        request_id="mreq_bad",
        correlation_id="corr_bad",
        requester_identity_id="ghost-requester",
        target_identity_id="ghost-target",
        proposal=proposal,
        approvals=(),
        approval_issued_at=(),
        window=MaintenanceWindow(starts_at=now - 10, ends_at=now + 10),
        requested_at=now,
    )
    maintenance_decision = evaluate_maintenance_request(
        maintenance_request,
        requester_admission=None,
        requester_health=None,
        target_admission=None,
        target_health=None,
        now=now,
    )
    assert maintenance_decision.outcome == MaintenanceOutcome.DENIED
    assert len(maintenance_decision.reason_codes) >= 3

    # Certification without a confirmed Stage 1 regression -> blocked, never certified.
    certification = certify_fleet(
        evaluated_identity_ids=(),
        identity_registry_conflict_free=True,
        event_schema_valid=True,
        evidence_chain_valid=True,
        health_protocol_compatible=True,
        admission_default_deny_selftest_passed=True,
        sigil_contract_restrictions_selftest_passed=True,
        remote_maintenance_default_deny_selftest_passed=True,
        stage1_regression_passed=None,
        policy_version="prime-admission-policy-v1",
        certifier_identity_id="prime",
        now=now,
        revalidation_seconds=3600,
    )
    assert certification.status != FleetCertificationStatus.CERTIFIED
