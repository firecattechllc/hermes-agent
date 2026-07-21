"""Read-only Mission Control projection for governed runtime recovery."""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_recovery_store import (
    RuntimeRecoveryRecord,
    RuntimeRecoveryState,
)


RUNTIME_RECOVERY_VISIBILITY_EVENT = "runtime_recovery_recorded"


class RuntimeRecoveryVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recovery_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    execution_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=64)
    state: str = Field(..., min_length=1, max_length=64)
    revision: int = Field(..., ge=1)
    requested_by: str = Field(..., min_length=1, max_length=256)
    requested_at: int = Field(..., ge=0)
    authorized_by: Optional[str] = Field(default=None, min_length=1, max_length=256)
    authorized_at: Optional[int] = Field(default=None, ge=0)


class RuntimeRecoveryVisibilityAdapter:
    def to_event(
        self,
        record: RuntimeRecoveryRecord,
    ) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(
            f"{record.recovery_id}|{record.revision}|{record.checksum}".encode()
        ).hexdigest()[:24]
        severity = (
            "warning"
            if record.state == RuntimeRecoveryState.AWAITING_AUTHORIZATION
            else "info"
        )
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_runtime_recovery_{digest}",
            event_type=RUNTIME_RECOVERY_VISIBILITY_EVENT,
            project_id=record.project_id,
            actor_id=record.authorized_by or record.requested_by,
            timestamp=record.authorized_at or record.requested_at,
            severity=severity,
            correlation_id=record.correlation_id,
            payload={
                "source": "runtime_recovery",
                "source_idempotency_key": (
                    f"runtime_recovery:{record.recovery_id}:{record.revision}"
                ),
                "recovery": record.model_dump(mode="json"),
            },
        )

    def from_events(
        self,
        events,
    ) -> Tuple[RuntimeRecoveryVisibilityRecord, ...]:
        latest: dict[str, RuntimeRecoveryVisibilityRecord] = {}
        revisions: dict[str, int] = {}

        for event in events:
            if event.event_type != RUNTIME_RECOVERY_VISIBILITY_EVENT:
                continue
            payload = event.payload
            if payload.get("source") != "runtime_recovery":
                raise ValueError("runtime recovery visibility source mismatch")

            record = RuntimeRecoveryRecord.model_validate(payload.get("recovery"))
            expected_key = f"runtime_recovery:{record.recovery_id}:{record.revision}"
            if payload.get("source_idempotency_key") != expected_key:
                raise ValueError("runtime recovery visibility idempotency mismatch")
            if record.project_id != event.project_id:
                raise ValueError("runtime recovery visibility project mismatch")

            previous = revisions.get(record.recovery_id, 0)
            if record.revision <= previous:
                if record.revision == previous:
                    continue
                raise ValueError("runtime recovery visibility revision regression")

            latest[record.recovery_id] = RuntimeRecoveryVisibilityRecord(
                recovery_id=record.recovery_id,
                project_id=record.project_id,
                execution_id=record.execution_id,
                action=record.action.value,
                state=record.state.value,
                revision=record.revision,
                requested_by=record.requested_by,
                requested_at=record.requested_at,
                authorized_by=record.authorized_by,
                authorized_at=record.authorized_at,
            )
            revisions[record.recovery_id] = record.revision

        return tuple(latest.values())


class RuntimeRecoveryVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeRecoveryVisibilityAdapter()

    def publish(
        self,
        record: RuntimeRecoveryRecord,
    ) -> RuntimeRecoveryVisibilityRecord:
        self._mission_control.append_event_once(self._adapter.to_event(record))
        projected = next(
            (
                item
                for item in self.list_records(record.project_id)
                if item.recovery_id == record.recovery_id
            ),
            None,
        )
        if projected is None or projected.revision < record.revision:
            raise ValueError("runtime recovery visibility reconciliation failed")
        return projected

    def list_records(
        self,
        project_id: str,
        *,
        state: Optional[RuntimeRecoveryState] = None,
    ) -> Tuple[RuntimeRecoveryVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if state is not None:
            records = tuple(item for item in records if item.state == state.value)
        return records
