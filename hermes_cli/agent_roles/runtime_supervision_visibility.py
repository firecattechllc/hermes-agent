"""Read-only Mission Control projection for runtime supervision health events."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_supervision_store import SupervisionJournalRecord, SupervisionStatus


RUNTIME_SUPERVISION_VISIBILITY_EVENT = "runtime_supervision_recorded"


class RuntimeSupervisionVisibilityRecord(BaseModel):
    """Latest Mission Control projection of a runtime supervision event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    execution_id: str
    state: str
    status: str
    actor_id: str
    correlation_id: str
    observed_at: int = Field(..., ge=0)
    heartbeat_age_seconds: int = Field(..., ge=0)
    heartbeat_threshold_seconds: int = Field(..., ge=1)
    revision: int = Field(..., ge=1)
    event_id: str


class RuntimeSupervisionVisibilityAdapter:
    """Bridge between append-only supervision journals and Mission Control."""

    def to_event(
        self, record: SupervisionJournalRecord
    ) -> mission_models.TelemetryEvent:
        event_id = self._event_id_for(record)
        severity = {
            SupervisionStatus.HEALTHY: "info",
            SupervisionStatus.RECOVERED: "info",
            SupervisionStatus.STALE: "warning",
            SupervisionStatus.DEGRADED: "error",
        }.get(record.status, "info")
        return mission_models.TelemetryEvent(
            event_id=event_id,
            event_type=RUNTIME_SUPERVISION_VISIBILITY_EVENT,
            project_id=record.project_id,
            task_id=record.execution_id,
            agent_id=record.actor_id,
            timestamp=record.observed_at,
            severity=severity,
            correlation_id=record.correlation_id,
            causation_id=record.causation_id,
            payload={
                "record": record.model_dump(mode="json"),
                "source": "runtime_supervision",
                "source_idempotency_key": (
                    f"runtime_supervision:{record.execution_id}:{record.revision}"
                ),
            },
        )

    def from_events(
        self, events: Iterable[mission_models.TelemetryEvent]
    ) -> Tuple[RuntimeSupervisionVisibilityRecord, ...]:
        latest: dict[str, RuntimeSupervisionVisibilityRecord] = {}
        source_keys: dict[tuple[str, int], str] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != RUNTIME_SUPERVISION_VISIBILITY_EVENT:
                continue
            record_data = event.payload.get("record")
            status_val = record_data.get("status") if record_data else None
            execution_id = event.task_id
            revision = record_data.get("revision", 1) if record_data else 1
            idempotency_key = (
                f"runtime_supervision:{execution_id}:{revision}"
            )
            if (
                event.payload.get("source") != "runtime_supervision"
                or event.payload.get("source_idempotency_key") != idempotency_key
                or event.project_id != (record_data.get("project_id") if record_data else None)
                or event.agent_id != (record_data.get("actor_id") if record_data else None)
            ):
                raise ValueError("runtime supervision visibility association mismatch")
            key = (execution_id, revision)
            previous_fingerprint = source_keys.get(key)
            if previous_fingerprint is not None:
                raise ValueError("runtime supervision visibility idempotency collision")
            source_keys[key] = idempotency_key
            previous = latest.get(execution_id)
            if previous is not None and revision <= previous.revision:
                continue
            latest[execution_id] = RuntimeSupervisionVisibilityRecord(
                project_id=event.project_id,
                execution_id=execution_id,
                state=status_val or "unknown",
                status=status_val or "unknown",
                actor_id=event.agent_id,
                correlation_id=event.correlation_id,
                observed_at=event.timestamp,
                heartbeat_age_seconds=(
                    record_data.get("heartbeat_age_seconds", 0) if record_data else 0
                ),
                heartbeat_threshold_seconds=(
                    record_data.get("heartbeat_threshold_seconds", 600) if record_data else 600
                ),
                revision=revision,
                event_id=event.event_id,
            )
        return tuple(latest[key] for key in sorted(latest))

    @staticmethod
    def _event_id_for(record: SupervisionJournalRecord) -> str:
        digest = hashlib.sha256(
            f"{record.execution_id}|{record.revision}|{record.status.value}".encode()
        ).hexdigest()[:24]
        return f"telemetry_runtime_supervision_{digest}"


class RuntimeSupervisionVisibilityService:
    """Publish supervision health events to Mission Control for visibility."""

    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeSupervisionVisibilityAdapter()

    def publish(self, record: SupervisionJournalRecord) -> RuntimeSupervisionVisibilityRecord:
        """Publish one supervision event to Mission Control; idempotent."""
        self._mission_control.append_event_once(
            self._adapter.to_event(record)
        )
        # Reconcile the published state
        projected = next(
            item for item in self.list_records(record.project_id)
            if item.execution_id == record.execution_id
            and item.revision >= record.revision
        )
        if projected.revision < record.revision:
            raise ValueError("runtime supervision visibility reconciliation failed")
        return projected

    def list_records(
        self,
        project_id: str,
        *,
        status: Optional[SupervisionStatus] = None,
    ) -> Tuple[RuntimeSupervisionVisibilityRecord, ...]:
        """List latest supervision records from Mission Control."""
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if status is not None:
            records = tuple(
                item for item in records if item.status == status.value
            )
        return records
