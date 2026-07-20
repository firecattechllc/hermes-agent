"""Governed orchestration across specialized-agent execution results.

The orchestrator is deliberately decision-only: it records which governed
transition may happen next, but never creates assignments, launches workers,
retries execution, or promotes artifacts implicitly.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .execution import ExecutionOutcome, ExecutionResult
from .execution_planning import RoleExecutionPlan


WORKFLOW_SCHEMA_VERSION = 1
MAX_WORKFLOW_STAGES = 32
MAX_WORKFLOW_EVENTS = 128


def _audit_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("workflow audit text must not be blank")
    lowered = value.lower()
    if any(marker in lowered for marker in (
        "api_key=", "api-key=", "authorization: bearer ", "password=",
        "private key-----", "secret=", "token=",
    )):
        raise ValueError("workflow audit text must not contain secrets")
    return value


class WorkflowState(str, Enum):
    ACTIVE = "active"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkflowDecision(str, Enum):
    ADVANCE = "advance"
    RETRY = "retry"
    PROMOTE = "promote"
    CANCEL = "cancel"


class AuthorizationDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


class WorkflowStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(..., ge=1)
    role_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    result_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    outcome: Optional[ExecutionOutcome] = None

    @model_validator(mode="after")
    def _result_and_outcome_are_atomic(self) -> "WorkflowStage":
        if (self.result_id is None) != (self.outcome is None):
            raise ValueError("stage result_id and outcome must be recorded together")
        return self


class WorkflowAuthorization(BaseModel):
    """Explicit, immutable authority for exactly one proposed decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    expected_version: int = Field(..., ge=1)
    decision: WorkflowDecision
    disposition: AuthorizationDecision
    actor: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=1024)
    timestamp: int = Field(..., ge=0)
    to_role_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("actor", "reason", "to_role_id")
    @classmethod
    def _normalise_text(cls, value: Optional[str]) -> Optional[str]:
        return _audit_text(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_target(self) -> "WorkflowAuthorization":
        if self.decision == WorkflowDecision.ADVANCE and self.to_role_id is None:
            raise ValueError("advance authorization requires to_role_id")
        if self.decision != WorkflowDecision.ADVANCE and self.to_role_id is not None:
            raise ValueError("to_role_id is only valid for advance authorization")
        return self


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(..., ge=1)
    event_type: str = Field(..., min_length=1, max_length=64)
    timestamp: int = Field(..., ge=0)
    actor: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=1024)
    result_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    authorization_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("actor", "reason")
    @classmethod
    def _protect_audit_text(cls, value: str) -> str:
        return _audit_text(value)


class GovernedWorkflow(BaseModel):
    """Immutable, replayable orchestration checkpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = WORKFLOW_SCHEMA_VERSION
    workflow_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    state: WorkflowState
    version: int = Field(..., ge=1)
    stages: Tuple[WorkflowStage, ...] = Field(
        ..., min_length=1, max_length=MAX_WORKFLOW_STAGES
    )
    current_stage: int = Field(..., ge=0)
    proposed_decision: Optional[WorkflowDecision] = None
    proposed_role_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    authorizations: Tuple[WorkflowAuthorization, ...] = Field(default_factory=tuple)
    events: Tuple[WorkflowEvent, ...] = Field(
        ..., min_length=1, max_length=MAX_WORKFLOW_EVENTS
    )
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_workflow(self) -> "GovernedWorkflow":
        if self.schema_version != WORKFLOW_SCHEMA_VERSION:
            raise ValueError("unsupported workflow schema version")
        if self.current_stage >= len(self.stages):
            raise ValueError("current_stage is outside stages")
        if tuple(stage.sequence for stage in self.stages) != tuple(
            range(1, len(self.stages) + 1)
        ):
            raise ValueError("workflow stage sequence must be contiguous")
        if tuple(event.sequence for event in self.events) != tuple(
            range(1, len(self.events) + 1)
        ):
            raise ValueError("workflow event sequence must be contiguous")
        if self.events[-1].timestamp != self.updated_at:
            raise ValueError("latest workflow event must match updated_at")
        if any(event.timestamp > self.updated_at for event in self.events):
            raise ValueError("workflow events cannot occur after updated_at")
        if any(
            later.timestamp < earlier.timestamp
            for earlier, later in zip(self.events, self.events[1:])
        ):
            raise ValueError("workflow events must be chronological")
        awaiting = self.state == WorkflowState.AWAITING_AUTHORIZATION
        if awaiting != (self.proposed_decision is not None):
            raise ValueError("authorization state requires exactly one proposed decision")
        if self.proposed_decision == WorkflowDecision.ADVANCE:
            if self.proposed_role_id is None:
                raise ValueError("advance proposal requires a role")
        elif self.proposed_role_id is not None:
            raise ValueError("proposed role is only valid for advance")
        auth_ids = [item.authorization_id for item in self.authorizations]
        if len(auth_ids) != len(set(auth_ids)):
            raise ValueError("workflow authorization IDs must be unique")
        if any(
            item.project_id != self.project_id
            or item.workflow_id != self.workflow_id
            or item.expected_version >= self.version
            or item.timestamp > self.updated_at
            for item in self.authorizations
        ):
            raise ValueError("workflow authorization association is invalid")
        assignment_ids = [item.assignment_id for item in self.stages]
        plan_ids = [item.plan_id for item in self.stages]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("workflow assignment IDs must be unique")
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("workflow plan IDs must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class GovernedWorkflowService:
    """Pure orchestration transitions with explicit authorization gates."""

    def create(self, plan: RoleExecutionPlan, *, created_at: int) -> GovernedWorkflow:
        seed = "|".join((plan.project_id, plan.assignment_id, plan.plan_id))
        workflow_id = f"workflow_{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
        return GovernedWorkflow(
            workflow_id=workflow_id,
            project_id=plan.project_id,
            state=WorkflowState.ACTIVE,
            version=1,
            stages=(WorkflowStage(
                sequence=1,
                role_id=plan.role_id,
                assignment_id=plan.assignment_id,
                plan_id=plan.plan_id,
            ),),
            current_stage=0,
            events=(WorkflowEvent(
                sequence=1,
                event_type="workflow_created",
                timestamp=created_at,
                actor="orchestrator",
                reason="governed workflow created from execution plan",
            ),),
            created_at=created_at,
            updated_at=created_at,
        )

    def record_result(
        self,
        workflow: GovernedWorkflow,
        plan: RoleExecutionPlan,
        result: ExecutionResult,
        *,
        timestamp: int,
        next_role_id: Optional[str] = None,
    ) -> GovernedWorkflow:
        if workflow.state != WorkflowState.ACTIVE:
            raise ValueError("only active workflows can record a result")
        stage = workflow.stages[workflow.current_stage]
        if stage.result_id is not None:
            raise ValueError("current workflow stage already has a result")
        if not (
            workflow.project_id == plan.project_id == result.project_id
            and stage.assignment_id == plan.assignment_id == result.assignment_id
            and stage.plan_id == plan.plan_id == result.plan_id
            and stage.role_id == plan.role_id == result.role_id
        ):
            raise ValueError("execution result is outside the current workflow stage")
        if timestamp < workflow.updated_at or timestamp < result.completed_at:
            raise ValueError("workflow result timestamp cannot move backwards")

        proposed: Optional[WorkflowDecision] = None
        proposed_role: Optional[str] = None
        state: WorkflowState
        if result.outcome == ExecutionOutcome.SUCCEEDED:
            if next_role_id is not None:
                if next_role_id not in plan.allowed_next_roles:
                    raise PermissionError("next role exceeds execution-plan authority")
                proposed = WorkflowDecision.ADVANCE
                proposed_role = next_role_id
            else:
                if plan.allowed_next_roles:
                    raise ValueError(
                        "successful stage with governed successors must select one"
                    )
                proposed = WorkflowDecision.PROMOTE
            state = WorkflowState.AWAITING_AUTHORIZATION
        elif result.retry.eligible:
            if next_role_id is not None:
                raise ValueError("failed stage cannot propose a next role")
            proposed = WorkflowDecision.RETRY
            state = WorkflowState.AWAITING_AUTHORIZATION
        else:
            if next_role_id is not None:
                raise ValueError("non-success stage cannot propose a next role")
            state = (
                WorkflowState.BLOCKED
                if result.outcome in {ExecutionOutcome.BLOCKED, ExecutionOutcome.POLICY_DENIED}
                else WorkflowState.FAILED
            )

        stages = list(workflow.stages)
        stages[workflow.current_stage] = stage.model_copy(update={
            "result_id": result.result_id,
            "outcome": result.outcome,
        })
        return self._update(
            workflow,
            timestamp=timestamp,
            state=state,
            stages=tuple(stages),
            proposed_decision=proposed,
            proposed_role_id=proposed_role,
            event_type="stage_result_recorded",
            reason=f"stage completed with {result.outcome.value}",
            result_id=result.result_id,
        )

    def authorize(
        self,
        workflow: GovernedWorkflow,
        authorization: WorkflowAuthorization,
        *,
        next_plan: Optional[RoleExecutionPlan] = None,
    ) -> GovernedWorkflow:
        if workflow.state != WorkflowState.AWAITING_AUTHORIZATION:
            raise ValueError("workflow is not awaiting authorization")
        if authorization.project_id != workflow.project_id or authorization.workflow_id != workflow.workflow_id:
            raise ValueError("authorization is outside this workflow")
        if authorization.expected_version != workflow.version:
            raise ValueError("authorization expected_version is stale")
        if authorization.decision != workflow.proposed_decision:
            raise PermissionError("authorization does not match proposed decision")
        if authorization.to_role_id != workflow.proposed_role_id:
            raise PermissionError("authorization role does not match proposed role")
        if authorization.timestamp < workflow.updated_at:
            raise ValueError("authorization timestamp cannot move backwards")

        if authorization.disposition == AuthorizationDecision.DENIED:
            return self._update(
                workflow,
                timestamp=authorization.timestamp,
                state=WorkflowState.BLOCKED,
                proposed_decision=None,
                proposed_role_id=None,
                authorizations=workflow.authorizations + (authorization,),
                event_type="authorization_denied",
                actor=authorization.actor,
                reason=authorization.reason,
                authorization_id=authorization.authorization_id,
            )

        decision = authorization.decision
        if decision == WorkflowDecision.ADVANCE:
            if next_plan is None:
                raise ValueError("approved advance requires a governed execution plan")
            if (
                next_plan.project_id != workflow.project_id
                or next_plan.role_id != authorization.to_role_id
            ):
                raise ValueError("next execution plan does not match authorized advance")
            if len(workflow.stages) >= MAX_WORKFLOW_STAGES:
                raise ValueError("workflow stage limit reached")
            stages = workflow.stages + (WorkflowStage(
                sequence=len(workflow.stages) + 1,
                role_id=authorization.to_role_id or "",
                assignment_id=next_plan.assignment_id,
                plan_id=next_plan.plan_id,
            ),)
            state = WorkflowState.ACTIVE
            current_stage = workflow.current_stage + 1
        elif decision == WorkflowDecision.RETRY:
            if next_plan is None:
                raise ValueError("approved retry requires a governed execution plan")
            if len(workflow.stages) >= MAX_WORKFLOW_STAGES:
                raise ValueError("workflow stage limit reached")
            current = workflow.stages[workflow.current_stage]
            if next_plan.project_id != workflow.project_id or next_plan.role_id != current.role_id:
                raise ValueError("retry execution plan must preserve project and role authority")
            stages = workflow.stages + (WorkflowStage(
                sequence=len(workflow.stages) + 1,
                role_id=current.role_id,
                assignment_id=next_plan.assignment_id,
                plan_id=next_plan.plan_id,
            ),)
            state = WorkflowState.ACTIVE
            current_stage = workflow.current_stage + 1
        elif decision == WorkflowDecision.PROMOTE:
            if next_plan is not None:
                raise ValueError("promotion authorization cannot create a stage")
            stages = workflow.stages
            state = WorkflowState.COMPLETED
            current_stage = workflow.current_stage
        else:
            if next_plan is not None:
                raise ValueError("cancellation cannot create a stage")
            stages = workflow.stages
            state = WorkflowState.CANCELLED
            current_stage = workflow.current_stage

        return self._update(
            workflow,
            timestamp=authorization.timestamp,
            state=state,
            stages=stages,
            current_stage=current_stage,
            proposed_decision=None,
            proposed_role_id=None,
            authorizations=workflow.authorizations + (authorization,),
            event_type=f"{decision.value}_authorized",
            actor=authorization.actor,
            reason=authorization.reason,
            authorization_id=authorization.authorization_id,
        )

    @staticmethod
    def _update(
        workflow: GovernedWorkflow,
        *,
        timestamp: int,
        state: WorkflowState,
        event_type: str,
        reason: str,
        actor: str = "orchestrator",
        stages: Optional[Tuple[WorkflowStage, ...]] = None,
        current_stage: Optional[int] = None,
        proposed_decision: Optional[WorkflowDecision] = None,
        proposed_role_id: Optional[str] = None,
        authorizations: Optional[Tuple[WorkflowAuthorization, ...]] = None,
        result_id: Optional[str] = None,
        authorization_id: Optional[str] = None,
    ) -> GovernedWorkflow:
        if len(workflow.events) >= MAX_WORKFLOW_EVENTS:
            raise ValueError("workflow event limit reached")
        event = WorkflowEvent(
            sequence=len(workflow.events) + 1,
            event_type=event_type,
            timestamp=timestamp,
            actor=actor,
            reason=reason,
            result_id=result_id,
            authorization_id=authorization_id,
        )
        return GovernedWorkflow.model_validate({
            **workflow.model_dump(mode="python"),
            "state": state,
            "version": workflow.version + 1,
            "stages": stages if stages is not None else workflow.stages,
            "current_stage": workflow.current_stage if current_stage is None else current_stage,
            "proposed_decision": proposed_decision,
            "proposed_role_id": proposed_role_id,
            "authorizations": authorizations if authorizations is not None else workflow.authorizations,
            "events": workflow.events + (event,),
            "updated_at": timestamp,
        })
