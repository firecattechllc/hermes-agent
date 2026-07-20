"""Read-only Mission Control projection for runtime execution revisions."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_execution import RuntimeExecutionRecord


RUNTIME_EXECUTION_VISIBILITY_EVENT = "runtime_execution_recorded"


class RuntimeExecutionVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    workflow_id: str
    run_id: str
    node_run_id: str
    execution_id: str
    dispatch_id: str
    session_id: str
    state: str
    revision: int = Field(..., ge=1)
    execution_fingerprint: str = Field(..., min_length=64, max_length=64)
    actor_id: str
    updated_at: int = Field(..., ge=0)
    event_id: str


class RuntimeExecutionVisibilityAdapter:
    def to_event(self, record: RuntimeExecutionRecord) -> mission_models.TelemetryEvent:
        return mission_models.TelemetryEvent(
            event_id=self.event_id_for(record),
            event_type=RUNTIME_EXECUTION_VISIBILITY_EVENT,
            project_id=record.project_id, task_id=record.execution_id,
            agent_id=record.agent_id, timestamp=record.updated_at,
            severity=("warning" if record.state.value in {"failed", "cancelled"} else "info"),
            correlation_id=record.run_id, causation_id=record.causation_id,
            payload={
                "record": record.model_dump(mode="json"),
                "source": "runtime_execution",
                "source_idempotency_key": (
                    f"runtime_execution:{record.execution_id}:{record.revision}"
                ),
            },
        )

    def from_events(
        self, events: Iterable[mission_models.TelemetryEvent]
    ) -> Tuple[RuntimeExecutionVisibilityRecord, ...]:
        latest: dict[str, RuntimeExecutionVisibilityRecord] = {}
        latest_source: dict[str, RuntimeExecutionRecord] = {}
        fingerprints: dict[tuple[str, int], str] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != RUNTIME_EXECUTION_VISIBILITY_EVENT:
                continue
            record = RuntimeExecutionRecord.model_validate(event.payload.get("record"))
            key = (record.execution_id, record.revision)
            if (
                event.event_id != self.event_id_for(record)
                or event.payload.get("source") != "runtime_execution"
                or event.payload.get("source_idempotency_key")
                != f"runtime_execution:{record.execution_id}:{record.revision}"
                or event.project_id != record.project_id
                or event.task_id != record.execution_id
                or event.agent_id != record.agent_id
                or event.timestamp != record.updated_at
                or event.correlation_id != record.run_id
                or event.causation_id != record.causation_id
            ):
                raise ValueError("runtime execution visibility association mismatch")
            previous_fingerprint = fingerprints.get(key)
            if previous_fingerprint is not None and previous_fingerprint != record.fingerprint:
                raise ValueError("runtime execution visibility idempotency collision")
            fingerprints[key] = record.fingerprint
            previous = latest.get(record.execution_id)
            source = latest_source.get(record.execution_id)
            if source is None and record.revision != 1:
                raise ValueError("runtime execution visibility history must begin at revision 1")
            if source is not None:
                if record.revision < source.revision:
                    continue
                if record.revision > source.revision + 1:
                    raise ValueError("runtime execution visibility revision gap")
                if (
                    record.revision == source.revision + 1
                    and record.causation_id != source.fingerprint
                ):
                    raise ValueError("runtime execution visibility causation mismatch")
            latest[record.execution_id] = RuntimeExecutionVisibilityRecord(
                project_id=record.project_id, workflow_id=record.workflow_id,
                run_id=record.run_id, node_run_id=record.node_run_id,
                execution_id=record.execution_id, dispatch_id=record.dispatch_id,
                session_id=record.session_id, state=record.state.value,
                revision=record.revision, execution_fingerprint=record.fingerprint,
                actor_id=record.actor_id, updated_at=record.updated_at,
                event_id=event.event_id,
            )
            latest_source[record.execution_id] = record
        return tuple(latest[key] for key in sorted(latest))

    @staticmethod
    def event_id_for(record: RuntimeExecutionRecord) -> str:
        digest = hashlib.sha256(
            f"{record.execution_id}|{record.revision}|{record.fingerprint}".encode()
        ).hexdigest()[:24]
        return f"telemetry_runtime_execution_{digest}"


class RuntimeExecutionVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeExecutionVisibilityAdapter()

    def publish(
        self, record: RuntimeExecutionRecord
    ) -> RuntimeExecutionVisibilityRecord:
        self._mission_control.append_event_once(self._adapter.to_event(record))
        projected = next(
            item for item in self.list_records(record.project_id)
            if item.execution_id == record.execution_id
        )
        if (
            projected.revision < record.revision
            or projected.execution_fingerprint != record.fingerprint
        ):
            raise ValueError("runtime execution visibility idempotency collision")
        return projected

    def list_records(
        self, project_id: str, *, workflow_id: Optional[str] = None,
        state: Optional[str] = None,
    ) -> Tuple[RuntimeExecutionVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if workflow_id is not None:
            records = tuple(item for item in records if item.workflow_id == workflow_id.strip())
        if state is not None:
            records = tuple(item for item in records if item.state == state.strip())
        return records
