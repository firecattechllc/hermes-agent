"""Exact execution of approved governed runtime recovery authority."""

from __future__ import annotations

import hashlib
from typing import Optional, Protocol

from .execution import ExecutionAction, ExecutionEvidence, PolicyDecision
from .execution_planning import RoleExecutionPlan
from .runtime_execution import RuntimeExecutionRecord, RuntimeExecutionState
from .runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionRecord,
    RuntimeRecoveryExecutionState,
    RuntimeRecoveryExecutionStore,
)
from .runtime_recovery_store import (
    RuntimeRecoveryAction,
    RuntimeRecoveryRecord,
    RuntimeRecoveryState,
    RuntimeRecoveryStore,
)
from .runtime_supervision_store import RuntimeSupervisionStore, SupervisionStatus


RUNTIME_RECOVERY_EXECUTION_SCHEMA_VERSION = 1


class _RuntimeExecutionStore(Protocol):
    def get(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[RuntimeExecutionRecord]: ...


class _RuntimeCoordinator(Protocol):
    def cancel(self, **kwargs) -> RuntimeExecutionRecord: ...


class _RuntimeRecoveryExecutionVisibility(Protocol):
    def publish(self, record: RuntimeRecoveryExecutionRecord): ...


class RuntimeRecoveryExecutionError(RuntimeError):
    """Fail-closed recovery execution violation."""


class RuntimeRecoveryExecutionPublicationError(RuntimeRecoveryExecutionError):
    """Recovery execution persisted but visibility publication failed."""

    def __init__(self, record: RuntimeRecoveryExecutionRecord) -> None:
        super().__init__(
            "runtime recovery execution persisted but Mission Control "
            "publication failed; reconcile"
        )
        self.record = record


class GovernedRuntimeRecoveryExecutionCoordinator:
    """Consume approved recovery authority once and only once."""

    def __init__(
        self,
        *,
        executions: _RuntimeExecutionStore,
        runtime: _RuntimeCoordinator,
        supervisions: RuntimeSupervisionStore,
        recoveries: RuntimeRecoveryStore,
        receipts: RuntimeRecoveryExecutionStore,
        visibility: Optional[_RuntimeRecoveryExecutionVisibility] = None,
    ) -> None:
        self._executions = executions
        self._runtime = runtime
        self._supervisions = supervisions
        self._recoveries = recoveries
        self._receipts = receipts
        self._visibility = visibility

    @staticmethod
    def recovery_execution_id_for(recovery: RuntimeRecoveryRecord) -> str:
        raw = (
            f"{recovery.project_id}|{recovery.recovery_id}|"
            f"{recovery.revision}|{recovery.checksum}"
        )
        return (
            "runtime_recovery_execution_"
            + hashlib.sha256(raw.encode()).hexdigest()[:24]
        )

    def execute(
        self,
        *,
        project_id: str,
        recovery_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        correlation_id: str,
        timestamp: int,
    ) -> RuntimeRecoveryExecutionRecord:
        recovery = self._recoveries.get(project_id, recovery_id)
        if recovery is None:
            raise RuntimeRecoveryExecutionError("runtime recovery request not found")
        if recovery.project_id != project_id:
            raise RuntimeRecoveryExecutionError("runtime recovery project mismatch")
        if recovery.state != RuntimeRecoveryState.APPROVED:
            raise RuntimeRecoveryExecutionError(
                "only approved runtime recovery may execute"
            )
        if (
            recovery.authorization_id is None
            or recovery.authorized_by is None
            or recovery.authorized_at is None
        ):
            raise RuntimeRecoveryExecutionError(
                "approved runtime recovery authority is incomplete"
            )
        if timestamp < recovery.authorized_at:
            raise RuntimeRecoveryExecutionError(
                "runtime recovery execution cannot predate authorization"
            )

        recovery_execution_id = self.recovery_execution_id_for(recovery)
        existing = self._receipts.find_by_recovery(project_id, recovery_id)
        if existing is not None:
            if existing.recovery_execution_id != recovery_execution_id:
                raise RuntimeRecoveryExecutionError(
                    "runtime recovery authority contains conflicting consumption"
                )
            self._publish(existing)
            return existing

        execution = self._executions.get(project_id, recovery.execution_id)
        if execution is None:
            raise RuntimeRecoveryExecutionError("runtime execution not found")
        if execution.project_id != project_id:
            raise RuntimeRecoveryExecutionError("runtime execution project mismatch")
        if execution.state != RuntimeExecutionState.RUNNING:
            raise RuntimeRecoveryExecutionError(
                "recovery authority requires its running source execution"
            )

        recovery_history = self._recoveries.history(
            project_id,
            recovery.recovery_id,
        )
        if len(recovery_history) < 2:
            raise RuntimeRecoveryExecutionError(
                "approved runtime recovery history is incomplete"
            )

        request_revision = recovery_history[0]
        if (
            request_revision.revision != 1
            or request_revision.state
            != RuntimeRecoveryState.AWAITING_AUTHORIZATION
            or recovery.causation_id != request_revision.checksum
        ):
            raise RuntimeRecoveryExecutionError(
                "runtime recovery authorization provenance is invalid"
            )

        supervision = self._supervisions.get_latest(
            project_id,
            recovery.execution_id,
        )
        if supervision is None:
            raise RuntimeRecoveryExecutionError(
                "runtime supervision evidence is missing"
            )
        if (
            supervision.revision != recovery.supervision_revision
            or supervision.checksum != request_revision.causation_id
            or recovery.supervision_id
            != f"supervision_{supervision.checksum[:24]}"
        ):
            raise RuntimeRecoveryExecutionError(
                "runtime recovery authority is superseded"
            )
        if supervision.causation_id != execution.fingerprint:
            raise RuntimeRecoveryExecutionError(
                "runtime execution changed after recovery authorization"
            )
        if supervision.status not in {
            SupervisionStatus.STALE,
            SupervisionStatus.DEGRADED,
        }:
            raise RuntimeRecoveryExecutionError(
                "runtime recovery no longer has unhealthy supervision"
            )

        if recovery.action == RuntimeRecoveryAction.CANCEL:
            return self._execute_cancel(
                recovery=recovery,
                execution=execution,
                recovery_execution_id=recovery_execution_id,
                plan=plan,
                actor_id=actor_id,
                correlation_id=correlation_id,
                timestamp=timestamp,
            )

        reason = (
            "approved retry requires governed successor scheduling handoff"
            if recovery.action == RuntimeRecoveryAction.RETRY
            else "approved escalation requires explicit external escalation handoff"
        )
        receipt = self._receipts.create(
            recovery_execution_id=recovery_execution_id,
            project_id=project_id,
            recovery_id=recovery.recovery_id,
            recovery_revision=recovery.revision,
            execution_id=recovery.execution_id,
            source_execution_fingerprint=execution.fingerprint,
            action=recovery.action,
            state=RuntimeRecoveryExecutionState.HANDOFF_REQUIRED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=recovery.checksum,
            authorization_id=recovery.authorization_id,
            executed_at=timestamp,
            reason=reason,
            evidence_refs=(recovery.authorization_id, recovery.recovery_id),
        )
        self._publish(receipt)
        return receipt

    def _execute_cancel(
        self,
        *,
        recovery: RuntimeRecoveryRecord,
        execution: RuntimeExecutionRecord,
        recovery_execution_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        correlation_id: str,
        timestamp: int,
    ) -> RuntimeRecoveryExecutionRecord:
        evidence_id = f"recovery_evidence_{recovery.checksum[:24]}"
        evidence = ExecutionEvidence(
            evidence_id=evidence_id,
            evidence_type="runtime_recovery_authorization",
            action=ExecutionAction.VERIFY,
            attempted="consume approved runtime cancellation authority",
            output_summary="approved recovery cancellation recorded",
            timestamp=timestamp,
            successful=True,
            policy_decision=PolicyDecision.ALLOWED,
            reason=recovery.authorization_reason,
            resulting_state=RuntimeExecutionState.CANCELLED.value,
        )

        cancelled = self._runtime.cancel(
            project_id=recovery.project_id,
            execution_id=recovery.execution_id,
            plan=plan,
            actor_id=actor_id,
            timestamp=timestamp,
            summary=recovery.authorization_reason
            or "approved governed runtime recovery cancellation",
            evidence=(evidence,),
            approvals=(recovery.authorization_id,),
        )

        if cancelled.state != RuntimeExecutionState.CANCELLED:
            raise RuntimeRecoveryExecutionError(
                "runtime cancellation did not produce cancelled execution"
            )

        receipt = self._receipts.create(
            recovery_execution_id=recovery_execution_id,
            project_id=recovery.project_id,
            recovery_id=recovery.recovery_id,
            recovery_revision=recovery.revision,
            execution_id=recovery.execution_id,
            source_execution_fingerprint=execution.fingerprint,
            action=recovery.action,
            state=RuntimeRecoveryExecutionState.EXECUTED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=recovery.checksum,
            authorization_id=recovery.authorization_id,
            executed_at=timestamp,
            reason="approved governed runtime cancellation executed",
            resulting_execution_revision=cancelled.revision,
            resulting_execution_state=cancelled.state.value,
            evidence_refs=(
                recovery.authorization_id,
                recovery.recovery_id,
                evidence_id,
                cancelled.fingerprint,
            ),
        )
        self._publish(receipt)
        return receipt

    def _publish(self, record: RuntimeRecoveryExecutionRecord) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeRecoveryExecutionPublicationError(record) from exc
