"""Read-only Mission Control projection for workflow dispatch outcomes."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .workflow_dispatch import WorkflowDispatchOutcome, WorkflowDispatchStatus


WORKFLOW_DISPATCH_VISIBILITY_EVENT = "workflow_dispatch_recorded"


class WorkflowDispatchVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    workflow_id: str
    run_id: str
    dispatch_id: str
    intent_id: str
    status: str
    dispatch_fingerprint: str = Field(..., min_length=64, max_length=64)
    assignment_id: str
    plan_id: str
    role_id: str
    agent_id: str
    authorization_id: str
    session_id: Optional[str]
    updated_at: int = Field(..., ge=0)
    event_id: str


class WorkflowDispatchVisibilityAdapter:
    def to_event(self, outcome: WorkflowDispatchOutcome) -> mission_models.TelemetryEvent:
        event_id = self.event_id_for(outcome)
        return mission_models.TelemetryEvent(
            event_id=event_id,
            event_type=WORKFLOW_DISPATCH_VISIBILITY_EVENT,
            project_id=outcome.project_id, task_id=outcome.dispatch_id,
            agent_id=outcome.agent_id, timestamp=outcome.created_at,
            severity=("warning" if outcome.status == WorkflowDispatchStatus.REFUSED else "info"),
            correlation_id=outcome.run_id, causation_id=outcome.causation_id,
            payload={
                "outcome": outcome.model_dump(mode="json"),
                "source": "workflow_dispatch",
                "source_idempotency_key": f"workflow_dispatch:{outcome.dispatch_id}",
            },
        )

    def from_events(
        self, events: Iterable[mission_models.TelemetryEvent]
    ) -> Tuple[WorkflowDispatchVisibilityRecord, ...]:
        records: dict[str, WorkflowDispatchVisibilityRecord] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != WORKFLOW_DISPATCH_VISIBILITY_EVENT:
                continue
            outcome = WorkflowDispatchOutcome.model_validate(event.payload.get("outcome"))
            if (
                event.event_id != self.event_id_for(outcome)
                or
                event.payload.get("source") != "workflow_dispatch"
                or event.payload.get("source_idempotency_key")
                != f"workflow_dispatch:{outcome.dispatch_id}"
                or event.project_id != outcome.project_id
                or event.task_id != outcome.dispatch_id
                or event.agent_id != outcome.agent_id
                or event.timestamp != outcome.created_at
                or event.correlation_id != outcome.run_id
                or event.causation_id != outcome.causation_id
            ):
                raise ValueError("workflow dispatch visibility association mismatch")
            previous = records.get(outcome.dispatch_id)
            if previous is not None and previous.dispatch_fingerprint != outcome.fingerprint:
                raise ValueError("workflow dispatch visibility idempotency collision")
            records[outcome.dispatch_id] = WorkflowDispatchVisibilityRecord(
                project_id=outcome.project_id, workflow_id=outcome.workflow_id,
                run_id=outcome.run_id, dispatch_id=outcome.dispatch_id,
                intent_id=outcome.intent_id, status=outcome.status.value,
                dispatch_fingerprint=outcome.fingerprint,
                assignment_id=outcome.assignment_id, plan_id=outcome.plan_id,
                role_id=outcome.role_id, agent_id=outcome.agent_id,
                authorization_id=outcome.authorization_id,
                session_id=(outcome.session.session_id if outcome.session else None),
                updated_at=outcome.created_at, event_id=event.event_id,
            )
        return tuple(records[key] for key in sorted(records))

    @staticmethod
    def event_id_for(outcome: WorkflowDispatchOutcome) -> str:
        digest = hashlib.sha256(
            f"{outcome.dispatch_id}|{outcome.fingerprint}".encode()
        ).hexdigest()[:24]
        return f"telemetry_workflow_dispatch_{digest}"


class WorkflowDispatchVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = WorkflowDispatchVisibilityAdapter()

    def publish(self, outcome: WorkflowDispatchOutcome) -> WorkflowDispatchVisibilityRecord:
        event = self._mission_control.append_event_once(self._adapter.to_event(outcome))
        if event is not None:
            record = self._adapter.from_events((event,))[0]
        else:
            record = next(
                item for item in self.list_records(outcome.project_id)
                if item.dispatch_id == outcome.dispatch_id
            )
        if record.dispatch_fingerprint != outcome.fingerprint:
            raise ValueError("workflow dispatch visibility idempotency collision")
        return record

    def list_records(
        self, project_id: str, *, workflow_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[WorkflowDispatchVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if workflow_id is not None:
            records = tuple(item for item in records if item.workflow_id == workflow_id.strip())
        if status is not None:
            records = tuple(item for item in records if item.status == status.strip())
        return records
