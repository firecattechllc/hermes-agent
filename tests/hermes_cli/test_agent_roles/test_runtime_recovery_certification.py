"""Step 21 governed runtime recovery lifecycle certification."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    RuntimeRecoveryCertification,
    RuntimeRecoveryCertificationBuilder,
    RuntimeRecoveryCertificationStatus,
    runtime_recovery_certifications_requiring_attention,
    verify_runtime_recovery_certification,
)
from hermes_cli.agent_roles.runtime_recovery_audit import (
    RuntimeRecoveryAuditBuilder,
    RuntimeRecoveryAuditEventType,
)
from hermes_cli.agent_roles.runtime_recovery_closure_store import (
    RuntimeRecoveryClosureRecord,
    RuntimeRecoveryClosureState,
)
from hermes_cli.agent_roles.runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionRecord,
    RuntimeRecoveryExecutionState,
)
from hermes_cli.agent_roles.runtime_recovery_reconciliation_store import (
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
)
from hermes_cli.agent_roles.runtime_recovery_reporting import RuntimeRecoveryReport
from hermes_cli.agent_roles.runtime_recovery_store import (
    RuntimeRecoveryAction,
    RuntimeRecoveryRecord,
    RuntimeRecoveryState,
)


def _checksum(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def lifecycle(**changes):
    recovery_values = dict(
        journal_sequence=2, recovery_id="recovery-1", project_id="project-1",
        execution_id="execution-1", supervision_id="supervision-1",
        supervision_revision=1, action=RuntimeRecoveryAction.CANCEL,
        state=RuntimeRecoveryState.APPROVED, revision=2, requested_by="requester",
        requested_at=10, request_reason="unhealthy runtime", correlation_id="correlation-1",
        causation_id="p" * 64, authorization_id="authorization-1",
        authorized_by="approver", authorized_at=20,
        authorization_reason="approved cancellation",
    )
    recovery_values.update(changes.get("recovery", {}))
    recovery = RuntimeRecoveryRecord(
        **recovery_values,
        checksum=RuntimeRecoveryRecord.calculate_checksum(**recovery_values),
    )

    receipt_values = dict(
        journal_sequence=1, recovery_execution_id="recovery-execution-1",
        project_id="project-1", recovery_id="recovery-1", recovery_revision=2,
        execution_id="execution-1", source_execution_fingerprint="f" * 64,
        action=RuntimeRecoveryAction.CANCEL, state=RuntimeRecoveryExecutionState.EXECUTED,
        actor_id="operator", correlation_id="correlation-1", causation_id=recovery.checksum,
        authorization_id="authorization-1", executed_at=30,
        reason="approved cancellation executed", resulting_execution_revision=2,
        resulting_execution_state="cancelled", evidence_refs=("evidence-1",),
    )
    receipt_values.update(changes.get("receipt", {}))
    receipt = RuntimeRecoveryExecutionRecord(
        **receipt_values,
        checksum=RuntimeRecoveryExecutionRecord.calculate_checksum(**receipt_values),
    )

    reconciliation_values = dict(
        reconciliation_id="reconciliation-1", schema_version=1,
        project_id="project-1", recovery_execution_id="recovery-execution-1",
        recovery_id="recovery-1", recovery_revision=2, execution_id="execution-1",
        action=RuntimeRecoveryAction.CANCEL,
        recovery_execution_state=RuntimeRecoveryExecutionState.EXECUTED,
        state=RuntimeRecoveryReconciliationState.RECONCILED, actor_id="reconciler",
        correlation_id="correlation-1", causation_id=receipt.checksum,
        authorization_id="authorization-1", reconciled_at=40,
        source_recovery_checksum=recovery.checksum,
        source_recovery_execution_checksum=receipt.checksum,
        expected_execution_revision=2, expected_execution_state="cancelled",
        observed_execution_revision=2, observed_execution_state="cancelled",
        reason="authoritative cancellation reconciled",
        evidence_refs=("evidence-1", recovery.checksum, receipt.checksum),
        revision=1, previous_checksum=None,
    )
    reconciliation_values.update(changes.get("reconciliation", {}))
    reconciliation = RuntimeRecoveryReconciliationRecord(
        **reconciliation_values,
        checksum=RuntimeRecoveryReconciliationRecord.calculate_checksum(**reconciliation_values),
    )

    closure_values = dict(
        closure_id="closure-1", schema_version=1, project_id="project-1",
        reconciliation_id="reconciliation-1", recovery_execution_id="recovery-execution-1",
        recovery_id="recovery-1", execution_id="execution-1",
        action=RuntimeRecoveryAction.CANCEL,
        reconciliation_state=RuntimeRecoveryReconciliationState.RECONCILED,
        state=RuntimeRecoveryClosureState.CLOSED, actor_id="closer",
        correlation_id="correlation-1", causation_id=reconciliation.checksum,
        closed_at=50, source_reconciliation_checksum=reconciliation.checksum,
        reason="recovery lifecycle closed",
        evidence_refs=("evidence-1", recovery.checksum, receipt.checksum,
                       "recovery-execution-1", "reconciliation-1", reconciliation.checksum),
        revision=1, previous_checksum=None,
    )
    closure_values.update(changes.get("closure", {}))
    closure = RuntimeRecoveryClosureRecord(
        **closure_values,
        checksum=RuntimeRecoveryClosureRecord.calculate_checksum(**closure_values),
    )

    report_evidence = tuple(dict.fromkeys((*reconciliation.evidence_refs, *closure.evidence_refs)))
    report_values = dict(
        schema_version=1, project_id="project-1", recovery_id="recovery-1",
        execution_id="execution-1", recovery_execution_id="recovery-execution-1",
        reconciliation_id="reconciliation-1", closure_id="closure-1", action="cancel",
        recovery_execution_state="executed", reconciliation_state="reconciled",
        closure_state="closed", actor_id="reconciler", correlation_id="correlation-1",
        causation_id=receipt.checksum, reconciled_at=40, closed_at=50,
        closure_latency=10, reason="authoritative cancellation reconciled",
        closure_reason="recovery lifecycle closed", evidence_refs=report_evidence,
        reconciliation_checksum=reconciliation.checksum, closure_checksum=closure.checksum,
        source_checksums=(recovery.checksum, receipt.checksum, reconciliation.checksum, closure.checksum),
        is_terminal=True, requires_attention=False,
    )
    report_values.update(changes.get("report", {}))
    report_checksum = _checksum({**report_values, "evidence_refs": list(report_values["evidence_refs"]), "source_checksums": list(report_values["source_checksums"])})
    report = RuntimeRecoveryReport(
        report_id=f"runtime_recovery_report_{report_checksum[:24]}",
        **report_values, checksum=report_checksum,
    )

    audit = RuntimeRecoveryAuditBuilder.build(
        audit_sequence=1, project_id="project-1", recovery_id="recovery-1",
        execution_id="execution-1", closure_id="closure-1", report_id=report.report_id,
        event_type=changes.get("audit_type", RuntimeRecoveryAuditEventType.RECOVERY_REPORT_CREATED),
        lifecycle_state="closed", actor_id="auditor", reason="complete recovery audit",
        occurred_at=60, evidence_refs=report.evidence_refs,
        source_checksums=(recovery.checksum, receipt.checksum, reconciliation.checksum, closure.checksum, report.checksum),
    )
    return recovery, receipt, reconciliation, closure, report, (audit,)


def build(parts=None, **kwargs):
    recovery, receipt, reconciliation, closure, report, audits = parts or lifecycle()
    return RuntimeRecoveryCertificationBuilder.from_artifacts(
        recovery=recovery, recovery_execution=receipt, reconciliation=reconciliation,
        closure=closure, report=report, audit_artifacts=audits,
        certified_at=100, actor_id="certifier", **kwargs,
    )


def artifact_map(parts):
    return {item_id: item for item_id, item in zip(
        ("recovery-1", "recovery-execution-1", "reconciliation-1", "closure-1", parts[4].report_id),
        parts[:5],
    )}


def check(certification, name):
    return next(item for item in certification.validation_checks if item.check_name == name)


def test_successful_complete_lifecycle_certification_and_direct_step_artifacts():
    certification = build()
    assert certification.status == RuntimeRecoveryCertificationStatus.CERTIFIED
    assert all(item.passed for item in certification.validation_checks)
    assert certification.artifact_inventory[-1].artifact_type == "runtime_recovery_audit"


def test_deterministic_rebuild_produces_identical_checksum_id_and_inventory():
    parts = lifecycle()
    first = build(parts)
    second = build(parts)
    assert first == second
    assert first.checksum == second.checksum
    assert first.certification_id == second.certification_id
    assert first.artifact_inventory == second.artifact_inventory


def test_certification_and_lifecycle_checksums_verify():
    parts = lifecycle()
    certification = build(parts)
    result = verify_runtime_recovery_certification(
        certification, artifacts=artifact_map(parts), audit_artifacts=parts[5]
    )
    assert result.valid is True
    assert result.checked_count > len(certification.artifact_inventory)


def test_missing_required_artifact_rejects():
    parts = lifecycle()
    certification = RuntimeRecoveryCertificationBuilder.from_artifacts(
        recovery=parts[0], recovery_execution=parts[1], reconciliation=parts[2],
        closure=None, report=parts[4], audit_artifacts=parts[5],
        certified_at=100, actor_id="certifier",
    )
    assert certification.status == RuntimeRecoveryCertificationStatus.REJECTED
    assert check(certification, "required_artifact_presence").passed is False


def test_corrupted_source_checksum_detected():
    parts = list(lifecycle())
    parts[1] = parts[1].model_copy(update={"checksum": "0" * 64})
    certification = build(tuple(parts))
    assert certification.status == RuntimeRecoveryCertificationStatus.REJECTED
    assert check(certification, "runtime_recovery_execution_checksum_integrity").passed is False


@pytest.mark.parametrize(("part", "field", "check_name"), [
    (1, "recovery_id", "recovery_id_consistency"),
    (1, "project_id", "project_id_consistency"),
    (1, "execution_id", "execution_id_consistency"),
    (1, "recovery_revision", "recovery_revision_consistency"),
])
def test_identity_and_revision_mismatches_detected(part, field, check_name):
    parts = list(lifecycle())
    parts[part] = parts[part].model_copy(update={field: "other" if field != "recovery_revision" else 3})
    certification = build(tuple(parts))
    assert certification.status == RuntimeRecoveryCertificationStatus.REJECTED
    assert check(certification, check_name).passed is False


def test_invalid_lifecycle_chronology_detected():
    certification = build(lifecycle(receipt={"executed_at": 15}))
    assert check(certification, "lifecycle_chronology").passed is False


def test_source_checksum_mismatch_detected():
    parts = list(lifecycle())
    parts[2] = parts[2].model_copy(update={"source_recovery_checksum": "0" * 64})
    certification = build(tuple(parts))
    assert check(certification, "source_checksum_references").passed is False


def test_evidence_reference_mismatch_detected():
    parts = list(lifecycle())
    parts[3] = parts[3].model_copy(update={"evidence_refs": ("different",)})
    certification = build(tuple(parts))
    assert check(certification, "evidence_reference_continuity").passed is False


def test_broken_audit_chain_detected():
    parts = list(lifecycle())
    second = RuntimeRecoveryAuditBuilder.build(
        audit_sequence=2, project_id="project-1", recovery_id="recovery-1",
        execution_id="execution-1", event_type=RuntimeRecoveryAuditEventType.RECOVERY_CLOSURE_CREATED,
        lifecycle_state="closed", actor_id="auditor", reason="closure", occurred_at=70,
        source_checksums=(parts[3].checksum,), previous_event_checksum=parts[5][0].checksum,
    )
    parts[5] = (second, parts[5][0])
    certification = build(tuple(parts))
    assert check(certification, "audit_sequence_checksum_chain_integrity").passed is False


def test_attention_flags_propagate_and_helper_filters():
    parts = lifecycle(audit_type=RuntimeRecoveryAuditEventType.ATTENTION_REQUIRED)
    certification = build(parts)
    assert certification.status == RuntimeRecoveryCertificationStatus.ATTENTION_REQUIRED
    assert certification.attention_flags
    assert runtime_recovery_certifications_requiring_attention((certification,)) == (certification,)


def test_corrupted_certification_rejected_by_verification():
    parts = lifecycle()
    certification = build(parts).model_copy(update={"checksum": "0" * 64})
    result = verify_runtime_recovery_certification(
        certification, artifacts=artifact_map(parts), audit_artifacts=parts[5]
    )
    assert result.valid is False
    assert result.errors


def test_verification_detects_missing_and_unexpected_artifacts():
    parts = lifecycle()
    certification = build(parts)
    artifacts = artifact_map(parts)
    artifacts.pop("closure-1")
    artifacts["unexpected"] = parts[0]
    result = verify_runtime_recovery_certification(
        certification, artifacts=artifacts, audit_artifacts=parts[5]
    )
    assert "closure-1" in result.missing_refs
    assert any("unexpected" in item for item in result.identity_mismatches)


def test_public_exports_import_correctly():
    assert RuntimeRecoveryCertificationBuilder is not None
    assert verify_runtime_recovery_certification is not None


@pytest.mark.parametrize("kwargs", [
    {"actor_id": " "},
    {"certified_at": -1},
    {"certification_revision": 0},
])
def test_invalid_builder_inputs_raise(kwargs):
    parts = lifecycle()
    values = dict(
        recovery=parts[0], recovery_execution=parts[1], reconciliation=parts[2],
        closure=parts[3], report=parts[4], audit_artifacts=parts[5],
        certified_at=100, actor_id="certifier",
    )
    values.update(kwargs)
    with pytest.raises(ValueError):
        RuntimeRecoveryCertificationBuilder.from_artifacts(**values)


def test_certification_is_immutable():
    with pytest.raises(ValidationError):
        build().status = RuntimeRecoveryCertificationStatus.REJECTED
