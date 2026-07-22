"""Step 22 governed runtime recovery final authorization."""

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    RuntimeRecoveryAuthorizationArtifact,
    RuntimeRecoveryAuthorizationBuilder,
    RuntimeRecoveryAuthorizationDecision,
    RuntimeRecoveryAuthorizationPolicy,
    RuntimeRecoveryAuthorizationStatus,
    RuntimeRecoveryHumanApproval,
    runtime_recovery_authorizations_requiring_attention,
    verify_runtime_recovery_authorization,
)
from hermes_cli.agent_roles.runtime_recovery_certification import (
    RuntimeRecoveryCertificationStatus,
)
from tests.hermes_cli.test_agent_roles.test_runtime_recovery_certification import (
    artifact_map,
    build as build_certification,
    lifecycle,
)


def approval_for(certification, **changes):
    values = dict(
        project_id=certification.project_id,
        recovery_id=certification.recovery_id,
        recovery_revision=certification.recovery_revision,
        execution_id=certification.execution_id,
        certification_id=certification.certification_id,
        certification_checksum=certification.checksum,
        actor_id="human-approver",
        approved_at=110,
        reason="human final acceptance",
        evidence_refs=certification.evidence_refs,
    )
    values.update(changes)
    return RuntimeRecoveryHumanApproval.build(**values)


def build_authorization(*, parts=None, certification=None, policy=None, approval=None, **changes):
    parts = parts or lifecycle()
    certification = certification or build_certification(parts)
    values = dict(
        certification=certification,
        artifacts=artifact_map(parts),
        audit_artifacts=parts[5],
        policy=policy or RuntimeRecoveryAuthorizationPolicy(),
        actor_id="governance-authorizer",
        authorized_at=120,
        reason="certified lifecycle accepted",
        correlation_id="authorization-correlation-1",
        causation_id=certification.checksum,
        approval=approval,
    )
    values.update(changes)
    return RuntimeRecoveryAuthorizationBuilder.from_certification(**values)


def verify(authorization, *, parts=None, certification=None, policy=None, approval=None):
    parts = parts or lifecycle()
    certification = certification or build_certification(parts)
    return verify_runtime_recovery_authorization(
        authorization,
        certification=certification,
        artifacts=artifact_map(parts),
        audit_artifacts=parts[5],
        policy=policy or RuntimeRecoveryAuthorizationPolicy(),
        approval=approval,
    )


def test_successful_authorization_from_valid_certification():
    authorization = build_authorization()
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.AUTHORIZED
    assert authorization.decision == RuntimeRecoveryAuthorizationDecision.APPROVE
    assert verify(authorization).valid is True


def test_deterministic_rebuild():
    parts = lifecycle()
    first = build_authorization(parts=parts)
    second = build_authorization(parts=parts)
    assert first == second


def test_authorization_checksum_and_id_verification():
    authorization = build_authorization()
    assert authorization.checksum == authorization.calculate_checksum(**authorization.model_dump(exclude={"checksum"}))
    assert authorization.authorization_id == authorization.authorization_id_for(**authorization.model_dump(exclude={"checksum"}))


def test_invalid_certification_is_denied():
    parts = lifecycle()
    certification = build_certification(parts).model_copy(update={"checksum": "0" * 64})
    authorization = build_authorization(parts=parts, certification=certification)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED


def test_forged_certification_verification_cannot_authorize():
    parts = lifecycle()
    certification = build_certification(parts).model_copy(update={"checksum": "0" * 64})
    valid_result = verify_runtime_recovery_authorization(
        build_authorization(parts=parts), certification=build_certification(parts),
        artifacts=artifact_map(parts), audit_artifacts=parts[5],
        policy=RuntimeRecoveryAuthorizationPolicy(),
    )
    forged = valid_result.model_copy(update={
        "missing_refs": (), "checksum_mismatches": (), "identity_mismatches": (),
        "policy_failures": (), "approval_failures": (), "errors": (),
    })
    # A result from a different verification type is rejected by Pydantic, and
    # a forged certification result is independently recomputed below.
    from hermes_cli.agent_roles import RuntimeRecoveryCertificationVerificationResult
    supplied = RuntimeRecoveryCertificationVerificationResult(
        valid=True, checked_count=forged.checked_count,
    )
    authorization = build_authorization(
        parts=parts, certification=certification,
        certification_verification=supplied,
    )
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED


def test_rejected_certification_is_denied():
    parts = lifecycle()
    certification = build_certification(parts).model_copy(update={"status": RuntimeRecoveryCertificationStatus.REJECTED})
    authorization = build_authorization(parts=parts, certification=certification)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED


def test_attention_required_certification_propagates():
    parts = lifecycle(audit_type="attention_required")
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.ATTENTION_REQUIRED
    assert authorization.attention_flags


def test_missing_human_approval_is_pending_and_structurally_valid():
    policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    authorization = build_authorization(policy=policy)
    result = verify(authorization, policy=policy)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.PENDING_APPROVAL
    assert result.valid is True
    assert result.approval_failures


def test_valid_human_approval_authorizes():
    parts = lifecycle()
    certification = build_certification(parts)
    policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    approval = approval_for(certification)
    authorization = build_authorization(parts=parts, certification=certification, policy=policy, approval=approval)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.AUTHORIZED
    assert verify(authorization, parts=parts, certification=certification, policy=policy, approval=approval).valid


def test_mismatched_human_approval_is_rejected():
    parts = lifecycle()
    certification = build_certification(parts)
    policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    approval = approval_for(certification, project_id="other-project")
    authorization = build_authorization(parts=parts, certification=certification, policy=policy, approval=approval)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED
    assert authorization.human_approval_ref is None


@pytest.mark.parametrize(("field", "expected"), [
    ("project_id", "project_id"),
    ("recovery_id", "recovery_id"),
    ("recovery_revision", "recovery_revision"),
    ("execution_id", "execution_id"),
])
def test_identity_mismatch_is_detected(field, expected):
    authorization = build_authorization().model_copy(update={field: 99 if field == "recovery_revision" else "other"})
    assert expected in verify(authorization).identity_mismatches


def test_certification_checksum_mismatch_is_detected():
    authorization = build_authorization().model_copy(update={"certification_checksum": "0" * 64})
    assert "certification" in verify(authorization).checksum_mismatches


def test_policy_fingerprint_mismatch_is_detected():
    authorization = build_authorization().model_copy(update={"policy_fingerprint": "0" * 64})
    assert "policy fingerprint mismatch" in verify(authorization).policy_failures


def test_policy_failure_prevents_authorization():
    policy = RuntimeRecoveryAuthorizationPolicy(denied_actions=("cancel",))
    authorization = build_authorization(policy=policy)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED


def test_corrupted_authorization_artifact_is_detected():
    authorization = build_authorization().model_copy(update={"checksum": "0" * 64})
    result = verify(authorization)
    assert result.valid is False
    assert result.errors


def test_evidence_mismatch_is_detected():
    parts = lifecycle()
    authorization = build_authorization(parts=parts)
    artifacts = artifact_map(parts)
    artifacts.pop("closure-1")
    result = verify_runtime_recovery_authorization(
        authorization, certification=build_certification(parts), artifacts=artifacts,
        audit_artifacts=parts[5], policy=RuntimeRecoveryAuthorizationPolicy(),
    )
    assert "closure-1" in result.missing_refs


def test_audit_chain_failure_prevents_authorization():
    parts = list(lifecycle())
    parts[5] = (parts[5][0].model_copy(update={"checksum": "0" * 64}),)
    certification = build_certification(tuple(parts))
    authorization = build_authorization(parts=tuple(parts), certification=certification)
    assert authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED


def test_attention_filtering_includes_status_flags_and_verification_failure():
    authorized = build_authorization()
    policy = RuntimeRecoveryAuthorizationPolicy(require_human_approval=True)
    pending = build_authorization(policy=policy)
    failed = verify(authorized).model_copy(update={"valid": False})
    selected = runtime_recovery_authorizations_requiring_attention(
        (authorized, pending), verification_results={authorized.authorization_id: failed}
    )
    assert selected == tuple(sorted((authorized, pending), key=lambda item: (item.authorized_at, item.authorization_id)))


def test_public_exports_import_correctly():
    assert RuntimeRecoveryAuthorizationArtifact is not None
    assert RuntimeRecoveryAuthorizationBuilder is not None
    assert verify_runtime_recovery_authorization is not None


@pytest.mark.parametrize("changes", [
    {"actor_id": " "}, {"reason": " "}, {"correlation_id": " "},
    {"causation_id": " "}, {"authorized_at": 99}, {"authorization_revision": 0},
])
def test_invalid_builder_inputs_raise(changes):
    with pytest.raises(ValueError):
        build_authorization(**changes)


def test_artifact_is_immutable():
    with pytest.raises(ValidationError):
        build_authorization().status = RuntimeRecoveryAuthorizationStatus.DENIED


def test_existing_step21_certification_is_consumed_directly():
    parts = lifecycle()
    certification = build_certification(parts)
    authorization = build_authorization(parts=parts, certification=certification)
    assert authorization.certification_id == certification.certification_id
    assert authorization.lifecycle_checksum == certification.lifecycle_checksum
