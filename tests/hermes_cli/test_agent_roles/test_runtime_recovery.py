"""Step 15 governed runtime recovery certification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli.agent_roles.runtime_execution import RuntimeExecutionState
from hermes_cli.agent_roles.runtime_recovery import (
    GovernedRuntimeRecoveryCoordinator,
    RuntimeRecoveryAuthorization,
    RuntimeRecoveryError,
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


def execution(*, state=RuntimeExecutionState.RUNNING, fingerprint="e" * 64):
    return SimpleNamespace(
        project_id="project001",
        execution_id="execution001",
        state=state,
        fingerprint=fingerprint,
    )


def build(tmp_path, *, status=SupervisionStatus.STALE):
    current = execution()
    executions = ExecutionStore(current)
    supervisions = RuntimeSupervisionStore(tmp_path / "supervisions")
    supervision = supervisions.observe(
        project_id=current.project_id,
        execution_id=current.execution_id,
        status=status,
        actor_id="supervisor",
        correlation_id="run001",
        causation_id=current.fingerprint,
        observed_at=2000,
        last_heartbeat_at=1000,
        started_at=1000,
        heartbeat_threshold_seconds=600,
        reason="execution heartbeat is stale",
    )
    recoveries = RuntimeRecoveryStore(tmp_path / "recoveries")
    coordinator = GovernedRuntimeRecoveryCoordinator(
        executions=executions,
        supervisions=supervisions,
        recoveries=recoveries,
    )
    return current, supervision, recoveries, coordinator


def request(coordinator):
    return coordinator.request(
        project_id="project001",
        execution_id="execution001",
        action=RuntimeRecoveryAction.CANCEL,
        actor_id="operator",
        correlation_id="run001",
        timestamp=2100,
        reason="request cancellation review",
    )


def test_request_requires_unhealthy_supervision(tmp_path):
    _, _, _, coordinator = build(tmp_path, status=SupervisionStatus.HEALTHY)

    with pytest.raises(RuntimeRecoveryError, match="stale or degraded"):
        request(coordinator)


def test_request_is_pending_and_does_not_mutate_execution(tmp_path):
    current, _, _, coordinator = build(tmp_path)

    record = request(coordinator)

    assert record.state == RuntimeRecoveryState.AWAITING_AUTHORIZATION
    assert record.action == RuntimeRecoveryAction.CANCEL
    assert record.revision == 1
    assert current.state == RuntimeExecutionState.RUNNING


def test_request_is_idempotent(tmp_path):
    _, _, recoveries, coordinator = build(tmp_path)

    first = request(coordinator)
    second = request(coordinator)

    assert first == second
    assert len(recoveries.history("project001", first.recovery_id)) == 1


def test_approve_requires_exact_revision(tmp_path):
    _, _, _, coordinator = build(tmp_path)
    pending = request(coordinator)

    authorization = RuntimeRecoveryAuthorization(
        authorization_id="auth001",
        project_id="project001",
        recovery_id=pending.recovery_id,
        expected_revision=99,
        decision=RuntimeRecoveryDecision.APPROVED,
        actor_id="owner",
        timestamp=2200,
        reason="approved after review",
    )

    with pytest.raises(RuntimeRecoveryError, match="stale"):
        coordinator.authorize(authorization=authorization)


def test_approval_is_journaled_without_execution(tmp_path):
    current, _, recoveries, coordinator = build(tmp_path)
    pending = request(coordinator)

    approved = coordinator.authorize(
        authorization=RuntimeRecoveryAuthorization(
            authorization_id="auth001",
            project_id="project001",
            recovery_id=pending.recovery_id,
            expected_revision=1,
            decision=RuntimeRecoveryDecision.APPROVED,
            actor_id="owner",
            timestamp=2200,
            reason="approved after review",
        )
    )

    assert approved.state == RuntimeRecoveryState.APPROVED
    assert approved.revision == 2
    assert approved.authorization_id == "auth001"
    assert current.state == RuntimeExecutionState.RUNNING
    assert len(recoveries.history("project001", pending.recovery_id)) == 2


def test_denial_is_terminal_governance_decision(tmp_path):
    _, _, _, coordinator = build(tmp_path)
    pending = request(coordinator)

    denied = coordinator.authorize(
        authorization=RuntimeRecoveryAuthorization(
            authorization_id="auth-denied",
            project_id="project001",
            recovery_id=pending.recovery_id,
            expected_revision=1,
            decision=RuntimeRecoveryDecision.DENIED,
            actor_id="owner",
            timestamp=2200,
            reason="insufficient evidence",
        )
    )

    assert denied.state == RuntimeRecoveryState.DENIED


def test_newer_supervision_supersedes_pending_recovery(tmp_path):
    current, _, _, coordinator = build(tmp_path)
    pending = request(coordinator)

    coordinator._supervisions.observe(
        project_id=current.project_id,
        execution_id=current.execution_id,
        status=SupervisionStatus.RECOVERED,
        actor_id="supervisor",
        correlation_id="run001",
        causation_id=current.fingerprint,
        observed_at=2150,
        last_heartbeat_at=2140,
        started_at=1000,
        heartbeat_threshold_seconds=600,
        reason="heartbeat recovered",
    )

    authorization = RuntimeRecoveryAuthorization(
        authorization_id="auth001",
        project_id="project001",
        recovery_id=pending.recovery_id,
        expected_revision=1,
        decision=RuntimeRecoveryDecision.APPROVED,
        actor_id="owner",
        timestamp=2200,
        reason="approve stale request",
    )

    with pytest.raises(RuntimeRecoveryError, match="superseded"):
        coordinator.authorize(authorization=authorization)


def test_terminal_execution_cannot_be_authorized(tmp_path):
    current, _, _, coordinator = build(tmp_path)
    pending = request(coordinator)
    current.state = RuntimeExecutionState.CANCELLED

    authorization = RuntimeRecoveryAuthorization(
        authorization_id="auth001",
        project_id="project001",
        recovery_id=pending.recovery_id,
        expected_revision=1,
        decision=RuntimeRecoveryDecision.APPROVED,
        actor_id="owner",
        timestamp=2200,
        reason="approve cancellation",
    )

    with pytest.raises(RuntimeRecoveryError, match="terminal execution"):
        coordinator.authorize(authorization=authorization)


def test_project_isolation(tmp_path):
    _, _, recoveries, coordinator = build(tmp_path)
    pending = request(coordinator)

    assert recoveries.get("other-project", pending.recovery_id) is None


def test_torn_tail_recovery(tmp_path):
    _, _, recoveries, coordinator = build(tmp_path)
    pending = request(coordinator)

    path = recoveries.journal_path("project001")
    with path.open("ab") as handle:
        handle.write(b'{"partial":')

    loaded = recoveries.get("project001", pending.recovery_id)

    assert loaded == pending
    assert path.read_bytes().endswith(b"\n")


def test_checksum_tampering_fails_closed(tmp_path):
    _, _, recoveries, coordinator = build(tmp_path)
    request(coordinator)

    path = recoveries.journal_path("project001")
    text = path.read_text()
    path.write_text(text.replace('"requested_by":"operator"', '"requested_by":"intruder"', 1))

    with pytest.raises(ValueError, match="corrupt runtime recovery journal"):
        recoveries.list("project001")
