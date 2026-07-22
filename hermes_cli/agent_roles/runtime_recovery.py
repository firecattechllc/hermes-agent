"""Governed recovery proposals and explicit authorization.

This boundary records recovery intent for unhealthy runtime executions. It does
not cancel, retry, restart, promote, deploy, or mutate runtime execution state.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .runtime_execution import RuntimeExecutionRecord, RuntimeExecutionState
from .runtime_recovery_store import (
    RuntimeRecoveryAction,
    RuntimeRecoveryDecision,
    RuntimeRecoveryRecord,
    RuntimeRecoveryState,
    RuntimeRecoveryStore,
)
from .runtime_supervision_store import (
    RuntimeSupervisionStore,
    SupervisionJournalRecord,
    SupervisionStatus,
)


RUNTIME_RECOVERY_SCHEMA_VERSION = 1


class RuntimeRecoveryAuthorization(BaseModel):
    """Explicit authority for one exact recovery request revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    expected_revision: int = Field(..., ge=1)
    decision: RuntimeRecoveryDecision
    actor_id: str = Field(..., min_length=1, max_length=256)
    timestamp: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)


class _RuntimeExecutionStore(Protocol):
    def get(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[RuntimeExecutionRecord]: ...


class _RuntimeRecoveryVisibility(Protocol):
    def publish(self, record: RuntimeRecoveryRecord): ...


class RuntimeRecoveryError(RuntimeError):
    """Fail-closed recovery governance violation."""


class RuntimeRecoveryPublicationError(RuntimeRecoveryError):
    """Recovery revision persisted but visibility publication failed."""

    def __init__(self, record: RuntimeRecoveryRecord) -> None:
        super().__init__(
            "runtime recovery persisted but Mission Control publication failed; reconcile"
        )
        self.record = record


class GovernedRuntimeRecoveryCoordinator:
    """Create and authorize recovery proposals without executing them."""

    def __init__(
        self,
        *,
        executions: _RuntimeExecutionStore,
        supervisions: RuntimeSupervisionStore,
        recoveries: RuntimeRecoveryStore,
        visibility: Optional[_RuntimeRecoveryVisibility] = None,
    ) -> None:
        self._executions = executions
        self._supervisions = supervisions
        self._recoveries = recoveries
        self._visibility = visibility

    @staticmethod
    def recovery_id_for(
        *,
        project_id: str,
        execution_id: str,
        supervision: SupervisionJournalRecord,
        action: RuntimeRecoveryAction,
    ) -> str:
        raw = (
            f"{project_id}|{execution_id}|{supervision.revision}|"
            f"{supervision.checksum}|{action.value}"
        )
        return f"recovery_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    def request(
        self,
        *,
        project_id: str,
        execution_id: str,
        action: RuntimeRecoveryAction,
        actor_id: str,
        correlation_id: str,
        timestamp: int,
        reason: str,
    ) -> RuntimeRecoveryRecord:
        execution = self._executions.get(project_id, execution_id)
        if execution is None:
            raise RuntimeRecoveryError("runtime execution not found")
        if execution.project_id != project_id:
            raise RuntimeRecoveryError("runtime execution project mismatch")
        if execution.state != RuntimeExecutionState.RUNNING:
            raise RuntimeRecoveryError(
                "recovery may only be requested for a running execution"
            )

        supervision = self._supervisions.get_latest(project_id, execution_id)
        if supervision is None:
            raise RuntimeRecoveryError("runtime supervision evidence is required")
        if supervision.causation_id != execution.fingerprint:
            raise RuntimeRecoveryError("runtime supervision evidence is stale")
        if supervision.status not in {
            SupervisionStatus.STALE,
            SupervisionStatus.DEGRADED,
        }:
            raise RuntimeRecoveryError(
                "recovery requires stale or degraded supervision evidence"
            )
        if timestamp < supervision.observed_at:
            raise RuntimeRecoveryError(
                "runtime recovery request cannot predate supervision evidence"
            )

        recovery_id = self.recovery_id_for(
            project_id=project_id,
            execution_id=execution_id,
            supervision=supervision,
            action=action,
        )
        record = self._recoveries.create(
            recovery_id=recovery_id,
            project_id=project_id,
            execution_id=execution_id,
            supervision_id=f"supervision_{supervision.checksum[:24]}",
            supervision_revision=supervision.revision,
            action=action,
            requested_by=actor_id,
            requested_at=timestamp,
            request_reason=reason,
            correlation_id=correlation_id,
            causation_id=supervision.checksum,
        )
        self._publish(record)
        return record

    def authorize(
        self,
        *,
        authorization: RuntimeRecoveryAuthorization,
    ) -> RuntimeRecoveryRecord:
        current = self._recoveries.get(
            authorization.project_id,
            authorization.recovery_id,
        )
        if current is None:
            raise RuntimeRecoveryError("runtime recovery request not found")
        if current.project_id != authorization.project_id:
            raise RuntimeRecoveryError("runtime recovery authorization project mismatch")
        if current.recovery_id != authorization.recovery_id:
            raise RuntimeRecoveryError("runtime recovery authorization mismatch")
        if current.revision != authorization.expected_revision:
            raise RuntimeRecoveryError("runtime recovery authorization is stale")
        if authorization.timestamp < current.requested_at:
            raise RuntimeRecoveryError(
                "runtime recovery authorization cannot predate request"
            )

        execution = self._executions.get(current.project_id, current.execution_id)
        if execution is None:
            raise RuntimeRecoveryError("runtime execution not found")
        if execution.state != RuntimeExecutionState.RUNNING:
            raise RuntimeRecoveryError(
                "terminal execution cannot receive recovery authorization"
            )

        supervision = self._supervisions.get_latest(
            current.project_id,
            current.execution_id,
        )
        if supervision is None:
            raise RuntimeRecoveryError("runtime supervision evidence is missing")
        if (
            supervision.revision != current.supervision_revision
            or supervision.checksum != current.causation_id
        ):
            raise RuntimeRecoveryError(
                "runtime recovery request is superseded by newer supervision"
            )
        if supervision.status not in {
            SupervisionStatus.STALE,
            SupervisionStatus.DEGRADED,
        }:
            raise RuntimeRecoveryError(
                "runtime recovery request no longer has unhealthy supervision"
            )

        record = self._recoveries.decide(
            project_id=current.project_id,
            recovery_id=current.recovery_id,
            expected_revision=authorization.expected_revision,
            decision=authorization.decision,
            authorization_id=authorization.authorization_id,
            authorized_by=authorization.actor_id,
            authorized_at=authorization.timestamp,
            authorization_reason=authorization.reason,
        )
        self._publish(record)
        return record

    def get(
        self,
        project_id: str,
        recovery_id: str,
    ) -> Optional[RuntimeRecoveryRecord]:
        return self._recoveries.get(project_id, recovery_id)

    def list_pending(self, project_id: str) -> Tuple[RuntimeRecoveryRecord, ...]:
        return self._recoveries.list(
            project_id,
            state=RuntimeRecoveryState.AWAITING_AUTHORIZATION,
        )

    def _publish(self, record: RuntimeRecoveryRecord) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeRecoveryPublicationError(record) from exc
