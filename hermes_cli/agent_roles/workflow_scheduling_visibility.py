"""Read-only Mission Control projection for workflow scheduling state."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .workflow_scheduling import CoordinationStatus, WorkflowExecutionIntent


WORKFLOW_SCHEDULING_VISIBILITY_EVENT = "workflow_scheduling_recorded"


class WorkflowSchedulingVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    workflow_id: str
    run_id: str
    intent_id: str
    status: str
    version: int = Field(..., ge=1)
    intent_fingerprint: str = Field(..., min_length=64, max_length=64)
    node_run_id: str
    assignment_id: str
    plan_id: str
    role_id: str
    agent_id: str
    authorization_id: str
    claimed_by: Optional[str]
    lease_expires_at: Optional[int]
    updated_at: int = Field(..., ge=0)
    event_id: str


class WorkflowSchedulingVisibilityAdapter:
    def to_event(self, intent: WorkflowExecutionIntent) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(f"{intent.intent_id}|{intent.fingerprint}".encode()).hexdigest()[:24]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_workflow_scheduling_{digest}",
            event_type=WORKFLOW_SCHEDULING_VISIBILITY_EVENT,
            project_id=intent.project_id,
            task_id=intent.intent_id,
            agent_id=intent.agent_id,
            timestamp=intent.updated_at,
            severity=("warning" if intent.status in {CoordinationStatus.REFUSED, CoordinationStatus.EXPIRED} else "info"),
            correlation_id=intent.run_id,
            causation_id=intent.causation_id,
            payload={
                "intent": intent.model_dump(mode="json"),
                "source": "workflow_scheduling",
                "source_idempotency_key": f"workflow_scheduling:{intent.intent_id}:{intent.version}",
            },
        )

    def from_events(self, events: Iterable[mission_models.TelemetryEvent]) -> Tuple[WorkflowSchedulingVisibilityRecord, ...]:
        latest: dict[str, WorkflowSchedulingVisibilityRecord] = {}
        fingerprints: dict[tuple[str, int], str] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != WORKFLOW_SCHEDULING_VISIBILITY_EVENT:
                continue
            intent = WorkflowExecutionIntent.model_validate(event.payload.get("intent"))
            expected_idempotency_key = (
                f"workflow_scheduling:{intent.intent_id}:{intent.version}"
            )
            if (
                event.payload.get("source") != "workflow_scheduling"
                or event.payload.get("source_idempotency_key")
                != expected_idempotency_key
                or event.project_id != intent.project_id
                or event.task_id != intent.intent_id
                or event.correlation_id != intent.run_id
                or event.agent_id != intent.agent_id
                or event.timestamp != intent.updated_at
                or event.causation_id != intent.causation_id
            ):
                raise ValueError("workflow scheduling visibility association mismatch")
            revision_key = (intent.intent_id, intent.version)
            existing_fingerprint = fingerprints.get(revision_key)
            if (
                existing_fingerprint is not None
                and existing_fingerprint != intent.fingerprint
            ):
                raise ValueError(
                    "workflow scheduling visibility revision collision"
                )
            fingerprints[revision_key] = intent.fingerprint
            previous = latest.get(intent.intent_id)
            if previous is not None and intent.version <= previous.version:
                continue
            latest[intent.intent_id] = WorkflowSchedulingVisibilityRecord(
                project_id=intent.project_id, workflow_id=intent.workflow_id,
                run_id=intent.run_id, intent_id=intent.intent_id,
                status=intent.status.value, version=intent.version,
                intent_fingerprint=intent.fingerprint,
                node_run_id=intent.node_run_id, assignment_id=intent.assignment_id,
                plan_id=intent.plan_id, role_id=intent.role_id, agent_id=intent.agent_id,
                authorization_id=intent.authorization_id, claimed_by=intent.claimed_by,
                lease_expires_at=intent.lease_expires_at, updated_at=intent.updated_at,
                event_id=event.event_id,
            )
        return tuple(latest[key] for key in sorted(latest))


class WorkflowSchedulingVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = WorkflowSchedulingVisibilityAdapter()

    def publish(self, intent: WorkflowExecutionIntent) -> WorkflowSchedulingVisibilityRecord:
        event = self._mission_control.append_event_once(self._adapter.to_event(intent))
        if event is not None:
            record = self._adapter.from_events((event,))[0]
        else:
            record = next(
                item
                for item in self.list_records(intent.project_id)
                if item.intent_id == intent.intent_id
            )
        if (
            record.version != intent.version
            or record.intent_fingerprint != intent.fingerprint
        ):
            raise ValueError("workflow scheduling visibility idempotency collision")
        return record

    def list_records(self, project_id: str, *, workflow_id: Optional[str] = None, status: Optional[str] = None) -> Tuple[WorkflowSchedulingVisibilityRecord, ...]:
        records = self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
        if workflow_id is not None:
            records = tuple(item for item in records if item.workflow_id == workflow_id.strip())
        if status is not None:
            records = tuple(item for item in records if item.status == status.strip())
        return records
