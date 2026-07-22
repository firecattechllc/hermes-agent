"""Concise Mission Control visibility for governed model execution."""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .model_execution import ModelExecutionEvidence, ModelExecutionState


MODEL_EXECUTION_EVENT = "model_execution_recorded"


class ModelExecutionVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_id: str
    selected_model: Optional[str]
    active_model: Optional[str]
    execution_state: str
    approval_state: str
    authorized_cost_micros: int = Field(..., ge=0)
    actual_cost_micros: int = Field(..., ge=0)
    attempt_count: int = Field(..., ge=0)
    fallback_state: str
    terminal_outcome: str
    error_classification: Optional[str]
    output_reference: Optional[str]
    event_id: str


class ModelExecutionVisibilityAdapter:
    def to_event(self, evidence: ModelExecutionEvidence) -> mission_models.TelemetryEvent:
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_{evidence.evidence_id}",
            event_type=MODEL_EXECUTION_EVENT,
            project_id=evidence.project_id, task_id=evidence.task_id,
            timestamp=evidence.completed_at,
            severity="info" if evidence.state == ModelExecutionState.SUCCEEDED else "warning",
            correlation_id=evidence.execution_id,
            payload={
                "source": "model_execution",
                "source_idempotency_key": f"model_execution:{evidence.execution_id}",
                "execution": evidence.model_dump(mode="json"),
            },
        )

    def from_events(
        self, events: Iterable[mission_models.TelemetryEvent]
    ) -> Tuple[ModelExecutionVisibilityRecord, ...]:
        records = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != MODEL_EXECUTION_EVENT:
                continue
            evidence = ModelExecutionEvidence.model_validate(event.payload.get("execution"))
            if (
                event.event_id != f"telemetry_{evidence.evidence_id}"
                or event.project_id != evidence.project_id
                or event.task_id != evidence.task_id
                or event.correlation_id != evidence.execution_id
                or event.payload.get("source") != "model_execution"
                or event.payload.get("source_idempotency_key")
                != f"model_execution:{evidence.execution_id}"
            ):
                raise ValueError("model execution visibility association mismatch")
            active = evidence.attempted_models[-1] if evidence.attempted_models else evidence.selected_model
            fallback = (
                "exhausted" if evidence.state == ModelExecutionState.EXHAUSTED
                else "used" if evidence.fallback_progression
                else "available" if evidence.state in {ModelExecutionState.FAILED, ModelExecutionState.APPROVAL_REQUIRED}
                else "not_used"
            )
            records[evidence.execution_id] = ModelExecutionVisibilityRecord(
                execution_id=evidence.execution_id, selected_model=evidence.selected_model,
                active_model=active, execution_state=evidence.state.value,
                approval_state=evidence.approval_disposition,
                authorized_cost_micros=evidence.authorized_cost_micros,
                actual_cost_micros=evidence.actual_cost_micros,
                attempt_count=len(evidence.attempts), fallback_state=fallback,
                terminal_outcome=evidence.state.value,
                error_classification=None if evidence.error_classification is None else evidence.error_classification.value,
                output_reference=evidence.output_reference, event_id=event.event_id,
            )
        return tuple(records[key] for key in sorted(records))


class ModelExecutionVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = ModelExecutionVisibilityAdapter()

    def publish(self, evidence: ModelExecutionEvidence) -> ModelExecutionVisibilityRecord:
        self._mission_control.append_event_once(self._adapter.to_event(evidence))
        return next(
            item for item in self.list_records(evidence.project_id)
            if item.execution_id == evidence.execution_id
        )

    def list_records(self, project_id: str) -> Tuple[ModelExecutionVisibilityRecord, ...]:
        return self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
