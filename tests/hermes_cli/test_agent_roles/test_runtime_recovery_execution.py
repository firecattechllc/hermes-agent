"""Step 16 governed runtime recovery execution certification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli.agent_roles.runtime_execution import RuntimeExecutionState
from hermes_cli.agent_roles.runtime_recovery_execution import (
    GovernedRuntimeRecoveryExecutionCoordinator,
    RuntimeRecoveryExecutionError,
)
from hermes_cli.agent_roles.runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionState,
    RuntimeRecoveryExecutionStore,
)
from hermes_cli.agent_roles.runtime_recovery_store import (
    RuntimeRecoveryAction,
    RuntimeRecoveryDecision,
    RuntimeRecoveryState,
    RuntimeRecoveryStore,
)
from hermes_cli.agent_roles.runtime_supervision_store import (
    RuntimeSupervisionStore,
    SupervisionStatus,
)


class ExecutionStore:
    def __init__(self, record):
        self.record = record

    def get(self, project_id, execution_id):
        if (
            self.record.project_id == project_id
            and self.record.execution_id == execution_id
        ):
            return self.record
        return None


class FakeRuntime:
    def __init__(self, execution):
        self.execution = execution
        self.cancel_calls = 0

    def cancel(self, **kwargs):
        self.cancel_calls += 1
        self.execution.state = RuntimeExecutionState.CANCELLED
        self.execution.revision += 1
        self.execution.fingerprint = "c" * 64
        return self.execution


def build(tmp_path, *, action=RuntimeRecoveryAction.CANCEL):
    execution = SimpleNamespace(
        project_id="project001",
        execution_id="execution001",
        revision=3,
        state=RuntimeExecutionState.RUNNING,
        fingerprint="e" * 64,
    )
    executions = ExecutionStore(execution)
    runtime = FakeRuntime(execution)

    supervisions = RuntimeSupervisionStore(tmp_path / "supervisions")
    supervision = supervisions.observe(
        project_id="project001",
        execution_id="execution001",
        status=SupervisionStatus.STALE,
        actor_id="supervisor",
        correlation_id="run001",
        causation_id=execution.fingerprint,
        observed_at=2000,
        last_heartbeat_at=1000,
        started_at=1000,
        heartbeat_threshold_seconds=600,
        reason="execution heartbeat is stale",
    )

    recoveries = RuntimeRecoveryStore(tmp_path / "recoveries")
    pending = recoveries.create(
        recovery_id=f"recovery-{action.value}",
        project_id="project001",
        execution_id="execution001",
        supervision_id=f"supervision_{supervision.checksum[:24]}",
        supervision_revision=supervision.revision,
        action=action,
        requested_by="operator",
        requested_at=2100,
        request_reason="recovery review requested",
        correlation_id="run001",
        causation_id=supervision.checksum,
    )
    approved = recoveries.decide(
        project_id="project001",
        recovery_id=pending.recovery_id,
        expected_revision=1,
        decision=RuntimeRecoveryDecision.APPROVED,
        authorization_id="auth001",
        authorized_by="owner",
        authorized_at=2200,
        authorization_reason="approved recovery",
    )
    assert approved.state == RuntimeRecoveryState.APPROVED

    receipts = RuntimeRecoveryExecutionStore(tmp_path / "receipts")
    coordinator = GovernedRuntimeRecoveryExecutionCoordinator(
        executions=executions,
        runtime=runtime,
        supervisions=supervisions,
        recoveries=recoveries,
        receipts=receipts,
    )
    return execution, runtime, supervisions, recoveries, receipts, coordinator


def execute(coordinator, recovery_id):
    return coordinator.execute(
        project_id="project001",
        recovery_id=recovery_id,
        plan=SimpleNamespace(),
        actor_id="recovery-executor",
        correlation_id="run001",
        timestamp=2300,
    )


def test_cancel_consumes_authority_and_cancels_once(tmp_path):
    execution, runtime, _, recoveries, receipts, coordinator = build(tmp_path)
    recovery = recoveries.get("project001", "recovery-cancel")

    receipt = execute(coordinator, recovery.recovery_id)

    assert receipt.state == RuntimeRecoveryExecutionState.EXECUTED
    assert receipt.resulting_execution_state == "cancelled"
    assert execution.state == RuntimeExecutionState.CANCELLED
    assert runtime.cancel_calls == 1
    assert receipts.find_by_recovery("project001", recovery.recovery_id) == receipt


def test_cancel_replay_returns_same_receipt_without_second_cancel(tmp_path):
    _, runtime, _, recoveries, _, coordinator = build(tmp_path)
    recovery = recoveries.get("project001", "recovery-cancel")

    first = execute(coordinator, recovery.recovery_id)
    second = execute(coordinator, recovery.recovery_id)

    assert first == second
    assert runtime.cancel_calls == 1


@pytest.mark.parametrize(
    "action",
    [RuntimeRecoveryAction.RETRY, RuntimeRecoveryAction.ESCALATE],
)
def test_non_cancel_actions_create_handoff_without_runtime_mutation(
    tmp_path,
    action,
):
    execution, runtime, _, recoveries, _, coordinator = build(
        tmp_path,
        action=action,
    )
    recovery = recoveries.get("project001", f"recovery-{action.value}")

    receipt = execute(coordinator, recovery.recovery_id)

    assert receipt.state == RuntimeRecoveryExecutionState.HANDOFF_REQUIRED
    assert receipt.resulting_execution_state is None
    assert execution.state == RuntimeExecutionState.RUNNING
    assert runtime.cancel_calls == 0


def test_pending_recovery_cannot_execute(tmp_path):
    _, _, _, recoveries, _, coordinator = build(tmp_path)
    approved = recoveries.get("project001", "recovery-cancel")

    path = recoveries.journal_path("project001")
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n")

    with pytest.raises(RuntimeRecoveryExecutionError, match="only approved"):
        execute(coordinator, approved.recovery_id)


def test_newer_supervision_supersedes_authority(tmp_path):
    execution, _, supervisions, recoveries, _, coordinator = build(tmp_path)
    recovery = recoveries.get("project001", "recovery-cancel")

    supervisions.observe(
        project_id="project001",
        execution_id="execution001",
        status=SupervisionStatus.RECOVERED,
        actor_id="supervisor",
        correlation_id="run001",
        causation_id=execution.fingerprint,
        observed_at=2250,
        last_heartbeat_at=2240,
        started_at=1000,
        heartbeat_threshold_seconds=600,
        reason="execution recovered",
    )

    with pytest.raises(RuntimeRecoveryExecutionError, match="superseded"):
        execute(coordinator, recovery.recovery_id)


def test_execution_fingerprint_change_invalidates_authority(tmp_path):
    execution, _, _, recoveries, _, coordinator = build(tmp_path)
    recovery = recoveries.get("project001", "recovery-cancel")
    execution.fingerprint = "x" * 64

    with pytest.raises(RuntimeRecoveryExecutionError, match="changed"):
        execute(coordinator, recovery.recovery_id)


def test_torn_tail_recovery(tmp_path):
    _, _, _, recoveries, receipts, coordinator = build(
        tmp_path,
        action=RuntimeRecoveryAction.RETRY,
    )
    recovery = recoveries.get("project001", "recovery-retry")
    receipt = execute(coordinator, recovery.recovery_id)

    with receipts.journal_path("project001").open("ab") as handle:
        handle.write(b'{"partial":')

    loaded = receipts.find_by_recovery("project001", recovery.recovery_id)

    assert loaded == receipt
    assert receipts.journal_path("project001").read_bytes().endswith(b"\n")


def test_checksum_tampering_fails_closed(tmp_path):
    _, _, _, recoveries, receipts, coordinator = build(
        tmp_path,
        action=RuntimeRecoveryAction.RETRY,
    )
    recovery = recoveries.get("project001", "recovery-retry")
    execute(coordinator, recovery.recovery_id)

    path = receipts.journal_path("project001")
    path.write_text(
        path.read_text().replace(
            '"actor_id":"recovery-executor"',
            '"actor_id":"intruder"',
            1,
        )
    )

    with pytest.raises(
        ValueError,
        match="corrupt runtime recovery execution journal",
    ):
        receipts.list("project001")
