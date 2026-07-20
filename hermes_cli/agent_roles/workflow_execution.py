"""Immutable workflow-execution evidence and deterministic replay.

This module records what happened during a governed workflow run.  It grants
no authority to launch nodes, retry work, or promote artifacts.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .execution import ExecutionOutcome, ExecutionResult
from .execution_planning import RoleExecutionPlan
from .workflow import (
    GovernedWorkflow,
    WorkflowAuthorization,
    WorkflowDecision,
    WorkflowState,
)


WORKFLOW_EXECUTION_SCHEMA_VERSION = 1
MAX_RUN_EVENTS = 256
MAX_RUN_NODES = 64
MAX_EVENT_EVIDENCE_REFS = 100


def _safe_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    lowered = value.lower()
    secret_markers = (
        "api_key=",
        "api-key=",
        "authorization: bearer ",
        "password=",
        "private key-----",
        "secret=",
        "token=",
    )
    if any(marker in lowered for marker in secret_markers):
        raise ValueError("workflow execution evidence must not contain secrets")
    return value


def _canonical_digest(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.value if isinstance(value, Enum) else str(value),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class WorkflowRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"


class NodeRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"


class ExecutionEventType(str, Enum):
    RUN_CREATED = "run_created"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    TRANSITION_REQUESTED = "transition_requested"
    RETRY_REQUESTED = "retry_requested"
    AUTHORIZATION_GRANTED = "authorization_granted"
    AUTHORIZATION_DENIED = "authorization_denied"
    POLICY_REFUSED = "policy_refused"
    RUN_BLOCKED = "run_blocked"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    RUN_COMPLETED = "run_completed"


class EvidenceActorSource(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    ORCHESTRATOR = "orchestrator"
    POLICY = "policy"
    RUNTIME = "runtime"
    SYSTEM = "system"


_TERMINAL_RUN_STATUSES = {
    WorkflowRunStatus.SUCCEEDED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.BLOCKED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.POLICY_DENIED,
}


class WorkflowExecutionEvent(BaseModel):
    """One immutable, bounded, governance-relevant evidence record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = WORKFLOW_EXECUTION_SCHEMA_VERSION
    event_id: str = Field(..., min_length=1, max_length=128)
    event_type: ExecutionEventType
    sequence: int = Field(..., ge=1, le=MAX_RUN_EVENTS)
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    workflow_version: int = Field(..., ge=1)
    run_id: str = Field(..., min_length=1, max_length=128)
    node_run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    assignment_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    plan_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    role_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    agent_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    actor_id: str = Field(..., min_length=1, max_length=256)
    actor_source: EvidenceActorSource
    timestamp: int = Field(..., ge=0)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    authorization_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    decision: Optional[WorkflowDecision] = None
    to_role_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    outcome: Optional[ExecutionOutcome] = None
    reason: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=MAX_EVENT_EVIDENCE_REFS,
    )

    @field_validator(
        "event_id",
        "project_id",
        "workflow_id",
        "run_id",
        "node_run_id",
        "assignment_id",
        "plan_id",
        "role_id",
        "agent_id",
        "actor_id",
        "correlation_id",
        "causation_id",
        "authorization_id",
        "to_role_id",
        "reason",
    )
    @classmethod
    def _normalise_text(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        return _safe_text(value, info.field_name)

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_evidence_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        output = []
        seen = set()
        for value in values:
            normalised = _safe_text(value, "evidence_ref")
            if normalised not in seen:
                seen.add(normalised)
                output.append(normalised)
        return tuple(output)

    @model_validator(mode="after")
    def _validate_structure(self) -> "WorkflowExecutionEvent":
        if self.schema_version != WORKFLOW_EXECUTION_SCHEMA_VERSION:
            raise ValueError("unsupported workflow execution schema version")
        if self.correlation_id != self.run_id:
            raise ValueError("workflow execution correlation_id must equal run_id")
        if self.sequence == 1:
            if self.event_type != ExecutionEventType.RUN_CREATED:
                raise ValueError("workflow run evidence must begin with run_created")
            if self.causation_id is not None:
                raise ValueError("run_created cannot have causation_id")
        elif self.causation_id is None:
            raise ValueError("non-creation evidence requires causation_id")

        node_fields = (
            self.node_run_id,
            self.assignment_id,
            self.plan_id,
            self.role_id,
            self.agent_id,
        )
        node_event = self.event_type in {
            ExecutionEventType.NODE_STARTED,
            ExecutionEventType.NODE_COMPLETED,
        }
        if node_event and any(value is None for value in node_fields):
            raise ValueError("node evidence requires complete node associations")
        if not node_event and any(value is not None for value in node_fields):
            raise ValueError("node associations are only valid for node evidence")
        if self.event_type == ExecutionEventType.NODE_COMPLETED:
            if self.outcome is None:
                raise ValueError("node_completed requires outcome")
        elif self.outcome is not None:
            raise ValueError("outcome is only valid for node_completed")

        decision_events = {
            ExecutionEventType.TRANSITION_REQUESTED,
            ExecutionEventType.RETRY_REQUESTED,
            ExecutionEventType.AUTHORIZATION_GRANTED,
            ExecutionEventType.AUTHORIZATION_DENIED,
        }
        if self.event_type in decision_events and self.decision is None:
            raise ValueError("governance evidence requires decision")
        if self.event_type not in decision_events and self.decision is not None:
            raise ValueError("decision is only valid for governance evidence")
        if self.event_type == ExecutionEventType.RETRY_REQUESTED:
            if self.decision != WorkflowDecision.RETRY:
                raise ValueError("retry_requested requires retry decision")
        if self.event_type == ExecutionEventType.TRANSITION_REQUESTED:
            if self.decision == WorkflowDecision.RETRY:
                raise ValueError("retry decisions require retry_requested evidence")
        if self.decision == WorkflowDecision.ADVANCE:
            if self.to_role_id is None:
                raise ValueError("advance evidence requires to_role_id")
        elif self.to_role_id is not None:
            raise ValueError("to_role_id is only valid for advance evidence")

        authorization_event = self.event_type in {
            ExecutionEventType.AUTHORIZATION_GRANTED,
            ExecutionEventType.AUTHORIZATION_DENIED,
        }
        if authorization_event != (self.authorization_id is not None):
            raise ValueError("authorization evidence requires authorization_id")

        reason_required = {
            ExecutionEventType.AUTHORIZATION_DENIED,
            ExecutionEventType.POLICY_REFUSED,
            ExecutionEventType.RUN_BLOCKED,
            ExecutionEventType.RUN_FAILED,
            ExecutionEventType.RUN_CANCELLED,
        }
        if self.event_type in reason_required and self.reason is None:
            raise ValueError(f"{self.event_type.value} requires reason")
        return self

    @property
    def fingerprint(self) -> str:
        return _canonical_digest(self.model_dump(mode="json", exclude={"event_id"}))


class NodeRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_run_id: str = Field(..., min_length=1, max_length=128)
    sequence: int = Field(..., ge=1, le=MAX_RUN_NODES)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    status: NodeRunStatus
    started_at: int = Field(..., ge=0)
    completed_at: Optional[int] = Field(default=None, ge=0)
    result_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_node(self) -> "NodeRunSummary":
        running = self.status == NodeRunStatus.RUNNING
        if running and (self.completed_at is not None or self.result_id is not None):
            raise ValueError("running node cannot have terminal evidence")
        if not running and (self.completed_at is None or self.result_id is None):
            raise ValueError("terminal node requires completion and result evidence")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("node completion cannot precede start")
        return self


class WorkflowRunSummary(BaseModel):
    """Deterministic read model reconstructed only from evidence events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = WORKFLOW_EXECUTION_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    run_id: str = Field(..., min_length=1, max_length=128)
    status: WorkflowRunStatus
    workflow_version: int = Field(..., ge=1)
    event_count: int = Field(..., ge=1, le=MAX_RUN_EVENTS)
    nodes: Tuple[NodeRunSummary, ...] = Field(
        default_factory=tuple,
        max_length=MAX_RUN_NODES,
    )
    pending_decision: Optional[WorkflowDecision] = None
    pending_to_role_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    last_authorized_decision: Optional[WorkflowDecision] = None
    last_event_id: str = Field(..., min_length=1, max_length=128)
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_summary(self) -> "WorkflowRunSummary":
        awaiting = self.status == WorkflowRunStatus.AWAITING_AUTHORIZATION
        if awaiting != (self.pending_decision is not None):
            raise ValueError("authorization state requires pending decision")
        if self.pending_decision == WorkflowDecision.ADVANCE:
            if self.pending_to_role_id is None:
                raise ValueError("pending advance requires target role")
        elif self.pending_to_role_id is not None:
            raise ValueError("pending role is only valid for advance")
        if self.updated_at < self.created_at:
            raise ValueError("workflow run update cannot precede creation")
        return self

    @property
    def fingerprint(self) -> str:
        return _canonical_digest(self.model_dump(mode="json"))


class WorkflowExecutionProjector:
    """Fail-closed state-machine replay for one workflow run."""

    _NODE_STATUS = {
        ExecutionOutcome.SUCCEEDED: NodeRunStatus.SUCCEEDED,
        ExecutionOutcome.FAILED: NodeRunStatus.FAILED,
        ExecutionOutcome.BLOCKED: NodeRunStatus.BLOCKED,
        ExecutionOutcome.CANCELLED: NodeRunStatus.CANCELLED,
        ExecutionOutcome.POLICY_DENIED: NodeRunStatus.POLICY_DENIED,
    }

    def replay(
        self,
        events: Tuple[WorkflowExecutionEvent, ...],
    ) -> WorkflowRunSummary:
        if not events:
            raise ValueError("workflow execution replay requires events")
        if len(events) > MAX_RUN_EVENTS:
            raise ValueError("workflow execution event limit exceeded")
        ordered = tuple(sorted(events, key=lambda item: item.sequence))
        if tuple(item.sequence for item in ordered) != tuple(range(1, len(ordered) + 1)):
            raise ValueError("workflow execution event sequence must be contiguous")
        first = ordered[0]
        associations = (first.project_id, first.workflow_id, first.run_id)
        if any(
            (item.project_id, item.workflow_id, item.run_id) != associations
            for item in ordered
        ):
            raise ValueError("workflow execution events cross run or project boundary")
        event_ids = [item.event_id for item in ordered]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("workflow execution event IDs must be unique")
        if any(
            later.timestamp < earlier.timestamp
            for earlier, later in zip(ordered, ordered[1:])
        ):
            raise ValueError("workflow execution events must be chronological")

        status = WorkflowRunStatus.CREATED
        nodes: list[NodeRunSummary] = []
        pending_decision = None
        pending_to_role = None
        last_authorized = None
        previous_event_id = None
        workflow_version = first.workflow_version

        for event in ordered:
            if event.sequence > 1 and event.causation_id != previous_event_id:
                raise ValueError("workflow execution causation chain is invalid")
            if event.workflow_version < workflow_version:
                raise ValueError("workflow version cannot move backwards")
            workflow_version = event.workflow_version
            if status in _TERMINAL_RUN_STATUSES:
                raise ValueError("terminal workflow run cannot accept more evidence")

            if event.event_type == ExecutionEventType.RUN_CREATED:
                if event.sequence != 1:
                    raise ValueError("run_created may only be the first event")
            elif event.event_type == ExecutionEventType.NODE_STARTED:
                if status not in {WorkflowRunStatus.CREATED, WorkflowRunStatus.RUNNING}:
                    raise ValueError("node cannot start while authorization is pending")
                if nodes and nodes[-1].status == NodeRunStatus.RUNNING:
                    raise ValueError("workflow run already has an active node")
                if len(nodes) >= MAX_RUN_NODES:
                    raise ValueError("workflow execution node limit exceeded")
                if any(item.node_run_id == event.node_run_id for item in nodes):
                    raise ValueError("node_run_id already exists")
                nodes.append(NodeRunSummary(
                    node_run_id=event.node_run_id or "",
                    sequence=len(nodes) + 1,
                    assignment_id=event.assignment_id or "",
                    plan_id=event.plan_id or "",
                    role_id=event.role_id or "",
                    agent_id=event.agent_id or "",
                    status=NodeRunStatus.RUNNING,
                    started_at=event.timestamp,
                ))
                status = WorkflowRunStatus.RUNNING
                last_authorized = None
            elif event.event_type == ExecutionEventType.NODE_COMPLETED:
                if status != WorkflowRunStatus.RUNNING or not nodes:
                    raise ValueError("node completion requires a running workflow node")
                current = nodes[-1]
                expected = (
                    current.node_run_id,
                    current.assignment_id,
                    current.plan_id,
                    current.role_id,
                    current.agent_id,
                )
                actual = (
                    event.node_run_id,
                    event.assignment_id,
                    event.plan_id,
                    event.role_id,
                    event.agent_id,
                )
                if actual != expected or current.status != NodeRunStatus.RUNNING:
                    raise ValueError("node completion associations do not match active node")
                result_refs = tuple(event.evidence_refs)
                if not result_refs:
                    raise ValueError("node completion requires result evidence reference")
                nodes[-1] = current.model_copy(update={
                    "status": self._NODE_STATUS[event.outcome],
                    "completed_at": event.timestamp,
                    "result_id": result_refs[0],
                    "evidence_refs": result_refs,
                })
            elif event.event_type in {
                ExecutionEventType.TRANSITION_REQUESTED,
                ExecutionEventType.RETRY_REQUESTED,
            }:
                if status != WorkflowRunStatus.RUNNING or not nodes:
                    raise ValueError("governance request requires completed node evidence")
                if nodes[-1].status == NodeRunStatus.RUNNING:
                    raise ValueError("governance request cannot precede node completion")
                pending_decision = event.decision
                pending_to_role = event.to_role_id
                status = WorkflowRunStatus.AWAITING_AUTHORIZATION
            elif event.event_type in {
                ExecutionEventType.AUTHORIZATION_GRANTED,
                ExecutionEventType.AUTHORIZATION_DENIED,
            }:
                if status != WorkflowRunStatus.AWAITING_AUTHORIZATION:
                    raise ValueError("authorization evidence has no pending request")
                if event.decision != pending_decision or event.to_role_id != pending_to_role:
                    raise ValueError("authorization does not match pending decision")
                if event.event_type == ExecutionEventType.AUTHORIZATION_DENIED:
                    status = WorkflowRunStatus.BLOCKED
                else:
                    status = WorkflowRunStatus.RUNNING
                    last_authorized = event.decision
                pending_decision = None
                pending_to_role = None
            elif event.event_type == ExecutionEventType.POLICY_REFUSED:
                self._require_no_active_node(nodes)
                status = WorkflowRunStatus.POLICY_DENIED
            elif event.event_type == ExecutionEventType.RUN_BLOCKED:
                self._require_no_active_node(nodes)
                status = WorkflowRunStatus.BLOCKED
            elif event.event_type == ExecutionEventType.RUN_FAILED:
                self._require_no_active_node(nodes)
                status = WorkflowRunStatus.FAILED
            elif event.event_type == ExecutionEventType.RUN_CANCELLED:
                self._require_no_active_node(nodes)
                status = WorkflowRunStatus.CANCELLED
            elif event.event_type == ExecutionEventType.RUN_COMPLETED:
                if last_authorized != WorkflowDecision.PROMOTE:
                    raise ValueError("run completion requires authorized promotion")
                if not nodes or nodes[-1].status != NodeRunStatus.SUCCEEDED:
                    raise ValueError("run completion requires successful node evidence")
                status = WorkflowRunStatus.SUCCEEDED
            previous_event_id = event.event_id

        return WorkflowRunSummary(
            project_id=first.project_id,
            workflow_id=first.workflow_id,
            run_id=first.run_id,
            status=status,
            workflow_version=workflow_version,
            event_count=len(ordered),
            nodes=tuple(nodes),
            pending_decision=pending_decision,
            pending_to_role_id=pending_to_role,
            last_authorized_decision=last_authorized,
            last_event_id=ordered[-1].event_id,
            created_at=first.timestamp,
            updated_at=ordered[-1].timestamp,
        )

    @staticmethod
    def _require_no_active_node(nodes: list[NodeRunSummary]) -> None:
        if nodes and nodes[-1].status == NodeRunStatus.RUNNING:
            raise ValueError("terminal workflow evidence cannot leave active node")


class WorkflowExecutionEvidenceService:
    """Create deterministic evidence events from Step 5–6 artifacts."""

    def __init__(self, projector: Optional[WorkflowExecutionProjector] = None) -> None:
        self.projector = projector or WorkflowExecutionProjector()

    def create_run(
        self,
        workflow: GovernedWorkflow,
        *,
        actor_id: str,
        timestamp: int,
    ) -> Tuple[WorkflowExecutionEvent, WorkflowRunSummary]:
        if timestamp < workflow.created_at:
            raise ValueError("workflow run cannot precede workflow creation")
        run_seed = "|".join((workflow.project_id, workflow.workflow_id, str(workflow.created_at)))
        run_id = f"workflow_run_{hashlib.sha256(run_seed.encode()).hexdigest()[:24]}"
        event = self._event(
            event_type=ExecutionEventType.RUN_CREATED,
            sequence=1,
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            run_id=run_id,
            actor_id=actor_id,
            actor_source=EvidenceActorSource.ORCHESTRATOR,
            timestamp=timestamp,
        )
        return event, self.projector.replay((event,))

    def start_node(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        plan: RoleExecutionPlan,
        *,
        actor_id: str,
        timestamp: int,
    ) -> WorkflowExecutionEvent:
        self._validate_workflow(summary, workflow)
        if workflow.state != WorkflowState.ACTIVE:
            raise ValueError("node start requires active governed workflow")
        stage = workflow.stages[workflow.current_stage]
        if (
            plan.project_id != summary.project_id
            or plan.assignment_id != stage.assignment_id
            or plan.plan_id != stage.plan_id
            or plan.role_id != stage.role_id
        ):
            raise ValueError("node execution plan does not match current workflow stage")
        node_seed = "|".join((summary.run_id, plan.assignment_id, plan.plan_id))
        node_run_id = f"node_run_{hashlib.sha256(node_seed.encode()).hexdigest()[:24]}"
        return self._next_event(
            summary,
            workflow,
            event_type=ExecutionEventType.NODE_STARTED,
            actor_id=actor_id,
            actor_source=EvidenceActorSource.RUNTIME,
            timestamp=timestamp,
            node_run_id=node_run_id,
            assignment_id=plan.assignment_id,
            plan_id=plan.plan_id,
            role_id=plan.role_id,
            agent_id=plan.agent_id,
        )

    def complete_node(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        result: ExecutionResult,
        *,
        actor_id: str,
        timestamp: int,
    ) -> WorkflowExecutionEvent:
        self._validate_workflow(summary, workflow)
        if timestamp != result.completed_at:
            raise ValueError("node completion timestamp must match execution result")
        if not summary.nodes or summary.nodes[-1].status != NodeRunStatus.RUNNING:
            raise ValueError("node completion requires active node summary")
        node = summary.nodes[-1]
        if (
            result.project_id != summary.project_id
            or result.assignment_id != node.assignment_id
            or result.plan_id != node.plan_id
            or result.role_id != node.role_id
            or result.agent_id != node.agent_id
        ):
            raise ValueError("execution result does not match active node")
        refs = (result.result_id,) + tuple(item.evidence_id for item in result.evidence)
        return self._next_event(
            summary,
            workflow,
            event_type=ExecutionEventType.NODE_COMPLETED,
            actor_id=actor_id,
            actor_source=EvidenceActorSource.RUNTIME,
            timestamp=timestamp,
            node_run_id=node.node_run_id,
            assignment_id=node.assignment_id,
            plan_id=node.plan_id,
            role_id=node.role_id,
            agent_id=node.agent_id,
            outcome=result.outcome,
            reason=result.summary,
            evidence_refs=refs,
        )

    def request_decision(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        *,
        actor_id: str,
        timestamp: int,
    ) -> WorkflowExecutionEvent:
        self._validate_workflow(summary, workflow)
        if workflow.proposed_decision is None:
            raise ValueError("workflow has no governed decision to record")
        event_type = (
            ExecutionEventType.RETRY_REQUESTED
            if workflow.proposed_decision == WorkflowDecision.RETRY
            else ExecutionEventType.TRANSITION_REQUESTED
        )
        return self._next_event(
            summary,
            workflow,
            event_type=event_type,
            actor_id=actor_id,
            actor_source=EvidenceActorSource.ORCHESTRATOR,
            timestamp=timestamp,
            decision=workflow.proposed_decision,
            to_role_id=workflow.proposed_role_id,
        )

    def record_authorization(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        authorization: WorkflowAuthorization,
    ) -> WorkflowExecutionEvent:
        self._validate_workflow(summary, workflow)
        if authorization not in workflow.authorizations:
            raise ValueError("authorization is not recorded by governed workflow")
        from .workflow import AuthorizationDecision

        granted = authorization.disposition == AuthorizationDecision.APPROVED
        return self._next_event(
            summary,
            workflow,
            event_type=(
                ExecutionEventType.AUTHORIZATION_GRANTED
                if granted
                else ExecutionEventType.AUTHORIZATION_DENIED
            ),
            actor_id=authorization.actor,
            actor_source=EvidenceActorSource.HUMAN,
            timestamp=authorization.timestamp,
            authorization_id=authorization.authorization_id,
            decision=authorization.decision,
            to_role_id=authorization.to_role_id,
            reason=authorization.reason,
        )

    def terminal_event(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        *,
        event_type: ExecutionEventType,
        actor_id: str,
        actor_source: EvidenceActorSource,
        timestamp: int,
        reason: Optional[str] = None,
    ) -> WorkflowExecutionEvent:
        allowed = {
            ExecutionEventType.POLICY_REFUSED,
            ExecutionEventType.RUN_BLOCKED,
            ExecutionEventType.RUN_FAILED,
            ExecutionEventType.RUN_CANCELLED,
            ExecutionEventType.RUN_COMPLETED,
        }
        if event_type not in allowed:
            raise ValueError("terminal_event requires terminal event type")
        expected_states = {
            ExecutionEventType.POLICY_REFUSED: {WorkflowState.BLOCKED},
            ExecutionEventType.RUN_BLOCKED: {WorkflowState.BLOCKED},
            ExecutionEventType.RUN_FAILED: {WorkflowState.FAILED},
            ExecutionEventType.RUN_CANCELLED: {WorkflowState.CANCELLED},
            ExecutionEventType.RUN_COMPLETED: {WorkflowState.COMPLETED},
        }
        if workflow.state not in expected_states[event_type]:
            raise ValueError(
                "terminal evidence does not match governed workflow state"
            )
        return self._next_event(
            summary,
            workflow,
            event_type=event_type,
            actor_id=actor_id,
            actor_source=actor_source,
            timestamp=timestamp,
            reason=reason,
        )

    @staticmethod
    def _validate_workflow(
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
    ) -> None:
        if (
            summary.project_id != workflow.project_id
            or summary.workflow_id != workflow.workflow_id
        ):
            raise ValueError("workflow execution summary is outside workflow")
        if workflow.version < summary.workflow_version:
            raise ValueError("workflow version cannot precede execution evidence")

    def _next_event(
        self,
        summary: WorkflowRunSummary,
        workflow: GovernedWorkflow,
        **values,
    ) -> WorkflowExecutionEvent:
        return self._event(
            sequence=summary.event_count + 1,
            project_id=summary.project_id,
            workflow_id=summary.workflow_id,
            workflow_version=workflow.version,
            run_id=summary.run_id,
            causation_id=summary.last_event_id,
            **values,
        )

    @staticmethod
    def _event(**values) -> WorkflowExecutionEvent:
        seed = {
            **values,
            "event_type": values["event_type"].value,
            "actor_source": values["actor_source"].value,
            "correlation_id": values["run_id"],
        }
        digest = _canonical_digest(seed)[:24]
        return WorkflowExecutionEvent(
            event_id=f"workflow_event_{digest}",
            correlation_id=values["run_id"],
            **values,
        )
