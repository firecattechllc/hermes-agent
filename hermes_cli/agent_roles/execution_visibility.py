"""Safe Mission Control projection of specialized-agent execution."""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hermes_cli.agent_roles.execution import ExecutionResult
from hermes_cli.agent_roles.execution_planning import RoleExecutionPlan
from hermes_cli.agent_roles.runtime_session import RuntimeSession
from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService


EXECUTION_EVENT_TYPE = "agent_execution_recorded"


class ExecutionVisibilityRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    assignment_id: str
    contract_id: str
    receipt_id: str
    session_id: str
    plan_id: str
    role_id: str
    agent_id: str
    session_state: str
    outcome: str
    evidence_count: int = Field(..., ge=0, le=100)
    evidence_summaries: Tuple[str, ...]
    blocking_reasons: Tuple[str, ...]
    retry_eligible: bool
    retry_requires_approval: bool
    retry_automatic: bool
    failure_category: str
    completed_at: int = Field(..., ge=0)
    event_id: str


class ExecutionVisibilityAdapter:
    def to_event(
        self,
        session: RuntimeSession,
        plan: RoleExecutionPlan,
        result: ExecutionResult,
    ) -> mission_models.TelemetryEvent:
        self._validate(session, plan, result)
        digest = hashlib.sha256(
            f"{result.result_id}|{result.fingerprint}".encode()
        ).hexdigest()[:24]
        return mission_models.TelemetryEvent(
            event_id=f"telemetry_execution_{digest}",
            event_type=EXECUTION_EVENT_TYPE,
            project_id=result.project_id,
            launch_id=result.contract_id,
            task_id=result.assignment_id,
            agent_id=result.agent_id,
            timestamp=result.completed_at,
            severity=("info" if result.outcome.value == "succeeded" else "error"),
            causation_id=result.session_id,
            payload={
                "session": session.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "source": "agent_roles",
            },
        )

    def from_events(
        self,
        events: Iterable[mission_models.TelemetryEvent],
    ) -> Tuple[ExecutionVisibilityRecord, ...]:
        records = []
        for event in events:
            if event.event_type != EXECUTION_EVENT_TYPE:
                continue
            session = RuntimeSession.model_validate(event.payload.get("session"))
            plan = RoleExecutionPlan.model_validate(event.payload.get("plan"))
            result = ExecutionResult.model_validate(event.payload.get("result"))
            self._validate(session, plan, result)
            records.append(
                ExecutionVisibilityRecord(
                    project_id=result.project_id,
                    assignment_id=result.assignment_id,
                    contract_id=result.contract_id,
                    receipt_id=result.receipt_id,
                    session_id=result.session_id,
                    plan_id=result.plan_id,
                    role_id=result.role_id,
                    agent_id=result.agent_id,
                    session_state=session.state.value,
                    outcome=result.outcome.value,
                    evidence_count=len(result.evidence),
                    evidence_summaries=tuple(
                        item.output_summary for item in result.evidence
                    ),
                    blocking_reasons=result.blocking_reasons,
                    retry_eligible=result.retry.eligible,
                    retry_requires_approval=result.retry.requires_approval,
                    retry_automatic=result.retry.automatic,
                    failure_category=result.failure_category.value,
                    completed_at=result.completed_at,
                    event_id=event.event_id,
                )
            )
        return tuple(
            sorted(records, key=lambda item: (item.completed_at, item.event_id))
        )

    @staticmethod
    def _validate(
        session: RuntimeSession, plan: RoleExecutionPlan, result: ExecutionResult
    ) -> None:
        common = (
            session.project_id == plan.project_id == result.project_id,
            session.assignment_id == plan.assignment_id == result.assignment_id,
            session.contract_id == plan.contract_id == result.contract_id,
            session.session_id == result.session_id,
            session.role_id == plan.role_id == result.role_id,
            session.agent_id == plan.agent_id == result.agent_id,
            plan.plan_id == result.plan_id,
        )
        if not all(common) or session.state.value != result.outcome.value:
            raise ValueError("execution visibility artifacts do not match")


class ExecutionVisibilityService:
    def __init__(
        self,
        mission_control: MissionControlService,
        adapter: Optional[ExecutionVisibilityAdapter] = None,
    ) -> None:
        self._mission_control = mission_control
        self._adapter = adapter or ExecutionVisibilityAdapter()

    def publish(
        self, session: RuntimeSession, plan: RoleExecutionPlan, result: ExecutionResult
    ) -> ExecutionVisibilityRecord:
        event = self._mission_control.append_event(
            self._adapter.to_event(session, plan, result)
        )
        return self._adapter.from_events((event,))[0]

    def list_records(
        self, project_id: str, *, assignment_id: Optional[str] = None
    ) -> Tuple[ExecutionVisibilityRecord, ...]:
        records = self._adapter.from_events(
            self._mission_control.get_events(project_id.strip())
        )
        if assignment_id is None:
            return records
        return tuple(item for item in records if item.assignment_id == assignment_id)
