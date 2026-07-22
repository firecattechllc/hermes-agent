"""Mission Control projection for runtime recovery closure."""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .runtime_recovery_closure_store import (
    RuntimeRecoveryClosureRecord,
    RuntimeRecoveryClosureState,
)


RUNTIME_RECOVERY_CLOSURE_VISIBILITY_EVENT = (
    "runtime_recovery_closure_recorded"
)


class RuntimeRecoveryClosureVisibilityRecord(BaseModel):
    """Projected Mission Control view of one terminal recovery closure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    closure_id: str
    schema_version: int
    project_id: str

    reconciliation_id: str
    recovery_execution_id: str
    recovery_id: str
    execution_id: str

    action: str
    reconciliation_state: str
    closure_state: str

    actor_id: str
    correlation_id: str
    causation_id: str

    closed_at: int

    source_reconciliation_checksum: str
    reason: str
    evidence_refs: Tuple[str, ...]

    revision: int
    previous_checksum: Optional[str]
    checksum: str


class RuntimeRecoveryClosureVisibilityAdapter:
    """Converts durable closure records to and from Mission Control events."""

    def to_event(
        self,
        record: RuntimeRecoveryClosureRecord,
    ) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(
            f"{record.closure_id}|{record.checksum}".encode()
        ).hexdigest()[:24]

        return mission_models.TelemetryEvent(
            event_id=f"telemetry_runtime_recovery_closure_{digest}",
            event_type=RUNTIME_RECOVERY_CLOSURE_VISIBILITY_EVENT,
            project_id=record.project_id,
            agent_id=record.actor_id,
            actor_id=record.actor_id,
            timestamp=record.closed_at,
            severity="info",
            correlation_id=record.correlation_id,
            payload={
                "source": "runtime_recovery_closure",
                "source_idempotency_key": (
                    "runtime_recovery_closure:"
                    f"{record.closure_id}:{record.revision}"
                ),
                "runtime_recovery_closure": record.model_dump(mode="json"),
            },
        )

    def from_events(
        self,
        events,
    ) -> Tuple[RuntimeRecoveryClosureVisibilityRecord, ...]:
        records: list[RuntimeRecoveryClosureVisibilityRecord] = []
        seen: set[str] = set()

        for event in events:
            if event.event_type != RUNTIME_RECOVERY_CLOSURE_VISIBILITY_EVENT:
                continue

            if event.payload.get("source") != "runtime_recovery_closure":
                raise ValueError(
                    "runtime recovery closure visibility source mismatch"
                )

            record = RuntimeRecoveryClosureRecord.model_validate(
                event.payload.get("runtime_recovery_closure")
            )

            key = (
                "runtime_recovery_closure:"
                f"{record.closure_id}:{record.revision}"
            )

            if (
                event.payload.get("source_idempotency_key") != key
                or event.project_id != record.project_id
                or event.agent_id != record.actor_id
                or event.timestamp != record.closed_at
                or event.correlation_id != record.correlation_id
            ):
                raise ValueError(
                    "runtime recovery closure visibility provenance mismatch"
                )

            if record.closure_id in seen:
                continue

            seen.add(record.closure_id)

            records.append(
                RuntimeRecoveryClosureVisibilityRecord(
                    **record.model_dump(
                        mode="python",
                        exclude={
                            "state",
                            "action",
                            "reconciliation_state",
                        },
                    ),
                    closure_state=record.state.value,
                    action=record.action.value,
                    reconciliation_state=record.reconciliation_state.value,
                )
            )

        return tuple(records)


class RuntimeRecoveryClosureVisibilityService:
    """Publishes and projects terminal recovery closure records."""

    def __init__(
        self,
        mission_control: MissionControlService,
    ) -> None:
        self._mission_control = mission_control
        self._adapter = RuntimeRecoveryClosureVisibilityAdapter()

    def publish(
        self,
        record: RuntimeRecoveryClosureRecord,
    ) -> RuntimeRecoveryClosureVisibilityRecord:
        self._mission_control.append_event_once(
            self._adapter.to_event(record)
        )

        projected = next(
            (
                item
                for item in self.list_records(record.project_id)
                if item.closure_id == record.closure_id
            ),
            None,
        )

        if projected is None:
            raise ValueError(
                "runtime recovery closure visibility projection failed"
            )

        return projected

    def list_records(
        self,
        project_id: str,
        *,
        state: Optional[RuntimeRecoveryClosureState] = None,
    ) -> Tuple[RuntimeRecoveryClosureVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )

        if state is not None:
            records = tuple(
                item
                for item in records
                if item.closure_state == state.value
            )

        return records
