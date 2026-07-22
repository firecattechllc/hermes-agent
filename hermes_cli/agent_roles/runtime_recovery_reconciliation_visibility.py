"""Mission Control projection for runtime recovery reconciliation."""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_recovery_reconciliation_store import RuntimeRecoveryReconciliationRecord, RuntimeRecoveryReconciliationState


RUNTIME_RECOVERY_RECONCILIATION_VISIBILITY_EVENT = "runtime_recovery_reconciliation_recorded"


class RuntimeRecoveryReconciliationVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reconciliation_id: str
    schema_version: int
    project_id: str
    execution_id: str
    recovery_id: str
    recovery_revision: int
    recovery_execution_id: str
    action: str
    recovery_execution_state: str
    reconciliation_state: str
    actor_id: str
    correlation_id: str
    causation_id: str
    authorization_id: str
    expected_execution_revision: Optional[int]
    expected_execution_state: Optional[str]
    observed_execution_revision: Optional[int]
    observed_execution_state: Optional[str]
    reason: str
    evidence_refs: Tuple[str, ...]
    reconciled_at: int
    revision: int
    previous_checksum: Optional[str]
    source_recovery_checksum: str
    source_recovery_execution_checksum: str
    checksum: str


class RuntimeRecoveryReconciliationVisibilityAdapter:
    def to_event(self, record: RuntimeRecoveryReconciliationRecord) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(f"{record.reconciliation_id}|{record.checksum}".encode()).hexdigest()[:24]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_runtime_recovery_reconciliation_{digest}",
            event_type=RUNTIME_RECOVERY_RECONCILIATION_VISIBILITY_EVENT,
            project_id=record.project_id, actor_id=record.actor_id,
            timestamp=record.reconciled_at,
            severity="warning" if record.state != RuntimeRecoveryReconciliationState.RECONCILED else "info",
            correlation_id=record.correlation_id,
            payload={"source": "runtime_recovery_reconciliation",
                     "source_idempotency_key": f"runtime_recovery_reconciliation:{record.reconciliation_id}:{record.revision}",
                     "runtime_recovery_reconciliation": record.model_dump(mode="json")},
        )

    def from_events(self, events) -> Tuple[RuntimeRecoveryReconciliationVisibilityRecord, ...]:
        records = []
        seen: set[str] = set()
        for event in events:
            if event.event_type != RUNTIME_RECOVERY_RECONCILIATION_VISIBILITY_EVENT:
                continue
            if event.payload.get("source") != "runtime_recovery_reconciliation":
                raise ValueError("runtime recovery reconciliation visibility source mismatch")
            record = RuntimeRecoveryReconciliationRecord.model_validate(event.payload.get("runtime_recovery_reconciliation"))
            key = f"runtime_recovery_reconciliation:{record.reconciliation_id}:{record.revision}"
            if event.payload.get("source_idempotency_key") != key or event.project_id != record.project_id:
                raise ValueError("runtime recovery reconciliation visibility provenance mismatch")
            if record.reconciliation_id in seen:
                continue
            seen.add(record.reconciliation_id)
            records.append(RuntimeRecoveryReconciliationVisibilityRecord(
                **record.model_dump(mode="python", exclude={"state", "action", "recovery_execution_state"}),
                reconciliation_state=record.state.value,
                action=record.action.value,
                recovery_execution_state=record.recovery_execution_state.value,
            ))
        return tuple(records)


class RuntimeRecoveryReconciliationVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeRecoveryReconciliationVisibilityAdapter()

    def publish(self, record: RuntimeRecoveryReconciliationRecord) -> RuntimeRecoveryReconciliationVisibilityRecord:
        self._mission_control.append_event_once(self._adapter.to_event(record))
        projected = next((item for item in self.list_records(record.project_id) if item.reconciliation_id == record.reconciliation_id), None)
        if projected is None:
            raise ValueError("runtime recovery reconciliation visibility projection failed")
        return projected

    def list_records(self, project_id: str, *, state: Optional[RuntimeRecoveryReconciliationState] = None) -> Tuple[RuntimeRecoveryReconciliationVisibilityRecord, ...]:
        records = self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
        if state is not None:
            records = tuple(item for item in records if item.reconciliation_state == state.value)
        return records
