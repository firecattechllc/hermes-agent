"""Replay-safe Mission Control projection for governed workflows."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .workflow import GovernedWorkflow


WORKFLOW_EVENT_TYPE = "governed_workflow_recorded"


class WorkflowVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    project_id: str
    state: str
    version: int
    current_role_id: str
    stage_count: int
    proposed_decision: Optional[str]
    proposed_role_id: Optional[str]
    authorization_count: int
    updated_at: int
    event_id: str


class WorkflowVisibilityAdapter:
    def to_event(self, workflow: GovernedWorkflow) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(
            f"{workflow.workflow_id}|{workflow.fingerprint}".encode()
        ).hexdigest()[:24]
        current = workflow.stages[workflow.current_stage]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_workflow_{digest}",
            event_type=WORKFLOW_EVENT_TYPE,
            project_id=workflow.project_id,
            task_id=current.assignment_id,
            agent_id=current.role_id,
            timestamp=workflow.updated_at,
            severity=("warning" if workflow.state.value in {"blocked", "failed"} else "info"),
            correlation_id=workflow.workflow_id,
            payload={"workflow": workflow.model_dump(mode="json"), "source": "agent_roles"},
        )

    def from_events(self, events: Iterable[mission_models.TelemetryEvent]) -> Tuple[WorkflowVisibilityRecord, ...]:
        latest: dict[str, WorkflowVisibilityRecord] = {}
        versions: dict[str, int] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != WORKFLOW_EVENT_TYPE:
                continue
            workflow = GovernedWorkflow.model_validate(event.payload.get("workflow"))
            if workflow.project_id != event.project_id or workflow.workflow_id != event.correlation_id:
                raise ValueError("workflow visibility event association mismatch")
            previous = versions.get(workflow.workflow_id, 0)
            if workflow.version <= previous:
                continue
            current = workflow.stages[workflow.current_stage]
            latest[workflow.workflow_id] = WorkflowVisibilityRecord(
                workflow_id=workflow.workflow_id,
                project_id=workflow.project_id,
                state=workflow.state.value,
                version=workflow.version,
                current_role_id=current.role_id,
                stage_count=len(workflow.stages),
                proposed_decision=(workflow.proposed_decision.value if workflow.proposed_decision else None),
                proposed_role_id=workflow.proposed_role_id,
                authorization_count=len(workflow.authorizations),
                updated_at=workflow.updated_at,
                event_id=event.event_id,
            )
            versions[workflow.workflow_id] = workflow.version
        return tuple(latest[key] for key in sorted(latest))


class WorkflowVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = WorkflowVisibilityAdapter()

    def publish(self, workflow: GovernedWorkflow) -> WorkflowVisibilityRecord:
        event = self._mission_control.append_event_once(self._adapter.to_event(workflow))
        if event is not None:
            return self._adapter.from_events((event,))[0]
        records = self.list_records(workflow.project_id)
        return next(item for item in records if item.workflow_id == workflow.workflow_id)

    def list_records(self, project_id: str) -> Tuple[WorkflowVisibilityRecord, ...]:
        return self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
