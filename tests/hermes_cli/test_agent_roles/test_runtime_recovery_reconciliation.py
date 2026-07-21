"""Step 17 governed runtime recovery reconciliation certification."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.runtime_execution import RuntimeExecutionState
from hermes_cli.agent_roles.runtime_recovery_execution_store import RuntimeRecoveryExecutionState, RuntimeRecoveryExecutionStore
from hermes_cli.agent_roles.runtime_recovery_reconciliation import (
    GovernedRuntimeRecoveryReconciliationCoordinator,
    RuntimeRecoveryReconciliationError,
    RuntimeRecoveryReconciliationPublicationError,
)
from hermes_cli.agent_roles.runtime_recovery_reconciliation_store import (
    RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION,
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
    RuntimeRecoveryReconciliationStore,
)
from hermes_cli.agent_roles.runtime_recovery_reconciliation_visibility import RuntimeRecoveryReconciliationVisibilityAdapter
from hermes_cli.agent_roles.runtime_recovery_store import RuntimeRecoveryAction, RuntimeRecoveryDecision, RuntimeRecoveryStore
from hermes_cli.agent_roles.runtime_supervision_store import RuntimeSupervisionStore, SupervisionStatus


class ExecutionStore:
    def __init__(self, before, after):
        self.before, self.after = before, after

    def get(self, project_id, execution_id):
        if project_id == self.after.project_id and execution_id == self.after.execution_id:
            return self.after
        return None

    def history(self, project_id, execution_id):
        if not self.get(project_id, execution_id):
            return ()
        return (self.before,) if self.before is self.after else (self.before, self.after)


def execution(revision, state, fingerprint, updated_at):
    return SimpleNamespace(project_id="project001", execution_id="execution001", revision=revision,
                           state=state, fingerprint=fingerprint, updated_at=updated_at)


def build(tmp_path, *, action=RuntimeRecoveryAction.CANCEL, visibility=None):
    before = execution(1, RuntimeExecutionState.RUNNING, "e" * 64, 2000)
    after = (execution(2, RuntimeExecutionState.CANCELLED, "c" * 64, 2300)
             if action == RuntimeRecoveryAction.CANCEL else before)
    executions = ExecutionStore(before, after)
    supervisions = RuntimeSupervisionStore(tmp_path / "supervisions")
    supervision = supervisions.observe(
        project_id="project001", execution_id="execution001", status=SupervisionStatus.STALE,
        actor_id="supervisor", correlation_id="run001", causation_id=before.fingerprint,
        observed_at=2000, last_heartbeat_at=1000, started_at=1000,
        heartbeat_threshold_seconds=600, reason="execution heartbeat is stale")
    recoveries = RuntimeRecoveryStore(tmp_path / "recoveries")
    pending = recoveries.create(
        recovery_id=f"recovery-{action.value}", project_id="project001", execution_id="execution001",
        supervision_id=f"supervision_{supervision.checksum[:24]}", supervision_revision=1,
        action=action, requested_by="operator", requested_at=2100,
        request_reason="recovery requested", correlation_id="run001", causation_id=supervision.checksum)
    recovery = recoveries.decide(
        project_id="project001", recovery_id=pending.recovery_id, expected_revision=1,
        decision=RuntimeRecoveryDecision.APPROVED, authorization_id="auth001",
        authorized_by="owner", authorized_at=2200, authorization_reason="approved recovery")
    receipts = RuntimeRecoveryExecutionStore(tmp_path / "receipts")
    state = RuntimeRecoveryExecutionState.EXECUTED if action == RuntimeRecoveryAction.CANCEL else RuntimeRecoveryExecutionState.HANDOFF_REQUIRED
    receipt = receipts.create(
        recovery_execution_id=f"receipt-{action.value}", project_id="project001",
        recovery_id=recovery.recovery_id, recovery_revision=recovery.revision,
        execution_id="execution001", source_execution_fingerprint=before.fingerprint,
        action=action, state=state, actor_id="executor", correlation_id="run001",
        causation_id=recovery.checksum, authorization_id="auth001", executed_at=2300,
        reason="receipt", resulting_execution_revision=2 if state == RuntimeRecoveryExecutionState.EXECUTED else None,
        resulting_execution_state="cancelled" if state == RuntimeRecoveryExecutionState.EXECUTED else None,
        evidence_refs=("auth001", recovery.recovery_id, before.fingerprint))
    reconciliations = RuntimeRecoveryReconciliationStore()
    coordinator = GovernedRuntimeRecoveryReconciliationCoordinator(
        executions=executions, supervisions=supervisions, recoveries=recoveries,
        receipts=receipts, reconciliations=reconciliations, visibility=visibility)
    return SimpleNamespace(before=before, after=after, executions=executions, supervisions=supervisions,
                           recoveries=recoveries, recovery=recovery, receipts=receipts, receipt=receipt,
                           reconciliations=reconciliations, coordinator=coordinator)


def reconcile(ctx, **overrides):
    values = dict(project_id="project001", recovery_execution_id=ctx.receipt.recovery_execution_id,
                  actor_id="reconciler", correlation_id="run001", timestamp=2400)
    values.update(overrides)
    return ctx.coordinator.reconcile(**values)


def replace_receipt(ctx, **changes):
    data = ctx.receipt.model_dump()
    data.update(changes)
    data["checksum"] = RuntimeRecoveryExecutionStore.__dict__.get("unused", "")
    data.pop("checksum")
    from hermes_cli.agent_roles.runtime_recovery_execution_store import RuntimeRecoveryExecutionRecord
    data["checksum"] = RuntimeRecoveryExecutionRecord.calculate_checksum(**data)
    ctx.receipts._read_unlocked = lambda *args, **kwargs: (RuntimeRecoveryExecutionRecord(**data),)


def test_successful_cancellation_and_deterministic_id(tmp_path):
    ctx = build(tmp_path)
    record = reconcile(ctx)
    assert record.state == RuntimeRecoveryReconciliationState.RECONCILED
    assert record.expected_execution_revision == record.observed_execution_revision == 2
    assert record.expected_execution_state == record.observed_execution_state == "cancelled"
    assert record.reconciliation_id == ctx.coordinator.reconciliation_id_for(ctx.receipt, ctx.recovery)


def test_exact_replay_is_idempotent_and_conflicting_replay_rejected(tmp_path):
    ctx = build(tmp_path)
    first = reconcile(ctx)
    assert reconcile(ctx) == first
    assert len(ctx.reconciliations.list("project001")) == 1
    with pytest.raises(RuntimeRecoveryReconciliationError, match="conflicting"):
        reconcile(ctx, actor_id="other")


def test_missing_receipt(tmp_path):
    ctx = build(tmp_path)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="receipt not found"):
        reconcile(ctx, recovery_execution_id="missing")


def test_missing_recovery_request(tmp_path):
    ctx = build(tmp_path)
    ctx.recoveries.get = lambda *args: None
    with pytest.raises(RuntimeRecoveryReconciliationError, match="request not found"):
        reconcile(ctx)


@pytest.mark.parametrize("field,value,match", [
    ("recovery_id", "wrong", "request not found"),
    ("recovery_revision", 1, "revision mismatch"),
    ("action", RuntimeRecoveryAction.RETRY, "action mismatch"),
    ("authorization_id", "wrong", "authorization mismatch"),
    ("causation_id", "x" * 64, "causation mismatch"),
])
def test_receipt_authority_mismatches_fail_closed(tmp_path, field, value, match):
    ctx = build(tmp_path)
    replace_receipt(ctx, **{field: value})
    with pytest.raises(RuntimeRecoveryReconciliationError, match=match):
        reconcile(ctx)


def test_project_mismatch_and_cross_project_isolation(tmp_path):
    ctx = build(tmp_path)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="receipt not found"):
        reconcile(ctx, project_id="other")
    record = reconcile(ctx)
    assert ctx.reconciliations.get("other", record.reconciliation_id) is None
    assert ctx.reconciliations.list("other") == ()


def test_broken_recovery_history_and_receipt_checksum_fail_closed(tmp_path):
    ctx = build(tmp_path)
    ctx.recoveries.history = lambda *args: (ctx.recovery,)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="history is broken"):
        reconcile(ctx)
    ctx = build(tmp_path / "checksum")
    path = ctx.receipts.journal_path("project001")
    path.write_text(path.read_text().replace('"actor_id":"executor"', '"actor_id":"intruder"'))
    with pytest.raises(RuntimeRecoveryReconciliationError, match="history is invalid"):
        reconcile(ctx)


def test_missing_runtime_and_bad_cancellation_result(tmp_path):
    ctx = build(tmp_path)
    ctx.executions.get = lambda *args: None
    with pytest.raises(RuntimeRecoveryReconciliationError, match="not found"):
        reconcile(ctx)
    ctx = build(tmp_path / "state")
    ctx.after.state = RuntimeExecutionState.RUNNING
    with pytest.raises(RuntimeRecoveryReconciliationError, match="not cancelled"):
        reconcile(ctx)


def test_cancellation_revision_and_state_mismatch(tmp_path):
    ctx = build(tmp_path)
    replace_receipt(ctx, resulting_execution_revision=3)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="revision mismatch"):
        reconcile(ctx)
    ctx = build(tmp_path / "state")
    replace_receipt(ctx, resulting_execution_state="failed")
    with pytest.raises(RuntimeRecoveryReconciliationError, match="state mismatch"):
        reconcile(ctx)


def test_source_fingerprint_uses_pre_cancellation_history(tmp_path):
    ctx = build(tmp_path)
    assert ctx.receipt.source_execution_fingerprint == ctx.before.fingerprint
    assert ctx.receipt.source_execution_fingerprint != ctx.after.fingerprint
    assert reconcile(ctx).state == RuntimeRecoveryReconciliationState.RECONCILED
    ctx = build(tmp_path / "bad")
    replace_receipt(ctx, source_execution_fingerprint="z" * 64)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="fingerprint"):
        reconcile(ctx)


@pytest.mark.parametrize("action", [RuntimeRecoveryAction.RETRY, RuntimeRecoveryAction.ESCALATE])
def test_handoff_actions_remain_pending_and_never_claim_execution(tmp_path, action):
    ctx = build(tmp_path, action=action)
    record = reconcile(ctx)
    assert record.state == RuntimeRecoveryReconciliationState.HANDOFF_PENDING
    assert record.expected_execution_revision is None
    assert "pending" in record.reason
    replace_receipt(ctx, state=RuntimeRecoveryExecutionState.EXECUTED,
                    resulting_execution_revision=2, resulting_execution_state="cancelled")
    ctx.reconciliations = RuntimeRecoveryReconciliationStore()
    ctx.coordinator._reconciliations = ctx.reconciliations
    with pytest.raises(RuntimeRecoveryReconciliationError, match="falsely claims"):
        reconcile(ctx)


@pytest.mark.parametrize("timestamp", [2199, 2299])
def test_timestamp_monotonicity(tmp_path, timestamp):
    ctx = build(tmp_path)
    with pytest.raises(RuntimeRecoveryReconciliationError, match="predates"):
        reconcile(ctx, timestamp=timestamp)


def test_store_immutability_checksum_chain_and_conflicts(tmp_path):
    ctx = build(tmp_path)
    record = reconcile(ctx)
    with pytest.raises(ValidationError):
        record.reason = "changed"
    assert ctx.reconciliations.get("project001", record.reconciliation_id) == record
    data = record.model_dump(exclude={"checksum"})
    data.update(reconciliation_id="other", recovery_execution_id="other", revision=2, previous_checksum=None)
    data["checksum"] = RuntimeRecoveryReconciliationRecord.calculate_checksum(**data)
    with pytest.raises(ValidationError, match="previous checksum"):
        RuntimeRecoveryReconciliationRecord(**data)
    data = record.model_dump(exclude={"checksum"})
    data["reason"] = "conflict"
    changed = RuntimeRecoveryReconciliationRecord(
        **data, checksum=RuntimeRecoveryReconciliationRecord.calculate_checksum(**data)
    )
    with pytest.raises(ValueError, match="conflicting"):
        ctx.reconciliations.append(changed)


def test_visibility_projection_contains_audit_fields(tmp_path):
    record = reconcile(build(tmp_path))
    adapter = RuntimeRecoveryReconciliationVisibilityAdapter()
    event = adapter.to_event(record)
    projected = adapter.from_events((event, event))
    assert len(projected) == 1
    item = projected[0]
    assert item.reconciliation_state == "reconciled"
    assert item.authorization_id == "auth001"
    assert item.expected_execution_state == item.observed_execution_state == "cancelled"
    assert item.checksum == record.checksum


def test_persistence_precedes_publication_and_failure_exposes_record(tmp_path):
    calls = []
    class Visibility:
        def publish(self, record):
            calls.append(record)
            raise RuntimeError("offline")
    ctx = build(tmp_path, visibility=Visibility())
    with pytest.raises(RuntimeRecoveryReconciliationPublicationError) as caught:
        reconcile(ctx)
    assert ctx.reconciliations.get("project001", caught.value.record.reconciliation_id) == caught.value.record
    assert calls == [caught.value.record]


def test_safe_publication_retry_uses_persisted_record(tmp_path):
    class Visibility:
        def __init__(self): self.fail = True; self.records = []
        def publish(self, record):
            self.records.append(record)
            if self.fail: raise RuntimeError("offline")
    visibility = Visibility()
    ctx = build(tmp_path, visibility=visibility)
    with pytest.raises(RuntimeRecoveryReconciliationPublicationError):
        reconcile(ctx)
    visibility.fail = False
    assert reconcile(ctx) == visibility.records[-1]
    assert len(ctx.reconciliations.list("project001")) == 1


def test_schema_and_canonical_serialization_integrity(tmp_path):
    record = reconcile(build(tmp_path))
    assert record.schema_version == RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION == 1
    canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert RuntimeRecoveryReconciliationRecord.model_validate_json(canonical) == record
    bad = record.model_copy(update={"checksum": "0" * 64})
    with pytest.raises(ValidationError, match="checksum mismatch"):
        RuntimeRecoveryReconciliationRecord.model_validate(bad.model_dump())
