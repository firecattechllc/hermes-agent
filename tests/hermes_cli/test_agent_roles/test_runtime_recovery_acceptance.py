"""Step 23 governed runtime recovery acceptance and handoff."""

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    RuntimeRecoveryAcceptance,
    RuntimeRecoveryAcceptanceBuilder,
    RuntimeRecoveryAcceptanceDecision,
    RuntimeRecoveryAcceptancePolicy,
    RuntimeRecoveryAcceptanceStatus,
    RuntimeRecoveryAuthorizationPolicy,
    RuntimeRecoveryAuthorizationStatus,
    RuntimeRecoveryHandoffPackage,
    RuntimeRecoveryHumanAcceptance,
    RuntimeRecoverySafeNextAction,
    runtime_recovery_acceptances_requiring_attention,
    verify_runtime_recovery_acceptance,
)
from tests.hermes_cli.test_agent_roles.test_runtime_recovery_authorization import (
    approval_for,
    build_authorization,
)
from tests.hermes_cli.test_agent_roles.test_runtime_recovery_certification import (
    artifact_map,
    build as build_certification,
    lifecycle,
)


def human_for(authorization, certification, **changes):
    values = dict(
        project_id=authorization.project_id,
        recovery_id=authorization.recovery_id,
        recovery_revision=authorization.recovery_revision,
        execution_id=authorization.execution_id,
        certification_id=certification.certification_id,
        certification_checksum=certification.checksum,
        authorization_id=authorization.authorization_id,
        authorization_checksum=authorization.checksum,
        actor_id="final-human",
        accepted=True,
        accepted_at=130,
        reason="final human acceptance",
        evidence_refs=authorization.evidence_refs,
    )
    values.update(changes)
    return RuntimeRecoveryHumanAcceptance.build(**values)


def build_acceptance(*, parts=None, certification=None, authorization=None,
                     policy=None, human_acceptance=None, **changes):
    parts = parts or lifecycle()
    certification = certification or build_certification(parts)
    authorization = authorization or build_authorization(parts=parts, certification=certification)
    values = dict(
        authorization=authorization,
        certification=certification,
        artifacts=artifact_map(parts),
        audit_artifacts=parts[5],
        authorization_policy=RuntimeRecoveryAuthorizationPolicy(),
        policy=policy or RuntimeRecoveryAcceptancePolicy(),
        actor_id="acceptance-governor",
        accepted_at=140,
        reason="authorized lifecycle accepted for governed handoff",
        correlation_id="acceptance-correlation-1",
        causation_id=authorization.checksum,
        human_acceptance=human_acceptance,
        destination_id="manual-review-board",
    )
    values.update(changes)
    return RuntimeRecoveryAcceptanceBuilder.from_authorization(**values)


def verify(acceptance, *, parts=None, certification=None, authorization=None,
           policy=None, human_acceptance=None, artifacts=None):
    parts = parts or lifecycle()
    certification = certification or build_certification(parts)
    authorization = authorization or build_authorization(parts=parts, certification=certification)
    return verify_runtime_recovery_acceptance(
        acceptance, authorization=authorization, certification=certification,
        artifacts=artifacts or artifact_map(parts), audit_artifacts=parts[5],
        authorization_policy=RuntimeRecoveryAuthorizationPolicy(),
        policy=policy or RuntimeRecoveryAcceptancePolicy(),
        human_acceptance=human_acceptance,
    )


def test_successful_final_acceptance_and_handoff():
    acceptance = build_acceptance()
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.ACCEPTED
    assert acceptance.decision == RuntimeRecoveryAcceptanceDecision.ACCEPT
    assert acceptance.handoff_package.safe_next_action == RuntimeRecoverySafeNextAction.READY_FOR_MANUAL_HANDOFF
    assert verify(acceptance).valid


def test_deterministic_acceptance_and_handoff_rebuilds():
    parts = lifecycle()
    first = build_acceptance(parts=parts)
    second = build_acceptance(parts=parts)
    assert first == second
    assert first.acceptance_id == second.acceptance_id
    assert first.checksum == second.checksum
    assert first.handoff_package.handoff_id == second.handoff_package.handoff_id
    assert first.handoff_package.checksum == second.handoff_package.checksum


def test_acceptance_checksum_and_id_verify():
    acceptance = build_acceptance()
    values = acceptance.model_dump(exclude={"checksum"})
    assert acceptance.checksum == RuntimeRecoveryAcceptance.calculate_checksum(**values)
    assert acceptance.acceptance_id == RuntimeRecoveryAcceptance.acceptance_id_for(**values)


def test_handoff_checksum_and_id_verify():
    handoff = build_acceptance().handoff_package
    values = handoff.model_dump(exclude={"checksum"})
    assert handoff.checksum == RuntimeRecoveryHandoffPackage.calculate_checksum(**values)
    assert handoff.handoff_id == RuntimeRecoveryHandoffPackage.handoff_id_for(**values)


def test_denied_authorization_causes_rejection():
    parts = lifecycle()
    authorization = build_authorization(parts=parts).model_copy(update={"status": RuntimeRecoveryAuthorizationStatus.DENIED})
    acceptance = build_acceptance(parts=parts, authorization=authorization)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.REJECTED


def test_pending_authorization_prevents_acceptance():
    parts = lifecycle()
    policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    authorization = build_authorization(parts=parts, policy=policy)
    acceptance = build_acceptance(parts=parts, authorization=authorization, authorization_policy=policy)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE


def test_attention_required_authorization_propagates():
    parts = lifecycle(audit_type="attention_required")
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    acceptance = build_acceptance(parts=parts, certification=certification, authorization=authorization)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.ATTENTION_REQUIRED
    assert acceptance.handoff_package.safe_next_action == RuntimeRecoverySafeNextAction.ATTENTION_REQUIRED


def test_missing_human_acceptance_is_pending_and_verifiable():
    policy = RuntimeRecoveryAcceptancePolicy(require_human_acceptance=True)
    acceptance = build_acceptance(policy=policy)
    result = verify(acceptance, policy=policy)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE
    assert result.valid
    assert acceptance.attention_flags == ("explicit final human acceptance is required",)


def test_valid_human_acceptance_permits_acceptance():
    parts = lifecycle()
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    policy = RuntimeRecoveryAcceptancePolicy(require_human_acceptance=True)
    human = human_for(authorization, certification)
    acceptance = build_acceptance(parts=parts, certification=certification, authorization=authorization, policy=policy, human_acceptance=human)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.ACCEPTED
    assert verify(acceptance, parts=parts, certification=certification, authorization=authorization, policy=policy, human_acceptance=human).valid


@pytest.mark.parametrize("changes", [
    {"project_id": "other-project"},
    {"authorization_checksum": "0" * 64},
    {"certification_checksum": "0" * 64},
    {"execution_id": "other-execution"},
    {"accepted": False},
    {"accepted_at": 100},
    {"evidence_refs": ("missing-evidence",)},
])
def test_mismatched_human_acceptance_is_rejected(changes):
    parts = lifecycle()
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    policy = RuntimeRecoveryAcceptancePolicy(require_human_acceptance=True)
    human = human_for(authorization, certification, **changes)
    acceptance = build_acceptance(parts=parts, certification=certification, authorization=authorization, policy=policy, human_acceptance=human)
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.REJECTED
    assert acceptance.human_acceptance_ref is None


@pytest.mark.parametrize(("field", "expected"), [
    ("project_id", "project_id"),
    ("recovery_id", "recovery_id"),
    ("recovery_revision", "recovery_revision"),
    ("execution_id", "execution_id"),
])
def test_identity_mismatches_are_detected(field, expected):
    acceptance = build_acceptance().model_copy(update={field: 99 if field == "recovery_revision" else "other"})
    assert expected in verify(acceptance).identity_mismatches


def test_authorization_checksum_mismatch_is_detected():
    acceptance = build_acceptance().model_copy(update={"authorization_checksum": "0" * 64})
    assert "authorization" in verify(acceptance).checksum_mismatches


def test_certification_continuity_mismatch_is_detected():
    acceptance = build_acceptance().model_copy(update={"certification_id": "other-certification"})
    assert "certification_id" in verify(acceptance).identity_mismatches


def test_lifecycle_checksum_mismatch_is_detected():
    acceptance = build_acceptance().model_copy(update={"lifecycle_checksum": "0" * 64})
    assert "lifecycle" in verify(acceptance).checksum_mismatches


def test_policy_fingerprint_mismatch_is_detected():
    acceptance = build_acceptance().model_copy(update={"policy_fingerprint": "0" * 64})
    assert "policy fingerprint mismatch" in verify(acceptance).policy_failures


def test_evidence_and_source_resolution_mismatch_is_detected():
    parts = lifecycle()
    acceptance = build_acceptance(parts=parts)
    artifacts = artifact_map(parts)
    artifacts.pop("closure-1")
    result = verify(acceptance, parts=parts, artifacts=artifacts)
    assert "closure-1" in result.missing_refs


def test_incomplete_handoff_inventory_is_detected():
    acceptance = build_acceptance()
    handoff = acceptance.handoff_package.model_copy(update={"artifact_inventory": acceptance.handoff_package.artifact_inventory[:-1]})
    acceptance = acceptance.model_copy(update={"handoff_package": handoff})
    assert "handoff inventory incomplete or inconsistent" in verify(acceptance).handoff_failures


def test_corrupted_acceptance_artifact_is_detected():
    result = verify(build_acceptance().model_copy(update={"checksum": "0" * 64}))
    assert not result.valid
    assert result.errors


def test_corrupted_handoff_package_is_detected():
    acceptance = build_acceptance()
    handoff = acceptance.handoff_package.model_copy(update={"checksum": "0" * 64})
    result = verify(acceptance.model_copy(update={"handoff_package": handoff}))
    assert not result.valid
    assert result.handoff_failures or result.errors


def test_safe_next_action_without_destination_is_review():
    acceptance = build_acceptance(destination_id=None)
    assert acceptance.handoff_package.safe_next_action == RuntimeRecoverySafeNextAction.READY_FOR_REVIEW


def test_attention_filtering_uses_status_verification_flags_and_handoff():
    accepted = build_acceptance()
    pending = build_acceptance(policy=RuntimeRecoveryAcceptancePolicy(require_human_acceptance=True))
    failed = verify(accepted).model_copy(update={"valid": False})
    selected = runtime_recovery_acceptances_requiring_attention(
        (accepted, pending), verification_results={accepted.acceptance_id: failed},
    )
    assert selected == tuple(sorted((accepted, pending), key=lambda item: (item.accepted_at, item.acceptance_id)))


def test_public_exports_import_correctly():
    assert RuntimeRecoveryAcceptance is not None
    assert RuntimeRecoveryAcceptanceBuilder is not None
    assert RuntimeRecoveryHandoffPackage is not None
    assert verify_runtime_recovery_acceptance is not None


@pytest.mark.parametrize("changes", [
    {"actor_id": " "}, {"reason": " "}, {"correlation_id": " "},
    {"causation_id": " "}, {"accepted_at": 119},
    {"acceptance_revision": 0}, {"destination_id": " "},
])
def test_invalid_builder_inputs_raise(changes):
    with pytest.raises(ValueError):
        build_acceptance(**changes)


def test_acceptance_and_handoff_are_immutable():
    acceptance = build_acceptance()
    with pytest.raises(ValidationError):
        acceptance.status = RuntimeRecoveryAcceptanceStatus.REJECTED
    with pytest.raises(ValidationError):
        acceptance.handoff_package.safe_next_action = RuntimeRecoverySafeNextAction.BLOCKED


def test_existing_step22_authorization_is_consumed_directly():
    parts = lifecycle()
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    acceptance = build_acceptance(parts=parts, certification=certification, authorization=authorization)
    assert acceptance.authorization_id == authorization.authorization_id
    assert acceptance.authorization_checksum == authorization.checksum


def test_step22_human_approved_authorization_is_consumed_with_approval_evidence():
    parts = lifecycle()
    certification = build_certification(parts)
    authorization_policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    authorization_approval = approval_for(certification)
    authorization = build_authorization(
        parts=parts, certification=certification, policy=authorization_policy,
        approval=authorization_approval,
    )
    acceptance = build_acceptance(
        parts=parts, certification=certification, authorization=authorization,
        authorization_policy=authorization_policy,
        authorization_approval=authorization_approval,
    )
    result = verify_runtime_recovery_acceptance(
        acceptance, authorization=authorization, certification=certification,
        artifacts=artifact_map(parts), audit_artifacts=parts[5],
        authorization_policy=authorization_policy,
        policy=RuntimeRecoveryAcceptancePolicy(),
        authorization_approval=authorization_approval,
    )
    assert acceptance.status == RuntimeRecoveryAcceptanceStatus.ACCEPTED
    assert result.valid


def test_full_step18_through_step23_lifecycle_verifies_end_to_end():
    parts = lifecycle()
    recovery, execution, reconciliation, closure, report, audits = parts
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    acceptance = build_acceptance(parts=parts, certification=certification, authorization=authorization)
    assert all((recovery, execution, reconciliation, closure, report, audits))
    assert certification.lifecycle_checksum == authorization.lifecycle_checksum == acceptance.lifecycle_checksum
    assert verify(acceptance, parts=parts, certification=certification, authorization=authorization).valid
