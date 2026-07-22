"""Step 20 governed runtime recovery audit certification."""

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.runtime_recovery_audit import (
    RuntimeRecoveryAuditBuilder,
    RuntimeRecoveryAuditEvent,
    RuntimeRecoveryAuditEventType as EventType,
    RuntimeRecoveryAuditStore,
    verify_runtime_recovery_audit_chain,
    verify_runtime_recovery_audit_sources,
)
from hermes_cli.agent_roles.runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionState,
    RuntimeRecoveryExecutionStore,
)
from hermes_cli.agent_roles.runtime_recovery_store import RuntimeRecoveryAction


def event(*, sequence=1, previous=None, project="project001", recovery="recovery001",
          execution="runtime_recovery_execution_001", occurred_at=100,
          event_type=EventType.RECOVERY_EXECUTION_CREATED, state="executed",
          evidence=("evidence-b", "evidence-a", "evidence-a"),
          sources=("b" * 64, "a" * 64, "a" * 64)):
    return RuntimeRecoveryAuditBuilder.build(
        audit_sequence=sequence, project_id=project, recovery_id=recovery,
        execution_id=execution, event_type=event_type, lifecycle_state=state,
        actor_id="operator", reason="governed recovery audit", occurred_at=occurred_at,
        evidence_refs=evidence, source_checksums=sources,
        previous_event_checksum=previous,
    )


def chain(*, second_type=EventType.RECONCILIATION_COMPLETED, second_state="reconciled"):
    first = event()
    second = event(sequence=2, previous=first.checksum, occurred_at=200,
                   event_type=second_type, state=second_state)
    third = event(sequence=3, previous=second.checksum, occurred_at=300,
                  event_type=EventType.RECOVERY_CLOSURE_CREATED, state="closed")
    return first, second, third


def test_terminal_successful_recovery_audit_chain():
    store = RuntimeRecoveryAuditStore()
    events = chain()
    for item in events:
        store.append(item)
    result = store.verify("recovery001")
    assert result.valid is True
    assert result.checked_count == 3
    assert [item.lifecycle_state for item in store.list_for_recovery("recovery001")] == ["executed", "reconciled", "closed"]


@pytest.mark.parametrize("event_type,state", [
    (EventType.RECOVERY_ACTION_FAILED, "failed"),
    (EventType.RECONCILIATION_UNRESOLVED, "inconsistent"),
])
def test_unsafe_recovery_requires_attention(event_type, state):
    store = RuntimeRecoveryAuditStore()
    item = event(event_type=event_type, state=state)
    store.append(item)
    assert store.list(requires_attention=True) == (item,)


def test_deterministic_event_creation_and_normalisation():
    first = event()
    second = event(evidence=("evidence-a", "evidence-b"), sources=("a" * 64, "b" * 64))
    assert first == second
    assert first.evidence_refs == ("evidence-a", "evidence-b")
    assert first.source_checksums == ("a" * 64, "b" * 64)


def test_deterministic_listing_and_filters():
    store = RuntimeRecoveryAuditStore()
    later = event(project="project002", recovery="recovery002", execution="receipt002", occurred_at=300)
    first, second, _ = chain()
    store.append(later)
    store.append(first)
    store.append(second)
    assert store.list_for_project("project001") == (first, second)
    assert store.list(event_type=EventType.RECONCILIATION_COMPLETED) == (second,)
    assert store.list_for_execution("receipt002") == (later,)


def test_identical_append_is_idempotent_and_conflict_rejected():
    store = RuntimeRecoveryAuditStore()
    item = event()
    assert store.append(item) == store.append(item)
    values = item.model_dump(exclude={"checksum"})
    values["reason"] = "conflicting replay"
    conflict = RuntimeRecoveryAuditEvent(
        **values,
        checksum=RuntimeRecoveryAuditEvent.calculate_checksum(**values),
    )
    with pytest.raises(ValueError, match="conflicting"):
        store.append(conflict)


def test_broken_previous_checksum_detected():
    first, second, _ = chain()
    broken = second.model_copy(update={"previous_event_checksum": "0" * 64})
    result = verify_runtime_recovery_audit_chain((first, broken))
    assert result.valid is False
    assert result.failure_position == 2
    assert "modified" in result.reason or "previous" in result.reason


def test_modified_event_detected():
    first = event()
    modified = first.model_copy(update={"reason": "tampered"})
    result = verify_runtime_recovery_audit_chain((modified,))
    assert result.valid is False
    assert result.failure_event_id == first.audit_event_id


def test_reordered_event_detected():
    first, second, _ = chain()
    result = verify_runtime_recovery_audit_chain((second, first))
    assert result.valid is False
    assert result.failure_position == 1


def test_missing_event_detected_by_sequence_gap():
    first, _, third = chain()
    result = verify_runtime_recovery_audit_chain((first, third))
    assert result.valid is False
    assert "missing or reordered" in result.reason


def test_missing_lookup_raises_key_error():
    with pytest.raises(KeyError, match="not found"):
        RuntimeRecoveryAuditStore().get("missing")


def test_audit_events_are_immutable():
    item = event()
    with pytest.raises(ValidationError):
        item.reason = "changed"


def test_cross_project_recovery_ownership_rejected():
    store = RuntimeRecoveryAuditStore()
    first = event()
    store.append(first)
    cross_project = event(sequence=2, previous=first.checksum, project="project002")
    with pytest.raises(ValueError, match="project ownership"):
        store.append(cross_project)


def test_cross_recovery_execution_ownership_rejected():
    store = RuntimeRecoveryAuditStore()
    store.append(event())
    other = event(project="project001", recovery="recovery002")
    with pytest.raises(ValueError, match="execution ownership"):
        store.append(other)


def test_store_rejects_broken_chain_on_append():
    store = RuntimeRecoveryAuditStore()
    first = event()
    store.append(first)
    with pytest.raises(ValueError, match="chain mismatch"):
        store.append(event(sequence=2, previous="0" * 64))


def test_record_rejects_checksum_mismatch():
    item = event()
    with pytest.raises(ValidationError, match="checksum mismatch"):
        RuntimeRecoveryAuditEvent.model_validate(item.model_copy(update={"checksum": "0" * 64}).model_dump())


def test_builds_from_and_verifies_existing_execution_artifact(tmp_path):
    receipt = RuntimeRecoveryExecutionStore(tmp_path).create(
        recovery_execution_id="receipt001", project_id="project001",
        recovery_id="recovery001", recovery_revision=2,
        execution_id="execution001", source_execution_fingerprint="e" * 64,
        action=RuntimeRecoveryAction.CANCEL,
        state=RuntimeRecoveryExecutionState.EXECUTED, actor_id="operator",
        correlation_id="run001", causation_id="r" * 64,
        authorization_id="auth001", executed_at=100,
        reason="governed cancellation completed",
        resulting_execution_revision=2,
        resulting_execution_state="cancelled",
        evidence_refs=("auth001",),
    )
    audit = RuntimeRecoveryAuditBuilder.from_artifact(
        receipt, audit_sequence=1,
        event_type=EventType.RECOVERY_ACTION_COMPLETED,
    )
    assert audit.execution_id == receipt.execution_id
    assert receipt.checksum in audit.source_checksums
    result = verify_runtime_recovery_audit_sources(
        audit, artifacts={receipt.recovery_execution_id: receipt}
    )
    assert result.valid is True
    assert result.checked_count == 1
