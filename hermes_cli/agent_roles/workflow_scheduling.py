"""Governed scheduling boundary for authorized workflow nodes.

Scheduling records coordination intent only.  This module never launches an
agent, invokes a provider, retries work, or promotes an artifact.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .execution_planning import RoleExecutionPlan
from .models import AssignmentStatus
from .service import AgentRoleService
from .workflow import (
    AuthorizationDecision,
    GovernedWorkflow,
    WorkflowAuthorization,
    WorkflowDecision,
    WorkflowState,
)
from .workflow_execution import (
    EvidenceActorSource,
    ExecutionEventType,
    NodeRunStatus,
    WorkflowExecutionEvidenceService,
    WorkflowRunStatus,
    WorkflowRunSummary,
)
from .workflow_execution_store import WorkflowExecutionStore
from .workflow_store import GovernedWorkflowStore


WORKFLOW_SCHEDULING_SCHEMA_VERSION = 1
MAX_SCHEDULING_EVIDENCE_REFS = 32
MAX_SCHEDULING_EVIDENCE_REF_LENGTH = 512
DEFAULT_SCHEDULING_CAPACITY = 256
MAX_CLAIM_LEASE_SECONDS = 86_400


def _safe_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    lowered = value.lower()
    if any(marker in lowered for marker in (
        "api_key=", "api-key=", "authorization: bearer ", "password=",
        "private key-----", "secret=", "token=",
    )):
        raise ValueError("workflow scheduling text must not contain secrets")
    return value


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class CoordinationStatus(str, Enum):
    SCHEDULED = "scheduled"
    CLAIMED = "claimed"
    DEFERRED = "deferred"
    REFUSED = "refused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    COMPLETED = "completed"


TERMINAL_COORDINATION_STATUSES = frozenset({
    CoordinationStatus.REFUSED,
    CoordinationStatus.CANCELLED,
    CoordinationStatus.EXPIRED,
    CoordinationStatus.COMPLETED,
})
# EXPIRED is terminal by design.  A later attempt requires a new governed
# workflow stage and explicit authorization; the scheduler never requeues it.


class WorkflowExecutionIntent(BaseModel):
    """One immutable revision of an authorized scheduling intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = WORKFLOW_SCHEDULING_SCHEMA_VERSION
    intent_id: str = Field(..., min_length=1, max_length=128)
    version: int = Field(..., ge=1)
    status: CoordinationStatus
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    workflow_version: int = Field(..., ge=1)
    run_id: str = Field(..., min_length=1, max_length=128)
    node_run_id: str = Field(..., min_length=1, max_length=128)
    stage_sequence: int = Field(..., ge=1)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    decision: WorkflowDecision
    authorization_id: str = Field(..., min_length=1, max_length=128)
    authorization: WorkflowAuthorization
    attempt_id: str = Field(..., min_length=1, max_length=128)
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=128)
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    available_at: int = Field(..., ge=0)
    claim_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    claimed_by: Optional[str] = Field(default=None, min_length=1, max_length=256)
    lease_expires_at: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = Field(
        default_factory=tuple, max_length=MAX_SCHEDULING_EVIDENCE_REFS
    )

    @field_validator(
        "intent_id", "project_id", "workflow_id", "run_id", "node_run_id",
        "assignment_id", "plan_id", "role_id", "agent_id", "authorization_id",
        "attempt_id", "actor_id", "correlation_id", "causation_id", "claim_id",
        "claimed_by", "reason",
    )
    @classmethod
    def _normalise_text(cls, value: Optional[str], info) -> Optional[str]:
        return _safe_text(value, info.field_name) if value is not None else None

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        result = []
        for value in values:
            value = _safe_text(value, "evidence_ref")
            if len(value) > MAX_SCHEDULING_EVIDENCE_REF_LENGTH:
                raise ValueError("evidence_ref exceeds the bounded length")
            if value not in result:
                result.append(value)
        return tuple(result)

    @model_validator(mode="after")
    def _validate_intent(self) -> "WorkflowExecutionIntent":
        if self.schema_version != WORKFLOW_SCHEDULING_SCHEMA_VERSION:
            raise ValueError("unsupported workflow scheduling schema version")
        if self.decision not in {WorkflowDecision.ADVANCE, WorkflowDecision.RETRY}:
            raise ValueError("only advance or retry may create execution intent")
        if (
            self.authorization_id != self.authorization.authorization_id
            or self.project_id != self.authorization.project_id
            or self.workflow_id != self.authorization.workflow_id
            or self.decision != self.authorization.decision
            or self.authorization.disposition != AuthorizationDecision.APPROVED
            or self.authorization.expected_version + 1 != self.workflow_version
        ):
            raise ValueError("workflow scheduling authorization provenance mismatch")
        if self.decision == WorkflowDecision.ADVANCE:
            if self.authorization.to_role_id != self.role_id:
                raise ValueError("advance scheduling target role mismatch")
        elif self.authorization.to_role_id is not None:
            raise ValueError("retry scheduling cannot carry a target role")
        if self.updated_at < self.created_at or self.available_at < self.created_at:
            raise ValueError("workflow scheduling timestamps cannot move backwards")
        if self.created_at < self.authorization.timestamp:
            raise ValueError("workflow scheduling cannot predate authorization")
        if self.correlation_id != self.run_id:
            raise ValueError("scheduling correlation_id must equal run_id")
        claim_values = (self.claim_id, self.claimed_by, self.lease_expires_at)
        if self.status == CoordinationStatus.CLAIMED:
            if any(value is None for value in claim_values):
                raise ValueError("claimed intent requires complete lease identity")
            if self.lease_expires_at is not None and self.lease_expires_at <= self.updated_at:
                raise ValueError("claim lease must expire after claim timestamp")
        elif any(value is not None for value in claim_values):
            raise ValueError("claim identity is only valid for claimed intent")
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))

    @property
    def stable_sort_key(self) -> tuple[int, str]:
        return (self.available_at, self.intent_id)


class _SchedulingStore(Protocol):
    def create(self, intent: WorkflowExecutionIntent) -> WorkflowExecutionIntent: ...
    def get(self, project_id: str, intent_id: str) -> Optional[WorkflowExecutionIntent]: ...
    def list(self, project_id: str, *, status: Optional[CoordinationStatus] = None) -> Tuple[WorkflowExecutionIntent, ...]: ...
    def claim(self, project_id: str, intent_id: str, *, claimed_by: str, timestamp: int, lease_seconds: int) -> WorkflowExecutionIntent: ...
    def transition(self, project_id: str, intent_id: str, *, status: CoordinationStatus, actor_id: str, timestamp: int, reason: str, evidence_refs: Tuple[str, ...] = (), available_at: Optional[int] = None, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent: ...


class _SchedulingVisibility(Protocol):
    def publish(self, intent: WorkflowExecutionIntent): ...
    def list_records(self, project_id: str, **filters): ...


class WorkflowSchedulingError(RuntimeError):
    """Fail-closed scheduling or coordination boundary violation."""


class SchedulingVisibilityError(WorkflowSchedulingError):
    def __init__(self, intent: WorkflowExecutionIntent) -> None:
        super().__init__(
            "workflow scheduling state persisted but Mission Control publication failed; reconcile visibility"
        )
        self.intent = intent


class GovernedWorkflowSchedulingCoordinator:
    """Validate durable authority, then coordinate intent state only."""

    _ASSIGNMENT_STATES = {
        AssignmentStatus.ASSIGNED,
        AssignmentStatus.ACCEPTED,
        AssignmentStatus.ACTIVE,
    }

    def __init__(
        self,
        *,
        roles: AgentRoleService,
        workflows: GovernedWorkflowStore,
        evidence: WorkflowExecutionStore,
        scheduling: _SchedulingStore,
        visibility: Optional[_SchedulingVisibility] = None,
    ) -> None:
        self._roles = roles
        self._workflows = workflows
        self._evidence = evidence
        self._scheduling = scheduling
        self._visibility = visibility

    def schedule(
        self,
        *,
        project_id: str,
        workflow_id: str,
        run_id: str,
        plan: RoleExecutionPlan,
        authorization_id: str,
        actor_id: str,
        timestamp: int,
    ) -> WorkflowExecutionIntent:
        workflow = self._workflows.get(project_id, workflow_id)
        summary = self._evidence.get_summary(project_id, run_id)
        if workflow is None or summary is None or summary.run_id != run_id:
            raise WorkflowSchedulingError("durable workflow and execution evidence are required")
        authorization = self._validate_authority(
            workflow, summary, plan, authorization_id=authorization_id
        )
        if timestamp < max(
            workflow.updated_at,
            summary.updated_at,
            plan.created_at,
            authorization.timestamp,
        ):
            raise WorkflowSchedulingError(
                "scheduling timestamp cannot precede durable authority or evidence"
            )
        events = self._evidence.events_for_run(project_id, run_id)
        auth_event = events[-1] if events else None
        request_event = events[-2] if len(events) >= 2 else None
        if (
            auth_event is None
            or len(events) != summary.event_count
            or auth_event.event_type != ExecutionEventType.AUTHORIZATION_GRANTED
            or auth_event.authorization_id != authorization.authorization_id
            or auth_event.decision != authorization.decision
            or auth_event.to_role_id != authorization.to_role_id
            or auth_event.actor_id != authorization.actor
            or auth_event.actor_source != EvidenceActorSource.HUMAN
            or auth_event.timestamp != authorization.timestamp
            or auth_event.reason != authorization.reason
            or auth_event.project_id != authorization.project_id
            or auth_event.workflow_id != authorization.workflow_id
            or auth_event.run_id != run_id
            or auth_event.workflow_version != workflow.version
            or auth_event.event_id != summary.last_event_id
            or request_event is None
            or auth_event.causation_id != request_event.event_id
            or auth_event.correlation_id != run_id
            or request_event.correlation_id != run_id
            or request_event.project_id != authorization.project_id
            or request_event.workflow_id != authorization.workflow_id
            or request_event.run_id != run_id
            or request_event.workflow_version != authorization.expected_version
            or request_event.decision != authorization.decision
            or request_event.to_role_id != authorization.to_role_id
            or request_event.actor_source != EvidenceActorSource.ORCHESTRATOR
            or request_event.event_type
            != (
                ExecutionEventType.RETRY_REQUESTED
                if authorization.decision == WorkflowDecision.RETRY
                else ExecutionEventType.TRANSITION_REQUESTED
            )
        ):
            raise WorkflowSchedulingError("matching authorization-granted execution evidence is required")

        stage = workflow.stages[workflow.current_stage]
        seed = "|".join((
            project_id, workflow_id, run_id, str(stage.sequence), stage.assignment_id,
            stage.plan_id, authorization.authorization_id,
        ))
        digest = hashlib.sha256(seed.encode()).hexdigest()[:24]
        node_run_id = WorkflowExecutionEvidenceService.node_run_id_for(
            run_id,
            plan.assignment_id,
            plan.plan_id,
        )
        intent = WorkflowExecutionIntent(
            intent_id=f"workflow_intent_{digest}",
            version=1,
            status=CoordinationStatus.SCHEDULED,
            project_id=project_id,
            workflow_id=workflow_id,
            workflow_version=workflow.version,
            run_id=run_id,
            node_run_id=node_run_id,
            stage_sequence=stage.sequence,
            assignment_id=plan.assignment_id,
            plan_id=plan.plan_id,
            role_id=plan.role_id,
            agent_id=plan.agent_id,
            decision=authorization.decision,
            authorization_id=authorization.authorization_id,
            authorization=authorization,
            attempt_id=f"attempt_{digest}",
            actor_id=actor_id,
            correlation_id=run_id,
            causation_id=auth_event.event_id,
            created_at=timestamp,
            updated_at=timestamp,
            available_at=timestamp,
            evidence_refs=(authorization.authorization_id, auth_event.event_id, summary.last_event_id),
        )
        existing = self._scheduling.get(project_id, intent.intent_id)
        if existing is not None:
            self._require_visibility_current(existing)
        return self._persist_and_publish(lambda: self._scheduling.create(intent))

    def list_eligible(self, project_id: str, *, timestamp: int) -> Tuple[WorkflowExecutionIntent, ...]:
        return tuple(
            item for item in self._scheduling.list(project_id)
            if item.status in {CoordinationStatus.SCHEDULED, CoordinationStatus.DEFERRED}
            and item.available_at <= timestamp
        )

    def get(
        self, project_id: str, intent_id: str
    ) -> Optional[WorkflowExecutionIntent]:
        """Return the latest durable coordination revision, if present."""
        return self._scheduling.get(project_id, intent_id)

    def claim(self, project_id: str, intent_id: str, *, claimed_by: str, timestamp: int, lease_seconds: int) -> WorkflowExecutionIntent:
        self._require_current_and_visible(project_id, intent_id)
        return self._persist_and_publish(lambda: self._scheduling.claim(
            project_id, intent_id, claimed_by=claimed_by, timestamp=timestamp,
            lease_seconds=lease_seconds,
        ))

    def refuse(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent:
        return self._transition(
            project_id, intent_id, CoordinationStatus.REFUSED, actor_id,
            timestamp, reason, expected_claim_id=expected_claim_id,
        )

    def cancel(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent:
        return self._transition(
            project_id, intent_id, CoordinationStatus.CANCELLED, actor_id,
            timestamp, reason, expected_claim_id=expected_claim_id,
        )

    def defer(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, available_at: int, reason: str, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent:
        self._require_current_and_visible(project_id, intent_id)
        return self._persist_and_publish(lambda: self._scheduling.transition(
            project_id, intent_id, status=CoordinationStatus.DEFERRED,
            actor_id=actor_id, timestamp=timestamp, reason=reason,
            available_at=available_at, expected_claim_id=expected_claim_id,
        ))

    def complete(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, evidence_refs: Tuple[str, ...], expected_claim_id: str) -> WorkflowExecutionIntent:
        if not evidence_refs:
            raise WorkflowSchedulingError("completion requires bounded evidence")
        self._require_current_and_visible(project_id, intent_id)
        return self._persist_and_publish(lambda: self._scheduling.transition(
            project_id, intent_id, status=CoordinationStatus.COMPLETED,
            actor_id=actor_id, timestamp=timestamp, reason=reason,
            evidence_refs=evidence_refs, expected_claim_id=expected_claim_id,
        ))

    def expire_claim(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, expected_claim_id: str) -> WorkflowExecutionIntent:
        current = self._scheduling.get(project_id, intent_id)
        if current is None or current.status != CoordinationStatus.CLAIMED:
            raise WorkflowSchedulingError("only a claimed intent may expire")
        if current.claim_id != expected_claim_id or current.lease_expires_at is None or timestamp < current.lease_expires_at:
            raise WorkflowSchedulingError("claim is current and cannot be expired")
        return self._transition(
            project_id, intent_id, CoordinationStatus.EXPIRED, actor_id, timestamp,
            reason, expected_claim_id=expected_claim_id,
        )

    def reconcile_visibility(self, project_id: str) -> Tuple[WorkflowExecutionIntent, ...]:
        intents = self._scheduling.list(project_id)
        if self._visibility is not None:
            for intent in intents:
                self._visibility.publish(intent)
        return intents

    def _transition(self, project_id: str, intent_id: str, status: CoordinationStatus, actor_id: str, timestamp: int, reason: str, *, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent:
        self._require_current_and_visible(project_id, intent_id)
        return self._persist_and_publish(lambda: self._scheduling.transition(
            project_id, intent_id, status=status, actor_id=actor_id,
            timestamp=timestamp, reason=reason, expected_claim_id=expected_claim_id,
        ))

    def _persist_and_publish(self, operation) -> WorkflowExecutionIntent:
        intent = operation()
        if self._visibility is not None:
            try:
                self._visibility.publish(intent)
            except Exception as exc:
                raise SchedulingVisibilityError(intent) from exc
        return intent

    def _require_current_and_visible(
        self,
        project_id: str,
        intent_id: str,
    ) -> WorkflowExecutionIntent:
        current = self._scheduling.get(project_id, intent_id)
        if current is None:
            raise WorkflowSchedulingError("workflow scheduling intent not found")
        self._require_visibility_current(current)
        return current

    def _require_visibility_current(self, intent: WorkflowExecutionIntent) -> None:
        if self._visibility is None:
            return
        record = next(
            (
                item
                for item in self._visibility.list_records(intent.project_id)
                if item.intent_id == intent.intent_id
            ),
            None,
        )
        if (
            record is None
            or record.version != intent.version
            or record.intent_fingerprint != intent.fingerprint
        ):
            raise WorkflowSchedulingError(
                "Mission Control scheduling visibility is stale; reconcile before continuing"
            )

    def _validate_authority(self, workflow: GovernedWorkflow, summary: WorkflowRunSummary, plan: RoleExecutionPlan, *, authorization_id: str):
        if workflow.project_id != plan.project_id or summary.project_id != plan.project_id:
            raise WorkflowSchedulingError("project association mismatch")
        if summary.workflow_id != workflow.workflow_id:
            raise WorkflowSchedulingError("workflow execution association mismatch")
        if workflow.state != WorkflowState.ACTIVE or summary.status != WorkflowRunStatus.RUNNING:
            raise WorkflowSchedulingError("workflow and evidence are not eligible for scheduling")
        if (
            summary.workflow_version != workflow.version
            or summary.last_authorized_decision is None
        ):
            raise WorkflowSchedulingError("workflow authorization evidence is stale")
        if summary.nodes and summary.nodes[-1].status == NodeRunStatus.RUNNING:
            raise WorkflowSchedulingError("workflow run already has an active node")
        stage = workflow.stages[workflow.current_stage]
        if stage.result_id is not None or (
            stage.assignment_id, stage.plan_id, stage.role_id
        ) != (plan.assignment_id, plan.plan_id, plan.role_id):
            raise WorkflowSchedulingError("plan does not match current workflow stage")
        if stage.sequence != len(summary.nodes) + 1:
            raise WorkflowSchedulingError(
                "workflow stage does not follow durable node evidence"
            )
        for prior_stage, node in zip(
            workflow.stages[:workflow.current_stage],
            summary.nodes,
        ):
            if (
                prior_stage.sequence != node.sequence
                or prior_stage.assignment_id != node.assignment_id
                or prior_stage.plan_id != node.plan_id
                or prior_stage.role_id != node.role_id
                or prior_stage.result_id != node.result_id
            ):
                raise WorkflowSchedulingError(
                    "workflow stage history does not match execution evidence"
                )
        authorization = next(
            (item for item in workflow.authorizations if item.authorization_id == authorization_id),
            None,
        )
        workflow_event = workflow.events[-1]
        if (
            authorization is None
            or authorization.disposition != AuthorizationDecision.APPROVED
            or authorization.decision not in {WorkflowDecision.ADVANCE, WorkflowDecision.RETRY}
            or authorization.decision != summary.last_authorized_decision
            or authorization.timestamp != summary.updated_at
            or authorization.expected_version + 1 != workflow.version
            or workflow.authorizations[-1] != authorization
            or workflow_event.authorization_id != authorization.authorization_id
            or workflow_event.event_type
            != f"{authorization.decision.value}_authorized"
            or workflow_event.actor != authorization.actor
            or workflow_event.timestamp != authorization.timestamp
            or workflow_event.reason != authorization.reason
        ):
            raise WorkflowSchedulingError("explicit matching authorization is required")
        if workflow.current_stage < 1:
            raise WorkflowSchedulingError(
                "initial workflow stage has no authorized scheduling transition"
            )
        previous_stage = workflow.stages[workflow.current_stage - 1]
        if authorization.decision == WorkflowDecision.ADVANCE:
            if authorization.to_role_id != stage.role_id:
                raise WorkflowSchedulingError(
                    "advance authorization does not match scheduled stage"
                )
        elif (
            authorization.to_role_id is not None
            or previous_stage.role_id != stage.role_id
        ):
            raise WorkflowSchedulingError(
                "retry authorization must preserve prior role authority"
            )
        assignment = self._roles.get_assignment(plan.project_id, plan.assignment_id)
        if (
            assignment.status not in self._ASSIGNMENT_STATES
            or assignment.role_id != plan.role_id
            or assignment.assigned_agent_id != plan.agent_id
        ):
            raise WorkflowSchedulingError("plan exceeds durable assignment authority")
        return authorization
