"""Step 18 governed runtime recovery closure certification."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.runtime_execution import RuntimeExecutionState
from hermes_cli.agent_roles.runtime_recovery_closure import (
    GovernedRuntimeRecoveryClosureCoordinator,
    RuntimeRecoveryClosureError,
    RuntimeRecoveryClosurePublicationError,
)
from hermes_cli.agent_roles.runtime_recovery_closure_store import (
    RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION,
    RuntimeRecoveryClosureRecord,
    RuntimeRecoveryClosureState,
    RuntimeRecoveryClosureStore,
)
from hermes_cli.agent_roles.runtime_recovery_closure_visibility import (
    RuntimeRecoveryClosureVisibilityAdapter,
)
from hermes_cli.agent_roles.runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionState,
    RuntimeRecoveryExecutionStore,
)
from hermes_cli.agent_roles.runtime_recovery_reconciliation import (
    GovernedRuntimeRecoveryReconciliationCoordinator,
)
from hermes_cli.agent_roles.runtime_recovery_reconciliation_store import (
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
    RuntimeRecoveryReconciliationStore,
)
from hermes_cli.agent_roles.runtime_recovery_store import (
    RuntimeRecoveryAction,
    RuntimeRecoveryDecision,
    RuntimeRecoveryStore,
)
from hermes_cli.agent_roles.runtime_supervision_store import (
    RuntimeSupervisionStore,
    SupervisionStatus,
)


class ExecutionStore:
    def __init__(self, before, after):
        self.before = before
        self.after = after

    def get(self, project_id, execution_id):
        if (
            project_id == self.after.project_id
            and execution_id == self.after.execution_id
        ):
            return self.after
        return None

    def history(self, project_id, execution_id):
        if not self.get(project_id, execution_id):
            return ()

        if self.before is self.after:
            return (self.before,)

        return self.before, self.after


def execution(revision, state, fingerprint, updated_at):
    return SimpleNamespace(
        project_id="project001",
        execution_id="execution001",
        revision=revision,
        state=state,
        fingerprint=fingerprint,
        updated_at=updated_at,
    )


def build(tmp_path, *, visibility=None):
    before = execution(
        1,
        RuntimeExecutionState.RUNNING,
        "e" * 64,
        2000,
    )
    after = execution(
        2,
        RuntimeExecutionState.CANCELLED,
        "c" * 64,
        2300,
    )

    executions = ExecutionStore(before, after)

    supervisions = RuntimeSupervisionStore(
        tmp_path / "supervisions"
    )
    supervision = supervisions.observe(
        project_id="project001",
        execution_id="execution001",
        status=SupervisionStatus.STALE,
        actor_id="supervisor",
        correlation_id="run001",
        causation_id=before.fingerprint,
        observed_at=2000,
        last_heartbeat_at=1000,
        started_at=1000,
        heartbeat_threshold_seconds=600,
        reason="execution heartbeat is stale",
    )

    recoveries = RuntimeRecoveryStore(
        tmp_path / "recoveries"
    )
    pending = recoveries.create(
        recovery_id="recovery-cancel",
        project_id="project001",
        execution_id="execution001",
        supervision_id=f"supervision_{supervision.checksum[:24]}",
        supervision_revision=1,
        action=RuntimeRecoveryAction.CANCEL,
        requested_by="operator",
        requested_at=2100,
        request_reason="recovery requested",
        correlation_id="run001",
        causation_id=supervision.checksum,
    )

    recovery = recoveries.decide(
        project_id="project001",
        recovery_id=pending.recovery_id,
        expected_revision=1,
        decision=RuntimeRecoveryDecision.APPROVED,
        authorization_id="auth001",
        authorized_by="owner",
        authorized_at=2200,
        authorization_reason="approved recovery",
    )

    receipts = RuntimeRecoveryExecutionStore(
        tmp_path / "receipts"
    )
    receipt = receipts.create(
        recovery_execution_id="receipt-cancel",
        project_id="project001",
        recovery_id=recovery.recovery_id,
        recovery_revision=recovery.revision,
        execution_id="execution001",
        source_execution_fingerprint=before.fingerprint,
        action=RuntimeRecoveryAction.CANCEL,
        state=RuntimeRecoveryExecutionState.EXECUTED,
        actor_id="executor",
        correlation_id="run001",
        causation_id=recovery.checksum,
        authorization_id="auth001",
        executed_at=2300,
        reason="cancellation receipt",
        resulting_execution_revision=2,
        resulting_execution_state="cancelled",
        evidence_refs=(
            "auth001",
            recovery.recovery_id,
            before.fingerprint,
        ),
    )

    reconciliations = RuntimeRecoveryReconciliationStore()
    reconciliation_coordinator = (
        GovernedRuntimeRecoveryReconciliationCoordinator(
            executions=executions,
            supervisions=supervisions,
            recoveries=recoveries,
            receipts=receipts,
            reconciliations=reconciliations,
        )
    )

    reconciliation = reconciliation_coordinator.reconcile(
        project_id="project001",
        recovery_execution_id=receipt.recovery_execution_id,
        actor_id="reconciler",
        correlation_id="run001",
        timestamp=2400,
    )

    closures = RuntimeRecoveryClosureStore()
    closure_coordinator = GovernedRuntimeRecoveryClosureCoordinator(
        reconciliations=reconciliations,
        closures=closures,
        visibility=visibility,
    )

    return SimpleNamespace(
        before=before,
        after=after,
        executions=executions,
        supervisions=supervisions,
        recoveries=recoveries,
        recovery=recovery,
        receipts=receipts,
        receipt=receipt,
        reconciliations=reconciliations,
        reconciliation=reconciliation,
        closures=closures,
        coordinator=closure_coordinator,
    )


def close(ctx, **overrides):
    values = dict(
        project_id="project001",
        reconciliation_id=ctx.reconciliation.reconciliation_id,
        actor_id="closer",
        correlation_id="run001",
        timestamp=2500,
    )
    values.update(overrides)
    return ctx.coordinator.close(**values)



def replace_reconciliation(ctx, **changes):
    data = ctx.reconciliation.model_dump()
    data.update(changes)
    data.pop("checksum")

    data["checksum"] = (
        RuntimeRecoveryReconciliationRecord.calculate_checksum(
            **data
        )
    )

    replacement = RuntimeRecoveryReconciliationRecord(**data)

    if not hasattr(ctx.reconciliations, "_records"):
        raise AssertionError(
            "reconciliation store does not expose in-memory records"
        )

    ctx.reconciliations._records = [
        replacement.model_copy(deep=True)
    ]

def test_successful_terminal_closure_and_deterministic_id(tmp_path):
    ctx = build(tmp_path)

    record = close(ctx)

    assert record.state == RuntimeRecoveryClosureState.CLOSED
    assert record.project_id == "project001"
    assert (
        record.reconciliation_id
        == ctx.reconciliation.reconciliation_id
    )
    assert (
        record.recovery_execution_id
        == ctx.reconciliation.recovery_execution_id
    )
    assert record.recovery_id == ctx.reconciliation.recovery_id
    assert record.execution_id == ctx.reconciliation.execution_id
    assert record.action == RuntimeRecoveryAction.CANCEL
    assert (
        record.reconciliation_state
        == RuntimeRecoveryReconciliationState.RECONCILED
    )
    assert (
        record.source_reconciliation_checksum
        == ctx.reconciliation.checksum
    )
    assert record.causation_id == ctx.reconciliation.checksum
    assert record.revision == 1
    assert record.previous_checksum is None
    assert record.closure_id == ctx.coordinator.closure_id_for(
        ctx.reconciliation
    )


def test_exact_replay_is_idempotent_and_conflicting_replay_rejected(
    tmp_path,
):
    ctx = build(tmp_path)

    first = close(ctx)

    assert close(ctx) == first
    assert len(ctx.closures.list("project001")) == 1

    with pytest.raises(
        RuntimeRecoveryClosureError,
        match="conflicting",
    ):
        close(ctx, actor_id="other")


def test_missing_reconciliation(tmp_path):
    ctx = build(tmp_path)

    with pytest.raises(
        RuntimeRecoveryClosureError,
        match="reconciliation not found",
    ):
        close(ctx, reconciliation_id="missing")


def test_project_mismatch_and_cross_project_isolation(tmp_path):
    ctx = build(tmp_path)

    with pytest.raises(
        RuntimeRecoveryClosureError,
        match="reconciliation not found",
    ):
        close(ctx, project_id="other")

    record = close(ctx)

    assert (
        ctx.closures.get("other", record.closure_id)
        is None
    )
    assert ctx.closures.list("other") == ()



@pytest.mark.parametrize(
    "field,value,match",
    [
        (
            "action",
            RuntimeRecoveryAction.RETRY,
            "only reconciled cancellation",
        ),
        (
            "causation_id",
            "x" * 64,
            "provenance is broken",
        ),
    ],
)
def test_reconciliation_authority_mismatches_fail_closed(
    tmp_path,
    field,
    value,
    match,
):
    ctx = build(tmp_path)
    replace_reconciliation(ctx, **{field: value})

    with pytest.raises(
        RuntimeRecoveryClosureError,
        match=match,
    ):
        close(ctx)


@pytest.mark.parametrize(
    "changes,match",
    [
        (
            {
                "state":
                    RuntimeRecoveryReconciliationState.HANDOFF_PENDING,
            },
            "handoff pending requires a handoff receipt",
        ),
        (
            {
                "expected_execution_state": "running",
            },
            "requires matching runtime result",
        ),
        (
            {
                "revision": 2,
            },
            "later reconciliation requires previous checksum",
        ),
        (
            {
                "previous_checksum": "p" * 64,
            },
            "initial reconciliation cannot have previous checksum",
        ),
    ],
)
def test_invalid_reconciliation_mutations_are_rejected_by_schema(
    tmp_path,
    changes,
    match,
):
    ctx = build(tmp_path)

    data = ctx.reconciliation.model_dump()
    data.update(changes)
    data.pop("checksum")

    data["checksum"] = (
        RuntimeRecoveryReconciliationRecord.calculate_checksum(
            **data
        )
    )

    with pytest.raises(
        ValidationError,
        match=match,
    ):
        RuntimeRecoveryReconciliationRecord(**data)


def test_tampered_reconciliation_history_fails_closed(tmp_path):
    ctx = build(tmp_path)

    tampered = ctx.reconciliation.model_copy(
        update={"actor_id": "intruder"}
    )

    ctx.reconciliations._records = [tampered]

    with pytest.raises(
        RuntimeRecoveryClosureError,
        match="history is invalid",
    ):
        close(ctx)

@pytest.mark.parametrize(
    "timestamp",
    [
        0,
        2399,
    ],
)
def test_timestamp_must_follow_reconciliation(
    tmp_path,
    timestamp,
):
    ctx = build(tmp_path)

    with pytest.raises(
        (
            RuntimeRecoveryClosureError,
            ValidationError,
            ValueError,
        )
    ):
        close(ctx, timestamp=timestamp)



def test_store_immutability_checksum_and_conflicts(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    assert (
        RuntimeRecoveryClosureRecord.calculate_checksum(
            **record.model_dump(exclude={"checksum"})
        )
        == record.checksum
    )

    with pytest.raises(ValidationError):
        record.actor_id = "intruder"

    duplicate_data = record.model_dump()
    duplicate_data["closure_id"] = "closure-other"
    duplicate_data.pop("checksum")
    duplicate_data["checksum"] = (
        RuntimeRecoveryClosureRecord.calculate_checksum(
            **duplicate_data
        )
    )

    duplicate = RuntimeRecoveryClosureRecord(
        **duplicate_data
    )

    ctx.closures._records = [
        record.model_copy(deep=True),
        duplicate.model_copy(deep=True),
    ]

    with pytest.raises(
        ValueError,
        match="multiple closures",
    ):
        ctx.closures._validated_records()


def test_store_rejects_duplicate_closure_identifier(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    duplicate_data = record.model_dump()
    duplicate_data["reconciliation_id"] = (
        "reconciliation-other"
    )
    duplicate_data.pop("checksum")
    duplicate_data["checksum"] = (
        RuntimeRecoveryClosureRecord.calculate_checksum(
            **duplicate_data
        )
    )

    duplicate = RuntimeRecoveryClosureRecord(
        **duplicate_data
    )

    ctx.closures._records = [
        record.model_copy(deep=True),
        duplicate.model_copy(deep=True),
    ]

    with pytest.raises(
        ValueError,
        match="duplicate runtime recovery closure identifier",
    ):
        ctx.closures._validated_records()

def test_visibility_projection_contains_audit_fields(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    adapter = RuntimeRecoveryClosureVisibilityAdapter()
    event = adapter.to_event(record)
    projected = adapter.from_events((event,))

    assert len(projected) == 1

    item = projected[0]

    assert item.closure_id == record.closure_id
    assert item.project_id == record.project_id
    assert (
        item.reconciliation_id
        == record.reconciliation_id
    )
    assert item.closure_state == record.state.value
    assert item.action == record.action.value
    assert (
        item.reconciliation_state
        == record.reconciliation_state.value
    )
    assert (
        item.source_reconciliation_checksum
        == record.source_reconciliation_checksum
    )
    assert item.checksum == record.checksum


def test_visibility_rejects_bad_source_and_provenance(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    adapter = RuntimeRecoveryClosureVisibilityAdapter()
    event = adapter.to_event(record)

    bad_source = event.model_copy(
        update={
            "payload": {
                **event.payload,
                "source": "wrong",
            }
        }
    )

    with pytest.raises(
        ValueError,
        match="source mismatch",
    ):
        adapter.from_events((bad_source,))

    bad_provenance = event.model_copy(
        update={"project_id": "other"}
    )

    with pytest.raises(
        ValueError,
        match="provenance mismatch",
    ):
        adapter.from_events((bad_provenance,))


def test_visibility_projection_deduplicates_exact_event(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    adapter = RuntimeRecoveryClosureVisibilityAdapter()
    event = adapter.to_event(record)

    projected = adapter.from_events(
        (event, event)
    )

    assert len(projected) == 1
    assert projected[0].closure_id == record.closure_id


def test_persistence_precedes_publication_and_failure_exposes_record(
    tmp_path,
):
    class FailingVisibility:
        def publish(self, record):
            raise RuntimeError("publication unavailable")

    ctx = build(
        tmp_path,
        visibility=FailingVisibility(),
    )

    with pytest.raises(
        RuntimeRecoveryClosurePublicationError,
    ) as captured:
        close(ctx)

    persisted = ctx.closures.get(
        "project001",
        captured.value.record.closure_id,
    )

    assert persisted == captured.value.record
    assert len(ctx.closures.list("project001")) == 1


def test_safe_publication_retry_uses_persisted_record(tmp_path):
    class FlakyVisibility:
        def __init__(self):
            self.attempts = 0
            self.records = []

        def publish(self, record):
            self.attempts += 1

            if self.attempts == 1:
                raise RuntimeError("temporary failure")

            self.records.append(record)
            return record

    visibility = FlakyVisibility()
    ctx = build(
        tmp_path,
        visibility=visibility,
    )

    with pytest.raises(
        RuntimeRecoveryClosurePublicationError,
    ):
        close(ctx)

    persisted = ctx.closures.list("project001")[0]

    replayed = close(ctx)

    assert replayed == persisted
    assert visibility.attempts == 2
    assert visibility.records == [persisted]
    assert len(ctx.closures.list("project001")) == 1


def test_schema_and_canonical_serialization_integrity(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    assert RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION == 1
    assert record.schema_version == 1

    encoded = json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )

    restored = RuntimeRecoveryClosureRecord.model_validate_json(
        encoded
    )

    assert restored == record
    assert restored.checksum == record.checksum


def test_record_rejects_invalid_schema_and_checksum(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    bad_schema = record.model_dump()
    bad_schema["schema_version"] = 999

    with pytest.raises(
        ValidationError,
    ):
        RuntimeRecoveryClosureRecord(**bad_schema)

    bad_checksum = record.model_dump()
    bad_checksum["checksum"] = "0" * 64

    with pytest.raises(
        ValidationError,
    ):
        RuntimeRecoveryClosureRecord(**bad_checksum)


def test_closure_evidence_preserves_terminal_authority(tmp_path):
    ctx = build(tmp_path)
    record = close(ctx)

    assert ctx.reconciliation.reconciliation_id in (
        record.evidence_refs
    )
    assert ctx.reconciliation.checksum in (
        record.evidence_refs
    )
    assert ctx.receipt.recovery_execution_id in (
        record.evidence_refs
    )
