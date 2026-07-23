"""Mission Control visibility for Step 31 learning decisions."""

from __future__ import annotations

from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .learning_hierarchy import (
    LearningDecision,
    LearningDecisionState,
    LearningRoute,
)


LEARNING_HIERARCHY_EVENT = "learning_hierarchy_recorded"


class LearningHierarchyVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    request_id: str
    project_id: str
    task_id: str
    selected_route: LearningRoute
    state: LearningDecisionState
    approval_state: str
    lesson_state: str
    execution_state: str
    fallback_count: int = Field(..., ge=0)
    reason_codes: Tuple[str, ...]
    created_at: int = Field(..., ge=0)
    event_id: str


class LearningHierarchyVisibilityAdapter:
    def to_event(
        self,
        decision: LearningDecision,
    ) -> mission_models.TelemetryEvent:
        severity = (
            "warning"
            if decision.state
            in {
                LearningDecisionState.APPROVAL_REQUIRED,
                LearningDecisionState.DEFERRED,
                LearningDecisionState.BLOCKED,
            }
            else "info"
        )
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_{decision.decision_id}",
            event_type=LEARNING_HIERARCHY_EVENT,
            project_id=decision.project_id,
            task_id=decision.task_id,
            timestamp=decision.created_at,
            severity=severity,
            correlation_id=decision.request_id,
            causation_id=decision.request_fingerprint[:128],
            payload={
                "source": "learning_hierarchy",
                "decision": decision.model_dump(mode="json"),
                "source_idempotency_key": (
                    f"learning_hierarchy:{decision.decision_id}"
                ),
            },
        )

    def from_events(
        self,
        events: Iterable[mission_models.TelemetryEvent],
    ) -> Tuple[LearningHierarchyVisibilityRecord, ...]:
        records = {}
        for event in sorted(
            events,
            key=lambda item: item.stable_sort_key(),
        ):
            if event.event_type != LEARNING_HIERARCHY_EVENT:
                continue

            decision = LearningDecision.model_validate(
                event.payload.get("decision")
            )
            if (
                event.event_id != f"telemetry_{decision.decision_id}"
                or event.project_id != decision.project_id
                or event.task_id != decision.task_id
                or event.timestamp != decision.created_at
                or event.correlation_id != decision.request_id
                or event.causation_id
                != decision.request_fingerprint[:128]
                or event.payload.get("source")
                != "learning_hierarchy"
                or event.payload.get("source_idempotency_key")
                != f"learning_hierarchy:{decision.decision_id}"
            ):
                raise ValueError(
                    "learning hierarchy visibility association mismatch"
                )

            records[decision.decision_id] = self.record_for(
                event,
                decision,
            )

        return tuple(records[key] for key in sorted(records))

    @staticmethod
    def record_for(
        event: mission_models.TelemetryEvent,
        decision: LearningDecision,
    ) -> LearningHierarchyVisibilityRecord:
        return LearningHierarchyVisibilityRecord(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            project_id=decision.project_id,
            task_id=decision.task_id,
            selected_route=decision.selected_route,
            state=decision.state,
            approval_state=(
                "required"
                if decision.requires_approval
                else "not_required"
            ),
            lesson_state=(
                "queued"
                if decision.lesson_request is not None
                else "not_queued"
            ),
            execution_state=(
                "permitted"
                if decision.execution_permitted
                else "decision_only"
            ),
            fallback_count=len(decision.fallback_chain),
            reason_codes=decision.reason_codes,
            created_at=decision.created_at,
            event_id=event.event_id,
        )


class LearningHierarchyVisibilityService:
    def __init__(
        self,
        mission_control: MissionControlService,
    ) -> None:
        self._mission_control = mission_control
        self._adapter = LearningHierarchyVisibilityAdapter()

    def publish(
        self,
        decision: LearningDecision,
    ) -> LearningHierarchyVisibilityRecord:
        event = self._mission_control.append_event_once(
            self._adapter.to_event(decision)
        )
        if event is not None:
            return self._adapter.from_events((event,))[0]

        return next(
            item
            for item in self.list_records(decision.project_id)
            if item.decision_id == decision.decision_id
        )

    def list_records(
        self,
        project_id: str,
    ) -> Tuple[LearningHierarchyVisibilityRecord, ...]:
        return self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
