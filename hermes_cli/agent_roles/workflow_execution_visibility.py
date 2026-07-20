"""Read-only Mission Control visibility for workflow execution evidence."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .workflow_execution import WorkflowRunSummary


WORKFLOW_EXECUTION_VISIBILITY_EVENT = "workflow_execution_evidence_recorded"


class WorkflowExecutionVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    workflow_id: str
    run_id: str
    status: str
    workflow_version: int = Field(..., ge=1)
    event_count: int = Field(..., ge=1)
    node_count: int = Field(..., ge=0)
    active_node_run_id: Optional[str]
    pending_decision: Optional[str]
    updated_at: int = Field(..., ge=0)
    event_id: str


class WorkflowExecutionVisibilityAdapter:
    def to_event(self, summary: WorkflowRunSummary) -> mission_models.TelemetryEvent:
        digest = hashlib.sha256(
            f"{summary.run_id}|{summary.fingerprint}".encode()
        ).hexdigest()[:24]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_workflow_run_{digest}",
            event_type=WORKFLOW_EXECUTION_VISIBILITY_EVENT,
            project_id=summary.project_id,
            task_id=summary.run_id,
            timestamp=summary.updated_at,
            severity=(
                "error"
                if summary.status.value in {"failed", "policy_denied"}
                else "warning"
                if summary.status.value == "blocked"
                else "info"
            ),
            correlation_id=summary.run_id,
            causation_id=summary.last_event_id,
            payload={
                "summary": summary.model_dump(mode="json"),
                "source": "workflow_execution_evidence",
                "source_idempotency_key": (
                    f"workflow_execution:{summary.run_id}:{summary.event_count}"
                ),
            },
        )

    def from_events(
        self,
        events: Iterable[mission_models.TelemetryEvent],
    ) -> Tuple[WorkflowExecutionVisibilityRecord, ...]:
        latest: dict[str, WorkflowExecutionVisibilityRecord] = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != WORKFLOW_EXECUTION_VISIBILITY_EVENT:
                continue
            summary = WorkflowRunSummary.model_validate(event.payload.get("summary"))
            if (
                event.project_id != summary.project_id
                or event.task_id != summary.run_id
                or event.correlation_id != summary.run_id
            ):
                raise ValueError("workflow execution visibility association mismatch")
            existing = latest.get(summary.run_id)
            if existing is not None and summary.event_count <= existing.event_count:
                continue
            active = next(
                (
                    node.node_run_id
                    for node in reversed(summary.nodes)
                    if node.status.value == "running"
                ),
                None,
            )
            latest[summary.run_id] = WorkflowExecutionVisibilityRecord(
                project_id=summary.project_id,
                workflow_id=summary.workflow_id,
                run_id=summary.run_id,
                status=summary.status.value,
                workflow_version=summary.workflow_version,
                event_count=summary.event_count,
                node_count=len(summary.nodes),
                active_node_run_id=active,
                pending_decision=(
                    summary.pending_decision.value
                    if summary.pending_decision is not None
                    else None
                ),
                updated_at=summary.updated_at,
                event_id=event.event_id,
            )
        return tuple(latest[key] for key in sorted(latest))


class WorkflowExecutionVisibilityService:
    def __init__(
        self,
        mission_control: MissionControlService,
        adapter: Optional[WorkflowExecutionVisibilityAdapter] = None,
    ) -> None:
        self._mission_control = mission_control
        self._adapter = adapter or WorkflowExecutionVisibilityAdapter()

    def publish(
        self,
        summary: WorkflowRunSummary,
    ) -> WorkflowExecutionVisibilityRecord:
        event = self._mission_control.append_event_once(
            self._adapter.to_event(summary)
        )
        if event is not None:
            return self._adapter.from_events((event,))[0]
        return next(
            item
            for item in self.list_records(summary.project_id)
            if item.run_id == summary.run_id
        )

    def list_records(
        self,
        project_id: str,
        *,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[WorkflowExecutionVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if workflow_id is not None:
            workflow_id = workflow_id.strip()
            records = tuple(
                item for item in records if item.workflow_id == workflow_id
            )
        if status is not None:
            status = status.strip()
            records = tuple(item for item in records if item.status == status)
        return records
