"""Concise Mission Control visibility for governed model-routing decisions."""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .model_routing import RoutingDecision, RoutingPolicyOutcome


MODEL_ROUTING_EVENT = "model_routing_recorded"


class ModelRoutingVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    request_id: str
    selected_model: Optional[str]
    approval_status: str
    budget_disposition: str
    fallback_available: bool
    fallback_count: int = Field(..., ge=0)
    no_route: bool
    estimated_cost_micros: Optional[int] = Field(default=None, ge=0)
    created_at: int = Field(..., ge=0)
    event_id: str


class ModelRoutingVisibilityAdapter:
    def to_event(self, project_id: str, decision: RoutingDecision) -> mission_models.TelemetryEvent:
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_{decision.decision_id}",
            event_type=MODEL_ROUTING_EVENT,
            project_id=project_id,
            task_id=decision.request_id,
            timestamp=decision.created_at,
            severity="warning" if decision.policy_outcome in {
                RoutingPolicyOutcome.APPROVAL_REQUIRED, RoutingPolicyOutcome.NO_ROUTE
            } else "info",
            correlation_id=decision.request_id,
            payload={
                "source": "model_routing",
                "decision": decision.model_dump(mode="json"),
                "source_idempotency_key": f"model_routing:{decision.decision_id}",
            },
        )

    def from_events(
        self, events: Iterable[mission_models.TelemetryEvent]
    ) -> Tuple[ModelRoutingVisibilityRecord, ...]:
        records = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type != MODEL_ROUTING_EVENT:
                continue
            decision = RoutingDecision.model_validate(event.payload.get("decision"))
            if (
                event.event_id != f"telemetry_{decision.decision_id}"
                or event.project_id.strip() != event.project_id
                or event.task_id != decision.request_id
                or event.timestamp != decision.created_at
                or event.correlation_id != decision.request_id
                or event.payload.get("source") != "model_routing"
                or event.payload.get("source_idempotency_key")
                != f"model_routing:{decision.decision_id}"
            ):
                raise ValueError("model routing visibility association mismatch")
            records[decision.decision_id] = self.record_for(event, decision)
        return tuple(records[key] for key in sorted(records))

    @staticmethod
    def record_for(
        event: mission_models.TelemetryEvent, decision: RoutingDecision
    ) -> ModelRoutingVisibilityRecord:
        selected = None
        if decision.selected_model_id is not None:
            selected = f"{decision.selected_provider_id}/{decision.selected_model_id}"
        approval = {
            RoutingPolicyOutcome.FREE: "not_required",
            RoutingPolicyOutcome.PREAPPROVED_PAID: "preapproved",
            RoutingPolicyOutcome.APPROVAL_REQUIRED: "required",
            RoutingPolicyOutcome.NO_ROUTE: "not_applicable",
        }[decision.policy_outcome]
        over_budget = any(
            "budget_exceeded" in item.rejection_reasons
            for item in decision.candidates
        )
        budget = (
            "over_budget" if decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE and over_budget
            else "no_route" if decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE
            else "free" if decision.estimated_cost_micros == 0
            else "within_budget"
        )
        return ModelRoutingVisibilityRecord(
            decision_id=decision.decision_id, request_id=decision.request_id,
            selected_model=selected, approval_status=approval,
            budget_disposition=budget,
            fallback_available=bool(decision.fallback_chain),
            fallback_count=len(decision.fallback_chain),
            no_route=decision.policy_outcome == RoutingPolicyOutcome.NO_ROUTE,
            estimated_cost_micros=decision.estimated_cost_micros,
            created_at=decision.created_at, event_id=event.event_id,
        )


class ModelRoutingVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = ModelRoutingVisibilityAdapter()

    def publish(self, project_id: str, decision: RoutingDecision) -> ModelRoutingVisibilityRecord:
        event = self._mission_control.append_event_once(
            self._adapter.to_event(project_id.strip(), decision)
        )
        if event is not None:
            return self._adapter.from_events((event,))[0]
        return next(
            item for item in self.list_records(project_id)
            if item.decision_id == decision.decision_id
        )

    def list_records(self, project_id: str) -> Tuple[ModelRoutingVisibilityRecord, ...]:
        return self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
