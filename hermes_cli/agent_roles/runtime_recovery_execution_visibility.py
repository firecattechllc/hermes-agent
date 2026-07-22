"""Mission Control projection for governed runtime recovery execution."""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionRecord,
    RuntimeRecoveryExecutionState,
)


RUNTIME_RECOVERY_EXECUTION_VISIBILITY_EVENT = (
    "runtime_recovery_execution_recorded"
)


class RuntimeRecoveryExecutionVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    recovery_execution_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    execution_id: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=64)
    state: str = Field(..., min_length=1, max_length=64)
    executed_at: int = Field(..., ge=0)
    resulting_execution_state: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
    )


class RuntimeRecoveryExecutionVisibilityAdapter:
    def to_event(
        self,
        record: RuntimeRecoveryExecutionRecord,
    ) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(
            (
                f"{record.recovery_execution_id}|"
                f"{record.checksum}"
            ).encode()
        ).hexdigest()[:24]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_runtime_recovery_execution_{digest}",
            event_type=RUNTIME_RECOVERY_EXECUTION_VISIBILITY_EVENT,
            project_id=record.project_id,
            actor_id=record.actor_id,
            timestamp=record.executed_at,
            severity=(
                "warning"
                if record.state
                == RuntimeRecoveryExecutionState.HANDOFF_REQUIRED
                else "info"
            ),
            correlation_id=record.correlation_id,
            payload={
                "source": "runtime_recovery_execution",
                "source_idempotency_key": (
                    "runtime_recovery_execution:"
                    f"{record.recovery_execution_id}"
                ),
                "recovery_execution": record.model_dump(mode="json"),
            },
        )

    def from_events(
        self,
        events,
    ) -> Tuple[RuntimeRecoveryExecutionVisibilityRecord, ...]:
        records = []
        seen: set[str] = set()
        for event in events:
            if event.event_type != RUNTIME_RECOVERY_EXECUTION_VISIBILITY_EVENT:
                continue
            if event.payload.get("source") != "runtime_recovery_execution":
                raise ValueError(
                    "runtime recovery execution visibility source mismatch"
                )
            record = RuntimeRecoveryExecutionRecord.model_validate(
                event.payload.get("recovery_execution")
            )
            expected = (
                "runtime_recovery_execution:"
                f"{record.recovery_execution_id}"
            )
            if event.payload.get("source_idempotency_key") != expected:
                raise ValueError(
                    "runtime recovery execution visibility idempotency mismatch"
                )
            if record.project_id != event.project_id:
                raise ValueError(
                    "runtime recovery execution visibility project mismatch"
                )
            if record.recovery_execution_id in seen:
                continue
            seen.add(record.recovery_execution_id)
            records.append(
                RuntimeRecoveryExecutionVisibilityRecord(
                    recovery_execution_id=record.recovery_execution_id,
                    recovery_id=record.recovery_id,
                    project_id=record.project_id,
                    execution_id=record.execution_id,
                    action=record.action.value,
                    state=record.state.value,
                    executed_at=record.executed_at,
                    resulting_execution_state=record.resulting_execution_state,
                )
            )
        return tuple(records)


class RuntimeRecoveryExecutionVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeRecoveryExecutionVisibilityAdapter()

    def publish(
        self,
        record: RuntimeRecoveryExecutionRecord,
    ) -> RuntimeRecoveryExecutionVisibilityRecord:
        self._mission_control.append_event_once(self._adapter.to_event(record))
        projected = next(
            (
                item
                for item in self.list_records(record.project_id)
                if item.recovery_execution_id
                == record.recovery_execution_id
            ),
            None,
        )
        if projected is None:
            raise ValueError(
                "runtime recovery execution visibility reconciliation failed"
            )
        return projected

    def list_records(
        self,
        project_id: str,
        *,
        state: Optional[RuntimeRecoveryExecutionState] = None,
    ) -> Tuple[RuntimeRecoveryExecutionVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if state is not None:
            records = tuple(item for item in records if item.state == state.value)
        return records
