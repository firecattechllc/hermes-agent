"""Fail-closed terminal closure of governed runtime recovery."""

from __future__ import annotations

import hashlib
from typing import Optional, Protocol

from .runtime_recovery_closure_store import (
    RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION,
    RuntimeRecoveryClosureRecord,
    RuntimeRecoveryClosureState,
    RuntimeRecoveryClosureStore,
)
from .runtime_recovery_reconciliation_store import (
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
    RuntimeRecoveryReconciliationStore,
)
from .runtime_recovery_store import RuntimeRecoveryAction


class _Visibility(Protocol):
    def publish(self, record: RuntimeRecoveryClosureRecord): ...


class RuntimeRecoveryClosureError(RuntimeError):
    """Authoritative runtime recovery could not be terminally closed."""


class RuntimeRecoveryClosurePublicationError(RuntimeRecoveryClosureError):
    """Closure persisted but Mission Control publication failed."""

    def __init__(self, record: RuntimeRecoveryClosureRecord) -> None:
        super().__init__(
            "runtime recovery closure persisted but Mission Control publication "
            "failed; retry publication"
        )
        self.record = record


class GovernedRuntimeRecoveryClosureCoordinator:
    """Consumes a reconciled recovery outcome and emits one terminal closure."""

    def __init__(
        self,
        *,
        reconciliations: RuntimeRecoveryReconciliationStore,
        closures: RuntimeRecoveryClosureStore,
        visibility: Optional[_Visibility] = None,
    ) -> None:
        self._reconciliations = reconciliations
        self._closures = closures
        self._visibility = visibility

    @staticmethod
    def closure_id_for(
        reconciliation: RuntimeRecoveryReconciliationRecord,
    ) -> str:
        raw = (
            f"{reconciliation.project_id}|"
            f"{reconciliation.reconciliation_id}|"
            f"{reconciliation.checksum}"
        )
        return (
            "runtime_recovery_closure_"
            + hashlib.sha256(raw.encode()).hexdigest()[:24]
        )

    def close(
        self,
        *,
        project_id: str,
        reconciliation_id: str,
        actor_id: str,
        correlation_id: str,
        timestamp: int,
    ) -> RuntimeRecoveryClosureRecord:
        project_id = project_id.strip()
        reconciliation_id = reconciliation_id.strip()
        actor_id = actor_id.strip()
        correlation_id = correlation_id.strip()

        if not project_id:
            raise RuntimeRecoveryClosureError("project identifier is required")
        if not reconciliation_id:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation identifier is required"
            )
        if not actor_id:
            raise RuntimeRecoveryClosureError("closure actor is required")
        if not correlation_id:
            raise RuntimeRecoveryClosureError(
                "closure correlation identifier is required"
            )
        if timestamp < 0:
            raise RuntimeRecoveryClosureError(
                "closure timestamp must be non-negative"
            )

        try:
            reconciliation = self._reconciliations.get(
                project_id,
                reconciliation_id,
            )
        except Exception as exc:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation history is invalid"
            ) from exc

        if reconciliation is None:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation not found"
            )

        self._validate_reconciliation(
            project_id=project_id,
            reconciliation_id=reconciliation_id,
            reconciliation=reconciliation,
        )

        closure_id = self.closure_id_for(reconciliation)

        try:
            existing = self._closures.find_by_reconciliation(
                project_id,
                reconciliation_id,
            )
        except Exception as exc:
            raise RuntimeRecoveryClosureError(
                "runtime recovery closure history is invalid"
            ) from exc

        if existing is not None:
            if (
                existing.closure_id != closure_id
                or existing.actor_id != actor_id
                or existing.correlation_id != correlation_id
                or existing.closed_at != timestamp
            ):
                raise RuntimeRecoveryClosureError(
                    "conflicting runtime recovery closure replay"
                )

            self._publish(existing)
            return existing

        if timestamp < reconciliation.reconciled_at:
            raise RuntimeRecoveryClosureError(
                "runtime recovery closure timestamp predates reconciliation"
            )

        values = dict(
            closure_id=closure_id,
            schema_version=RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION,
            project_id=project_id,
            reconciliation_id=reconciliation.reconciliation_id,
            recovery_execution_id=reconciliation.recovery_execution_id,
            recovery_id=reconciliation.recovery_id,
            execution_id=reconciliation.execution_id,
            action=reconciliation.action,
            reconciliation_state=reconciliation.state,
            state=RuntimeRecoveryClosureState.CLOSED,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=reconciliation.checksum,
            closed_at=timestamp,
            source_reconciliation_checksum=reconciliation.checksum,
            reason=(
                "governed runtime recovery lifecycle closed after authoritative "
                "cancellation reconciliation"
            ),
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *reconciliation.evidence_refs,
                        reconciliation.recovery_execution_id,
                        reconciliation.reconciliation_id,
                        reconciliation.checksum,
                    )
                )
            ),
            revision=1,
            previous_checksum=None,
        )

        record = RuntimeRecoveryClosureRecord(
            **values,
            checksum=RuntimeRecoveryClosureRecord.calculate_checksum(**values),
        )

        try:
            persisted = self._closures.append(record)
        except Exception as exc:
            raise RuntimeRecoveryClosureError(
                "runtime recovery closure persistence failed"
            ) from exc

        self._publish(persisted)
        return persisted

    @staticmethod
    def _validate_reconciliation(
        *,
        project_id: str,
        reconciliation_id: str,
        reconciliation: RuntimeRecoveryReconciliationRecord,
    ) -> None:
        try:
            validated = RuntimeRecoveryReconciliationRecord.model_validate(
                reconciliation.model_dump()
            )
        except Exception as exc:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation record is invalid"
            ) from exc

        if validated.project_id != project_id:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation project mismatch"
            )

        if validated.reconciliation_id != reconciliation_id:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation identifier mismatch"
            )

        if validated.state != RuntimeRecoveryReconciliationState.RECONCILED:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation is not terminal"
            )

        if validated.action != RuntimeRecoveryAction.CANCEL:
            raise RuntimeRecoveryClosureError(
                "only reconciled cancellation recovery may be closed"
            )

        if (
            validated.expected_execution_revision
            != validated.observed_execution_revision
        ):
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation revision result is inconsistent"
            )

        if (
            validated.expected_execution_state
            != validated.observed_execution_state
        ):
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation state result is inconsistent"
            )

        if validated.expected_execution_revision is None:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation lacks terminal execution evidence"
            )

        if validated.expected_execution_state != "cancelled":
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation did not prove cancellation"
            )

        if validated.causation_id != validated.source_recovery_execution_checksum:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation provenance is broken"
            )

        if validated.revision != 1 or validated.previous_checksum is not None:
            raise RuntimeRecoveryClosureError(
                "runtime recovery reconciliation history is not initial and immutable"
            )

    def _publish(
        self,
        record: RuntimeRecoveryClosureRecord,
    ) -> None:
        if self._visibility is None:
            return

        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeRecoveryClosurePublicationError(record) from exc
