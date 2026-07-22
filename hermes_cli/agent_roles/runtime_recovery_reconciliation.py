"""Fail-closed reconciliation of governed runtime recovery execution."""

from __future__ import annotations

import hashlib
from typing import Optional, Protocol, Tuple

from .runtime_execution import RuntimeExecutionRecord, RuntimeExecutionState
from .runtime_recovery_execution_store import RuntimeRecoveryExecutionRecord, RuntimeRecoveryExecutionState, RuntimeRecoveryExecutionStore
from .runtime_recovery_reconciliation_store import (
    RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION,
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
    RuntimeRecoveryReconciliationStore,
)
from .runtime_recovery_store import RuntimeRecoveryAction, RuntimeRecoveryRecord, RuntimeRecoveryState, RuntimeRecoveryStore
from .runtime_supervision_store import RuntimeSupervisionStore


class _ExecutionStore(Protocol):
    def get(self, project_id: str, execution_id: str) -> Optional[RuntimeExecutionRecord]: ...
    def history(self, project_id: str, execution_id: str) -> Tuple[RuntimeExecutionRecord, ...]: ...


class _Visibility(Protocol):
    def publish(self, record: RuntimeRecoveryReconciliationRecord): ...


class RuntimeRecoveryReconciliationError(RuntimeError):
    """Authoritative recovery facts could not be reconciled."""


class RuntimeRecoveryReconciliationPublicationError(RuntimeRecoveryReconciliationError):
    """Reconciliation persisted but Mission Control publication failed."""

    def __init__(self, record: RuntimeRecoveryReconciliationRecord) -> None:
        super().__init__("runtime recovery reconciliation persisted but Mission Control publication failed; retry publication")
        self.record = record


class GovernedRuntimeRecoveryReconciliationCoordinator:
    def __init__(self, *, executions: _ExecutionStore, supervisions: RuntimeSupervisionStore,
                 recoveries: RuntimeRecoveryStore, receipts: RuntimeRecoveryExecutionStore,
                 reconciliations: RuntimeRecoveryReconciliationStore,
                 visibility: Optional[_Visibility] = None) -> None:
        self._executions = executions
        self._supervisions = supervisions
        self._recoveries = recoveries
        self._receipts = receipts
        self._reconciliations = reconciliations
        self._visibility = visibility

    @staticmethod
    def reconciliation_id_for(receipt: RuntimeRecoveryExecutionRecord, recovery: RuntimeRecoveryRecord) -> str:
        raw = f"{receipt.project_id}|{receipt.recovery_execution_id}|{receipt.checksum}|{recovery.checksum}"
        return "runtime_recovery_reconciliation_" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    def reconcile(self, *, project_id: str, recovery_execution_id: str,
                  actor_id: str, correlation_id: str, timestamp: int) -> RuntimeRecoveryReconciliationRecord:
        try:
            receipt = self._receipts.get(project_id, recovery_execution_id)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution history is invalid") from exc
        if receipt is None:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution receipt not found")
        if receipt.project_id != project_id:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution project mismatch")

        try:
            recovery = self._recoveries.get(project_id, receipt.recovery_id)
            recovery_history = self._recoveries.history(project_id, receipt.recovery_id)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime recovery history is invalid") from exc
        if recovery is None:
            raise RuntimeRecoveryReconciliationError("runtime recovery request not found")
        self._validate_recovery(receipt, recovery, recovery_history)
        self._validate_unique_consumption(project_id, receipt)
        self._validate_supervision(project_id, recovery, recovery_history, receipt)

        reconciliation_id = self.reconciliation_id_for(receipt, recovery)
        existing = self._reconciliations.find_by_recovery_execution(project_id, recovery_execution_id)
        if existing is not None:
            if (existing.reconciliation_id != reconciliation_id or existing.actor_id != actor_id.strip()
                    or existing.correlation_id != correlation_id.strip() or existing.reconciled_at != timestamp):
                raise RuntimeRecoveryReconciliationError("conflicting runtime recovery reconciliation replay")
            self._publish(existing)
            return existing

        if timestamp < max(recovery.authorized_at or 0, receipt.executed_at):
            raise RuntimeRecoveryReconciliationError("reconciliation timestamp predates authority or receipt")

        expected_revision = expected_state = observed_revision = observed_state = None
        if receipt.action == RuntimeRecoveryAction.CANCEL:
            if receipt.state != RuntimeRecoveryExecutionState.EXECUTED:
                raise RuntimeRecoveryReconciliationError("cancellation receipt was not executed")
            execution, history = self._load_execution_history(project_id, receipt.execution_id)
            expected_revision = receipt.resulting_execution_revision
            expected_state = receipt.resulting_execution_state
            observed_revision = execution.revision
            observed_state = execution.state.value
            if execution.updated_at > timestamp:
                raise RuntimeRecoveryReconciliationError("reconciliation timestamp predates runtime result")
            if execution.state != RuntimeExecutionState.CANCELLED:
                raise RuntimeRecoveryReconciliationError("authoritative runtime execution is not cancelled")
            if execution.revision != expected_revision:
                raise RuntimeRecoveryReconciliationError("runtime cancellation revision mismatch")
            if execution.state.value != expected_state:
                raise RuntimeRecoveryReconciliationError("runtime cancellation state mismatch")
            pre_revision = execution.revision - 1
            source = next((item for item in history if item.revision == pre_revision), None)
            if source is None or source.fingerprint != receipt.source_execution_fingerprint:
                raise RuntimeRecoveryReconciliationError("source execution fingerprint is not in pre-cancellation history")
            state = RuntimeRecoveryReconciliationState.RECONCILED
            reason = "approved cancellation receipt matches authoritative cancelled runtime execution"
        else:
            if receipt.state != RuntimeRecoveryExecutionState.HANDOFF_REQUIRED:
                raise RuntimeRecoveryReconciliationError("retry or escalation receipt falsely claims execution")
            execution, history = self._load_execution_history(project_id, receipt.execution_id)
            source = next((item for item in history if item.fingerprint == receipt.source_execution_fingerprint), None)
            if source is None:
                raise RuntimeRecoveryReconciliationError("source execution fingerprint is absent from runtime history")
            if source.updated_at > receipt.executed_at or execution.updated_at > timestamp:
                raise RuntimeRecoveryReconciliationError("handoff reconciliation timestamp predates source execution")
            state = RuntimeRecoveryReconciliationState.HANDOFF_PENDING
            reason = ("governed successor scheduling handoff remains pending" if receipt.action == RuntimeRecoveryAction.RETRY
                      else "explicit external escalation handoff remains pending")

        values = dict(
            reconciliation_id=reconciliation_id,
            schema_version=RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION,
            project_id=project_id,
            recovery_execution_id=receipt.recovery_execution_id,
            recovery_id=recovery.recovery_id,
            recovery_revision=recovery.revision,
            execution_id=receipt.execution_id,
            action=receipt.action,
            recovery_execution_state=receipt.state,
            state=state,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=receipt.checksum,
            authorization_id=recovery.authorization_id,
            reconciled_at=timestamp,
            source_recovery_checksum=recovery.checksum,
            source_recovery_execution_checksum=receipt.checksum,
            expected_execution_revision=expected_revision,
            expected_execution_state=expected_state,
            observed_execution_revision=observed_revision,
            observed_execution_state=observed_state,
            reason=reason,
            evidence_refs=tuple(dict.fromkeys((*receipt.evidence_refs, recovery.checksum, receipt.checksum))),
            revision=1,
            previous_checksum=None,
        )
        record = RuntimeRecoveryReconciliationRecord(**values, checksum=RuntimeRecoveryReconciliationRecord.calculate_checksum(**values))
        try:
            persisted = self._reconciliations.append(record)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime recovery reconciliation persistence failed") from exc
        self._publish(persisted)
        return persisted

    @staticmethod
    def _validate_recovery(receipt, recovery, history) -> None:
        if recovery.project_id != receipt.project_id:
            raise RuntimeRecoveryReconciliationError("runtime recovery project mismatch")
        if recovery.recovery_id != receipt.recovery_id:
            raise RuntimeRecoveryReconciliationError("runtime recovery identifier mismatch")
        if recovery.revision != receipt.recovery_revision:
            raise RuntimeRecoveryReconciliationError("runtime recovery revision mismatch")
        if recovery.execution_id != receipt.execution_id:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution identifier mismatch")
        if recovery.action != receipt.action:
            raise RuntimeRecoveryReconciliationError("runtime recovery action mismatch")
        if recovery.state != RuntimeRecoveryState.APPROVED:
            raise RuntimeRecoveryReconciliationError("runtime recovery is not approved")
        if recovery.authorization_id != receipt.authorization_id:
            raise RuntimeRecoveryReconciliationError("runtime recovery authorization mismatch")
        if receipt.causation_id != recovery.checksum:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution causation mismatch")
        if len(history) != recovery.revision or tuple(item.revision for item in history) != tuple(range(1, recovery.revision + 1)):
            raise RuntimeRecoveryReconciliationError("runtime recovery revision history is broken")
        if history[-1] != recovery or recovery.causation_id != history[-2].checksum:
            raise RuntimeRecoveryReconciliationError("runtime recovery provenance is broken")
        if receipt.executed_at < (recovery.authorized_at or 0):
            raise RuntimeRecoveryReconciliationError("runtime recovery receipt predates authorization")

    def _validate_unique_consumption(self, project_id, receipt) -> None:
        try:
            matches = tuple(item for item in self._receipts.list(project_id) if item.recovery_id == receipt.recovery_id)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime recovery execution history is invalid") from exc
        if len(matches) != 1 or matches[0] != receipt:
            raise RuntimeRecoveryReconciliationError("recovery authority was not consumed exactly once")

    def _validate_supervision(self, project_id, recovery, recovery_history, receipt) -> None:
        try:
            history = self._supervisions.history(project_id, recovery.execution_id)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime supervision provenance is invalid") from exc
        supervision = next((item for item in history if item.revision == recovery.supervision_revision), None)
        request = recovery_history[0]
        if tuple(item.revision for item in history) != tuple(range(1, len(history) + 1)):
            raise RuntimeRecoveryReconciliationError("runtime supervision revision history is broken")
        if (supervision is None or request.causation_id != supervision.checksum
                or recovery.supervision_id != f"supervision_{supervision.checksum[:24]}"):
            raise RuntimeRecoveryReconciliationError("runtime supervision provenance mismatch")
        if supervision.causation_id != receipt.source_execution_fingerprint:
            raise RuntimeRecoveryReconciliationError("runtime supervision source execution fingerprint mismatch")
        if request.requested_at < supervision.observed_at:
            raise RuntimeRecoveryReconciliationError("runtime recovery request predates supervision")

    def _load_execution_history(self, project_id, execution_id):
        try:
            execution = self._executions.get(project_id, execution_id)
            history = self._executions.history(project_id, execution_id)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationError("runtime execution history is invalid") from exc
        if execution is None or not history:
            raise RuntimeRecoveryReconciliationError("authoritative runtime execution not found")
        if execution.project_id != project_id or execution.execution_id != execution_id:
            raise RuntimeRecoveryReconciliationError("runtime execution project or identifier mismatch")
        revisions = tuple(item.revision for item in history)
        if revisions != tuple(range(1, len(history) + 1)) or history[-1] != execution:
            raise RuntimeRecoveryReconciliationError("runtime execution revision history is broken")
        return execution, history

    def _publish(self, record) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeRecoveryReconciliationPublicationError(record) from exc
