"""Sanitized Mission Control visibility for Step 28 optimizations."""

from __future__ import annotations

import hashlib
from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService

from .intelligence_engine import IntelligenceEvidence, IntelligenceState


INTELLIGENCE_OPTIMIZATION_REQUESTED = "intelligence_optimization_requested"
INTELLIGENCE_OPTIMIZATION_RECORDED = "intelligence_optimization_recorded"
INTELLIGENCE_OPTIMIZATION_BLOCKED = "intelligence_optimization_blocked"
INTELLIGENCE_OPTIMIZATION_APPROVAL_REQUIRED = "intelligence_optimization_approval_required"
INTELLIGENCE_OPTIMIZATION_APPLIED = "intelligence_optimization_applied"
INTELLIGENCE_RECOVERY_RECOMMENDED = "intelligence_recovery_recommended"
INTELLIGENCE_BUDGET_PRESSURE_DETECTED = "intelligence_budget_pressure_detected"
INTELLIGENCE_EVENT_TYPES = (
    INTELLIGENCE_OPTIMIZATION_REQUESTED, INTELLIGENCE_OPTIMIZATION_RECORDED,
    INTELLIGENCE_OPTIMIZATION_BLOCKED, INTELLIGENCE_OPTIMIZATION_APPROVAL_REQUIRED,
    INTELLIGENCE_OPTIMIZATION_APPLIED, INTELLIGENCE_RECOVERY_RECOMMENDED,
    INTELLIGENCE_BUDGET_PRESSURE_DETECTED,
)


class IntelligenceVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    optimization_id: str
    project_id: str
    task_id: str
    lifecycle_state: IntelligenceState
    active_objective: str
    selected_strategy: str | None
    selected_model_recommendation: str | None
    context_action: str
    parallel_width: int = Field(..., ge=0)
    budget_authorized_micros: int = Field(..., ge=0)
    budget_consumed_micros: int = Field(..., ge=0)
    budget_remaining_micros: int = Field(..., ge=0)
    estimated_savings_micros: int = Field(..., ge=0)
    quality_impact: int
    reliability_impact: int
    recovery_state: str
    approval_state: str
    automatic_application_state: str
    confidence: int = Field(..., ge=0, le=1000)
    terminal_result: str
    reason_codes: Tuple[str, ...]
    event_id: str


class IntelligenceVisibilityAdapter:
    def to_events(self, evidence: IntelligenceEvidence) -> Tuple[mission_models.TelemetryEvent, ...]:
        types = [INTELLIGENCE_OPTIMIZATION_REQUESTED, INTELLIGENCE_OPTIMIZATION_RECORDED]
        if evidence.lifecycle_state is IntelligenceState.BLOCKED:
            types.append(INTELLIGENCE_OPTIMIZATION_BLOCKED)
        if evidence.lifecycle_state is IntelligenceState.APPROVAL_REQUIRED:
            types.append(INTELLIGENCE_OPTIMIZATION_APPROVAL_REQUIRED)
        if evidence.automatic_application_permitted:
            types.append(INTELLIGENCE_OPTIMIZATION_APPLIED)
        if evidence.recovery_plan.signals:
            types.append(INTELLIGENCE_RECOVERY_RECOMMENDED)
        if evidence.budget_plan.budget_pressure:
            types.append(INTELLIGENCE_BUDGET_PRESSURE_DETECTED)
        payload = {"evidence": evidence.model_dump(mode="json"), "source": "intelligence_engine", "source_idempotency_key": f"intelligence:{evidence.optimization_id}:{evidence.evidence_id}"}
        return tuple(mission_models.TelemetryEvent(event_id=f"telemetry_intelligence_{hashlib.sha256(f'{event_type}|{evidence.evidence_id}'.encode()).hexdigest()[:24]}", event_type=event_type, project_id=evidence.project_id, task_id=evidence.task_or_workflow_id, timestamp=evidence.completed_at, severity="warning" if event_type in {INTELLIGENCE_OPTIMIZATION_BLOCKED, INTELLIGENCE_BUDGET_PRESSURE_DETECTED} else "info", correlation_id=evidence.optimization_id, causation_id=evidence.request_fingerprint[:128], payload=payload) for event_type in types)

    def from_events(self, events: Iterable[mission_models.TelemetryEvent]) -> Tuple[IntelligenceVisibilityRecord, ...]:
        records = {}
        for event in sorted(events, key=lambda item: item.stable_sort_key()):
            if event.event_type not in INTELLIGENCE_EVENT_TYPES:
                continue
            evidence = IntelligenceEvidence.model_validate(event.payload.get("evidence"))
            expected_key = f"intelligence:{evidence.optimization_id}:{evidence.evidence_id}"
            if event.payload.get("source") != "intelligence_engine" or event.payload.get("source_idempotency_key") != expected_key or event.project_id != evidence.project_id or event.task_id != evidence.task_or_workflow_id or event.correlation_id != evidence.optimization_id or event.causation_id != evidence.request_fingerprint[:128] or event.timestamp != evidence.completed_at:
                raise ValueError("intelligence visibility association mismatch")
            selected = evidence.selected_plan
            route = evidence.route_recommendation
            records[evidence.optimization_id] = IntelligenceVisibilityRecord(optimization_id=evidence.optimization_id, project_id=evidence.project_id, task_id=evidence.task_or_workflow_id, lifecycle_state=evidence.lifecycle_state, active_objective=evidence.objective, selected_strategy=None if selected is None else selected.action.value, selected_model_recommendation=route.selected_model_id, context_action=evidence.context_plan.decisions[0].action.value if evidence.context_plan.decisions else "none", parallel_width=evidence.scheduling_plan.maximum_parallel_width, budget_authorized_micros=evidence.budget_plan.authorized_budget_micros, budget_consumed_micros=evidence.budget_plan.consumed_budget_micros, budget_remaining_micros=evidence.budget_plan.remaining_budget_micros, estimated_savings_micros=evidence.budget_plan.estimated_savings_micros, quality_impact=evidence.expected_quality_impact, reliability_impact=evidence.expected_reliability_impact, recovery_state=evidence.recovery_plan.actions[0].value if evidence.recovery_plan.actions else "none", approval_state="required" if evidence.approval_requirements else "not_required", automatic_application_state="simulated" if evidence.automatic_application_permitted else "not_permitted", confidence=evidence.confidence, terminal_result=evidence.application_result, reason_codes=evidence.reason_codes, event_id=event.event_id)
        return tuple(records[key] for key in sorted(records))


class IntelligenceVisibilityService:
    def __init__(self, mission_control: MissionControlService) -> None:
        self._mission_control = mission_control
        self._adapter = IntelligenceVisibilityAdapter()

    def publish(self, evidence: IntelligenceEvidence) -> IntelligenceVisibilityRecord:
        for event in self._adapter.to_events(evidence):
            self._mission_control.append_event_once(event)
        return next(item for item in self.list_records(evidence.project_id) if item.optimization_id == evidence.optimization_id)

    def list_records(self, project_id: str) -> Tuple[IntelligenceVisibilityRecord, ...]:
        return self._adapter.from_events(self._mission_control.get_events(project_id.strip()))
