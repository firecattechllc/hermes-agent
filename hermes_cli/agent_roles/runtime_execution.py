"""Governed, non-executing lifecycle for prepared workflow dispatches.

The coordinator records explicit caller-driven runtime transitions.  It never
invokes a provider, process, agent, retry, scheduler, or promotion boundary.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .execution import (
    ExecutionEvidence,
    ExecutionOutcome,
    ExecutionResult,
    FailureCategory,
    GovernedExecutionService,
)
from .execution_planning import RoleExecutionPlan
from .models import AssignmentStatus
from .runtime_session import RuntimeSession, RuntimeSessionState
from .service import AgentRoleService
from .workflow import GovernedWorkflow, WorkflowState
from .workflow_dispatch import WorkflowDispatchOutcome, WorkflowDispatchStatus
from .workflow_execution import WorkflowExecutionEvidenceService, WorkflowRunStatus
from .workflow_execution_store import WorkflowExecutionRecorder, WorkflowExecutionStore
from .workflow_scheduling import CoordinationStatus, WorkflowExecutionIntent


RUNTIME_EXECUTION_SCHEMA_VERSION = 1
MAX_RUNTIME_EXECUTION_EVIDENCE_REFS = 100


class RuntimeExecutionState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    POLICY_DENIED = "policy_denied"


TERMINAL_RUNTIME_EXECUTION_STATES = frozenset({
    RuntimeExecutionState.SUCCEEDED,
    RuntimeExecutionState.FAILED,
    RuntimeExecutionState.CANCELLED,
    RuntimeExecutionState.BLOCKED,
    RuntimeExecutionState.POLICY_DENIED,
})


def _safe_text(value: str, field_name: str, *, maximum: int = 1024) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds bounded length")
    lowered = value.lower()
    if any(marker in lowered for marker in (
        "api_key=", "api-key=", "authorization: bearer ", "password=",
        "private key-----", "secret=", "token=",
    )):
        raise ValueError(f"{field_name} must not contain secrets")
    return value


class RuntimeExecutionRecord(BaseModel):
    """One immutable revision of an explicitly driven execution lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = RUNTIME_EXECUTION_SCHEMA_VERSION
    execution_id: str = Field(..., min_length=1, max_length=128)
    revision: int = Field(..., ge=1)
    state: RuntimeExecutionState
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    workflow_version: int = Field(..., ge=1)
    run_id: str = Field(..., min_length=1, max_length=128)
    node_run_id: str = Field(..., min_length=1, max_length=128)
    dispatch_id: str = Field(..., min_length=1, max_length=128)
    dispatch_fingerprint: str = Field(..., min_length=64, max_length=64)
    intent_id: str = Field(..., min_length=1, max_length=128)
    intent_fingerprint: str = Field(..., min_length=64, max_length=64)
    claim_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    contract_id: str = Field(..., min_length=1, max_length=128)
    receipt_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    runtime: str = Field(..., min_length=1, max_length=128)
    repository_root: str = Field(..., min_length=1, max_length=4096)
    engine: Optional[str] = Field(default=None, min_length=1, max_length=128)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=128)
    model: Optional[str] = Field(default=None, min_length=1, max_length=256)
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=128)
    attempt: int = Field(default=1, ge=1)
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    started_at: Optional[int] = Field(default=None, ge=0)
    completed_at: Optional[int] = Field(default=None, ge=0)
    last_heartbeat_at: Optional[int] = Field(default=None, ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)
    result: Optional[ExecutionResult] = None
    session: RuntimeSession
    evidence_refs: Tuple[str, ...] = Field(
        default_factory=tuple, max_length=MAX_RUNTIME_EXECUTION_EVIDENCE_REFS
    )

    @field_validator(
        "execution_id", "project_id", "workflow_id", "run_id", "node_run_id",
        "dispatch_id", "intent_id", "claim_id", "session_id", "contract_id",
        "receipt_id", "assignment_id", "plan_id", "role_id", "agent_id",
        "authorization_id", "runtime", "repository_root", "engine", "provider",
        "model", "actor_id", "correlation_id", "causation_id", "reason",
    )
    @classmethod
    def _normalise_text(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        maximum = 4096 if info.field_name == "repository_root" else 1024
        return _safe_text(value, info.field_name, maximum=maximum)

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        refs = []
        for raw in values:
            value = _safe_text(raw, "evidence_ref", maximum=512)
            if value not in refs:
                refs.append(value)
        return tuple(refs)

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeExecutionRecord":
        if self.schema_version != RUNTIME_EXECUTION_SCHEMA_VERSION:
            raise ValueError("unsupported runtime execution schema version")
        if self.correlation_id != self.run_id:
            raise ValueError("runtime execution correlation_id must equal run_id")
        if self.updated_at < self.created_at:
            raise ValueError("runtime execution update predates creation")
        if self.state == RuntimeExecutionState.READY:
            if self.started_at is not None or self.completed_at is not None or self.result:
                raise ValueError("ready execution cannot contain runtime result state")
        elif self.state == RuntimeExecutionState.RUNNING:
            if self.started_at is None or self.completed_at is not None or self.result:
                raise ValueError("running execution requires only a start timestamp")
        else:
            if self.started_at is None or self.completed_at is None or self.result is None:
                raise ValueError("terminal execution requires start, completion, and result")
            expected = {
                RuntimeExecutionState.SUCCEEDED: ExecutionOutcome.SUCCEEDED,
                RuntimeExecutionState.FAILED: ExecutionOutcome.FAILED,
                RuntimeExecutionState.CANCELLED: ExecutionOutcome.CANCELLED,
                RuntimeExecutionState.BLOCKED: ExecutionOutcome.BLOCKED,
                RuntimeExecutionState.POLICY_DENIED: ExecutionOutcome.POLICY_DENIED,
            }[self.state]
            if self.result.outcome != expected:
                raise ValueError("runtime execution result outcome mismatch")
        if self.last_heartbeat_at is not None and (
            self.started_at is None
            or self.last_heartbeat_at < self.started_at
            or self.last_heartbeat_at > self.updated_at
        ):
            raise ValueError("runtime execution heartbeat is outside lifecycle bounds")
        if self.result is not None and (
            self.result.project_id != self.project_id
            or self.result.assignment_id != self.assignment_id
            or self.result.contract_id != self.contract_id
            or self.result.receipt_id != self.receipt_id
            or self.result.session_id != self.session_id
            or self.result.plan_id != self.plan_id
            or self.result.role_id != self.role_id
            or self.result.agent_id != self.agent_id
            or self.result.started_at != self.started_at
            or self.result.completed_at != self.completed_at
        ):
            raise ValueError("runtime execution result association mismatch")
        if self.result is not None:
            if len(self.result.blocking_reasons) > 32:
                raise ValueError("runtime execution blocking reasons exceed limit")
            for reason in self.result.blocking_reasons:
                _safe_text(reason, "blocking_reason", maximum=1024)
        expected_session_state = {
            RuntimeExecutionState.READY: RuntimeSessionState.READY,
            RuntimeExecutionState.RUNNING: RuntimeSessionState.RUNNING,
            RuntimeExecutionState.SUCCEEDED: RuntimeSessionState.SUCCEEDED,
            RuntimeExecutionState.FAILED: RuntimeSessionState.FAILED,
            RuntimeExecutionState.CANCELLED: RuntimeSessionState.CANCELLED,
            RuntimeExecutionState.BLOCKED: RuntimeSessionState.BLOCKED,
            RuntimeExecutionState.POLICY_DENIED: RuntimeSessionState.POLICY_DENIED,
        }[self.state]
        if (
            self.session.state != expected_session_state
            or self.session.session_id != self.session_id
            or self.session.project_id != self.project_id
            or self.session.contract_id != self.contract_id
            or self.session.receipt_id != self.receipt_id
            or self.session.assignment_id != self.assignment_id
            or self.session.role_id != self.role_id
            or self.session.agent_id != self.agent_id
            or self.session.updated_at > self.updated_at
            or self.session.execution_started != (self.state != RuntimeExecutionState.READY)
        ):
            raise ValueError("runtime execution session association mismatch")
        nested = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in nested for marker in (
            "api_key=", "api-key=", "authorization: bearer ", "password=",
            "private key-----", "secret=", "token=",
        )):
            raise ValueError("runtime execution record must not contain secrets")
        return self

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class _RuntimeExecutionStore(Protocol):
    def create(self, record: RuntimeExecutionRecord) -> RuntimeExecutionRecord: ...
    def append(self, record: RuntimeExecutionRecord, *, expected_revision: int) -> RuntimeExecutionRecord: ...
    def get(self, project_id: str, execution_id: str) -> Optional[RuntimeExecutionRecord]: ...
    def find_by_dispatch(self, project_id: str, dispatch_id: str) -> Optional[RuntimeExecutionRecord]: ...


class _RuntimeExecutionVisibility(Protocol):
    def publish(self, record: RuntimeExecutionRecord): ...


class _DispatchStore(Protocol):
    def get(self, project_id: str, dispatch_id: str) -> Optional[WorkflowDispatchOutcome]: ...


class _Scheduling(Protocol):
    def get(self, project_id: str, intent_id: str) -> Optional[WorkflowExecutionIntent]: ...


class _Workflows(Protocol):
    def get(self, project_id: str, workflow_id: str) -> Optional[GovernedWorkflow]: ...


class RuntimeExecutionError(RuntimeError):
    """Fail-closed runtime execution lifecycle violation."""


class RuntimeExecutionPublicationError(RuntimeExecutionError):
    def __init__(self, record: RuntimeExecutionRecord, target: str) -> None:
        super().__init__(
            f"runtime execution persisted but {target} publication failed; reconcile"
        )
        self.record = record
        self.target = target


class GovernedRuntimeExecutionCoordinator:
    """Persist explicit lifecycle transitions without performing execution."""

    def __init__(
        self, *, roles: AgentRoleService, dispatches: _DispatchStore, scheduling: _Scheduling,
        workflows: _Workflows, workflow_evidence: WorkflowExecutionStore,
        executions: _RuntimeExecutionStore,
        workflow_recorder: Optional[WorkflowExecutionRecorder] = None,
        visibility: Optional[_RuntimeExecutionVisibility] = None,
    ) -> None:
        self._roles = roles
        self._dispatches = dispatches
        self._scheduling = scheduling
        self._workflows = workflows
        self._workflow_evidence = workflow_evidence
        self._executions = executions
        self._workflow_recorder = workflow_recorder
        self._visibility = visibility
        self._evidence_service = WorkflowExecutionEvidenceService()
        self._execution_service = GovernedExecutionService()

    @staticmethod
    def execution_id_for(dispatch: WorkflowDispatchOutcome) -> str:
        seed = f"{dispatch.project_id}|{dispatch.dispatch_id}|{dispatch.session.session_id if dispatch.session else ''}"
        return "runtime_execution_" + hashlib.sha256(seed.encode()).hexdigest()[:24]

    def admit(
        self, *, project_id: str, dispatch_id: str, plan: RoleExecutionPlan,
        actor_id: str, timestamp: int,
    ) -> RuntimeExecutionRecord:
        existing = self._executions.find_by_dispatch(project_id, dispatch_id)
        dispatch, intent, workflow = self._validate_authority(
            project_id, dispatch_id, plan, actor_id, timestamp,
            require_unstarted=existing is None,
        )
        if existing is not None:
            self._validate_record_authority(existing, dispatch, intent, workflow, plan)
            self._publish(existing)
            return existing
        assert dispatch.session and dispatch.contract and dispatch.receipt
        record = RuntimeExecutionRecord(
            execution_id=self.execution_id_for(dispatch), revision=1,
            state=RuntimeExecutionState.READY, project_id=project_id,
            workflow_id=dispatch.workflow_id, workflow_version=workflow.version,
            run_id=dispatch.run_id, node_run_id=intent.node_run_id,
            dispatch_id=dispatch.dispatch_id, dispatch_fingerprint=dispatch.fingerprint,
            intent_id=intent.intent_id,
            intent_fingerprint=dispatch.intent_fingerprint,
            claim_id=dispatch.claim_id, session_id=dispatch.session.session_id,
            contract_id=dispatch.contract.contract_id,
            receipt_id=dispatch.receipt.receipt_id,
            assignment_id=dispatch.assignment_id, plan_id=dispatch.plan_id,
            role_id=dispatch.role_id, agent_id=dispatch.agent_id,
            authorization_id=dispatch.authorization_id,
            runtime=dispatch.contract.environment.runtime,
            repository_root=dispatch.contract.workspace.repository_root,
            engine=dispatch.contract.environment.engine,
            provider=dispatch.contract.environment.provider,
            model=dispatch.contract.environment.model, actor_id=actor_id,
            correlation_id=dispatch.run_id, causation_id=dispatch.dispatch_id,
            created_at=timestamp, updated_at=timestamp,
            reason="prepared dispatch admitted for explicit execution",
            session=dispatch.session,
            evidence_refs=dispatch.evidence_refs + (dispatch.dispatch_id,),
        )
        persisted = self._executions.create(record)
        self._publish(persisted)
        return persisted

    def start(
        self, *, project_id: str, execution_id: str, plan: RoleExecutionPlan,
        actor_id: str, timestamp: int,
    ) -> RuntimeExecutionRecord:
        current, dispatch, intent, workflow = self._current_authority(
            project_id, execution_id, plan, actor_id, timestamp
        )
        if current.state != RuntimeExecutionState.READY:
            raise RuntimeExecutionError("only ready runtime execution can start")
        assert dispatch.session
        session = self._execution_service.start(current.session, plan, started_at=timestamp)
        record = RuntimeExecutionRecord.model_validate({
            **current.model_dump(mode="python"),
            "revision": current.revision + 1,
            "state": RuntimeExecutionState.RUNNING,
            "updated_at": timestamp,
            "started_at": timestamp,
            "actor_id": actor_id,
            "causation_id": current.fingerprint,
            "reason": "explicit governed execution start recorded",
            "session": session,
        })
        persisted = self._executions.append(record, expected_revision=current.revision)
        self._record_step7_start(persisted, workflow, plan)
        self._publish(persisted)
        return persisted

    def heartbeat(
        self, *, project_id: str, execution_id: str, plan: RoleExecutionPlan,
        actor_id: str, timestamp: int, reason: str = "explicit runtime heartbeat",
    ) -> RuntimeExecutionRecord:
        current, _, _, _ = self._current_authority(
            project_id, execution_id, plan, actor_id, timestamp
        )
        if current.state != RuntimeExecutionState.RUNNING:
            raise RuntimeExecutionError("only running runtime execution can heartbeat")
        record = RuntimeExecutionRecord.model_validate({
            **current.model_dump(mode="python"),
            "revision": current.revision + 1, "updated_at": timestamp,
            "last_heartbeat_at": timestamp, "actor_id": actor_id,
            "causation_id": current.fingerprint,
            "reason": _safe_text(reason, "reason"),
        })
        persisted = self._executions.append(record, expected_revision=current.revision)
        self._publish(persisted)
        return persisted

    def complete(
        self, *, project_id: str, execution_id: str, plan: RoleExecutionPlan,
        actor_id: str, timestamp: int, outcome: ExecutionOutcome, summary: str,
        evidence: Tuple[ExecutionEvidence, ...],
        failure_category: FailureCategory = FailureCategory.NONE,
        blocking_reasons: Tuple[str, ...] = (), approvals: Tuple[str, ...] = (),
    ) -> RuntimeExecutionRecord:
        if outcome not in {
            ExecutionOutcome.SUCCEEDED, ExecutionOutcome.FAILED,
            ExecutionOutcome.CANCELLED, ExecutionOutcome.BLOCKED,
            ExecutionOutcome.POLICY_DENIED,
        }:
            raise RuntimeExecutionError("runtime lifecycle outcome is not supported")
        current, dispatch, _, workflow = self._current_authority(
            project_id, execution_id, plan, actor_id, timestamp
        )
        if current.state != RuntimeExecutionState.RUNNING:
            raise RuntimeExecutionError("only running runtime execution can complete")
        assert dispatch.contract and dispatch.receipt
        terminal_session, result = self._execution_service.complete(
            current.session, plan, dispatch.contract, dispatch.receipt,
            outcome=outcome, summary=_safe_text(summary, "summary", maximum=8192),
            evidence=evidence, completed_at=timestamp,
            failure_category=failure_category, blocking_reasons=blocking_reasons,
            approvals=approvals,
        )
        state = {
            ExecutionOutcome.SUCCEEDED: RuntimeExecutionState.SUCCEEDED,
            ExecutionOutcome.FAILED: RuntimeExecutionState.FAILED,
            ExecutionOutcome.CANCELLED: RuntimeExecutionState.CANCELLED,
            ExecutionOutcome.BLOCKED: RuntimeExecutionState.BLOCKED,
            ExecutionOutcome.POLICY_DENIED: RuntimeExecutionState.POLICY_DENIED,
        }[result.outcome]
        record = RuntimeExecutionRecord.model_validate({
            **current.model_dump(mode="python"),
            "revision": current.revision + 1, "state": state,
            "updated_at": timestamp, "completed_at": timestamp,
            "actor_id": actor_id, "causation_id": current.fingerprint,
            "reason": result.summary, "result": result,
            "session": terminal_session,
            "evidence_refs": tuple(dict.fromkeys(
                current.evidence_refs + (result.result_id,)
                + tuple(item.evidence_id for item in result.evidence)
            )),
        })
        persisted = self._executions.append(record, expected_revision=current.revision)
        self._record_step7_completion(persisted, workflow)
        self._publish(persisted)
        return persisted

    def cancel(self, **kwargs) -> RuntimeExecutionRecord:
        return self.complete(
            outcome=ExecutionOutcome.CANCELLED,
            failure_category=FailureCategory.CANCELLED,
            evidence=kwargs.pop("evidence", ()), **kwargs,
        )

    def reconcile(
        self, project_id: str, execution_id: str, *,
        plan: RoleExecutionPlan, actor_id: str, timestamp: int,
    ) -> RuntimeExecutionRecord:
        record = self._executions.get(project_id, execution_id)
        if record is None:
            raise RuntimeExecutionError("runtime execution not found")
        record, _, _, workflow = self._current_authority(
            project_id, execution_id, plan, actor_id, timestamp
        )
        if record.state == RuntimeExecutionState.RUNNING:
            self._record_step7_start(record, workflow, plan)
        elif record.state in TERMINAL_RUNTIME_EXECUTION_STATES:
            self._record_step7_completion(record, workflow)
        self._publish(record)
        return record

    def _current_authority(self, project_id, execution_id, plan, actor_id, timestamp):
        current = self._executions.get(project_id, execution_id)
        if current is None:
            raise RuntimeExecutionError("runtime execution not found")
        dispatch, intent, workflow = self._validate_authority(
            project_id, current.dispatch_id, plan, actor_id, timestamp,
            require_unstarted=False,
        )
        self._validate_record_authority(current, dispatch, intent, workflow, plan)
        if timestamp < current.updated_at:
            raise RuntimeExecutionError("runtime execution timestamp is stale")
        return current, dispatch, intent, workflow

    def _validate_authority(
        self, project_id, dispatch_id, plan, actor_id, timestamp, *, require_unstarted,
    ):
        dispatch = self._dispatches.get(project_id, dispatch_id)
        if dispatch is None or dispatch.status != WorkflowDispatchStatus.PREPARED:
            raise RuntimeExecutionError("durable PREPARED dispatch is required")
        if not dispatch.session or dispatch.session.state != RuntimeSessionState.READY:
            raise RuntimeExecutionError("durable ready prepared session is required")
        intent = self._scheduling.get(project_id, dispatch.intent_id)
        workflow = self._workflows.get(project_id, dispatch.workflow_id)
        summary = self._workflow_evidence.get_summary(project_id, dispatch.run_id)
        if intent is None or workflow is None or summary is None:
            raise RuntimeExecutionError("durable execution authority is incomplete")
        if actor_id.strip() != dispatch.claimed_by:
            raise RuntimeExecutionError("runtime execution actor must equal dispatch authority")
        if timestamp < dispatch.created_at:
            raise RuntimeExecutionError("runtime execution timestamp predates dispatch")
        try:
            assignment = self._roles.get_assignment(project_id, dispatch.assignment_id)
            role = self._roles.get_role(project_id, dispatch.role_id)
        except KeyError as exc:
            raise RuntimeExecutionError("current role authority is missing") from exc
        if (
            assignment.status not in {
                AssignmentStatus.ASSIGNED,
                AssignmentStatus.ACCEPTED,
                AssignmentStatus.ACTIVE,
            }
            or assignment.project_id != project_id
            or assignment.role_id != dispatch.role_id
            or assignment.assigned_agent_id != dispatch.agent_id
            or not role.active
        ):
            raise RuntimeExecutionError("current role assignment authority mismatch")
        if (
            intent.status != CoordinationStatus.COMPLETED
            or dispatch.dispatch_id not in intent.evidence_refs
            or intent.actor_id != dispatch.actor_id
            or intent.reason != dispatch.reason
            or intent.project_id != dispatch.project_id
            or intent.workflow_id != dispatch.workflow_id
            or intent.run_id != dispatch.run_id
            or intent.intent_id != dispatch.intent_id
            or intent.assignment_id != dispatch.assignment_id
            or intent.plan_id != dispatch.plan_id
            or intent.role_id != dispatch.role_id
            or intent.agent_id != dispatch.agent_id
            or workflow.state != WorkflowState.ACTIVE
            or workflow.version != intent.workflow_version
            or summary.status != WorkflowRunStatus.RUNNING
            or plan.project_id != dispatch.project_id
            or plan.assignment_id != dispatch.assignment_id
            or plan.plan_id != dispatch.plan_id
            or plan.role_id != dispatch.role_id
            or plan.agent_id != dispatch.agent_id
            or plan.contract_id != dispatch.contract.contract_id
            or intent.authorization_id != dispatch.authorization_id
            or intent.node_run_id != WorkflowExecutionEvidenceService.node_run_id_for(
                dispatch.run_id, dispatch.assignment_id, dispatch.plan_id
            )
        ):
            raise RuntimeExecutionError("runtime execution authority association mismatch")
        if require_unstarted and summary.nodes and summary.nodes[-1].status.value == "running":
            raise RuntimeExecutionError("workflow node already started")
        return dispatch, intent, workflow

    @staticmethod
    def _validate_record_authority(record, dispatch, intent, workflow, plan) -> None:
        expected = (
            record.project_id == dispatch.project_id,
            record.workflow_id == dispatch.workflow_id,
            record.workflow_version == workflow.version,
            record.run_id == dispatch.run_id,
            record.node_run_id == intent.node_run_id,
            record.dispatch_fingerprint == dispatch.fingerprint,
            record.intent_fingerprint == dispatch.intent_fingerprint,
            record.claim_id == dispatch.claim_id,
            record.session_id == dispatch.session.session_id,
            record.contract_id == dispatch.contract.contract_id,
            record.receipt_id == dispatch.receipt.receipt_id,
            record.assignment_id == plan.assignment_id,
            record.plan_id == plan.plan_id,
            record.role_id == plan.role_id,
            record.agent_id == plan.agent_id,
            record.authorization_id == dispatch.authorization_id,
            record.runtime == dispatch.contract.environment.runtime,
            record.repository_root == dispatch.contract.workspace.repository_root,
            record.engine == dispatch.contract.environment.engine,
            record.provider == dispatch.contract.environment.provider,
            record.model == dispatch.contract.environment.model,
        )
        if not all(expected):
            raise RuntimeExecutionError("durable runtime execution authority mismatch")

    def _record_step7_start(self, record, workflow, plan) -> None:
        if self._workflow_recorder is None:
            return
        summary = self._workflow_evidence.get_summary(record.project_id, record.run_id)
        if summary is None:
            raise RuntimeExecutionError("workflow execution summary not found")
        if summary.nodes and summary.nodes[-1].node_run_id == record.node_run_id:
            node = summary.nodes[-1]
            if (
                node.status.value != "running"
                or node.assignment_id != record.assignment_id
                or node.plan_id != record.plan_id
                or node.role_id != record.role_id
            ):
                raise RuntimeExecutionError("conflicting Step 7 start evidence")
            return
        event = self._evidence_service.start_node(
            summary, workflow, plan, actor_id=record.actor_id,
            timestamp=record.started_at or record.updated_at,
        )
        try:
            self._workflow_recorder.record(event)
        except Exception as exc:
            raise RuntimeExecutionPublicationError(record, "Step 7 evidence") from exc

    def _record_step7_completion(self, record, workflow) -> None:
        if self._workflow_recorder is None or record.result is None:
            return
        summary = self._workflow_evidence.get_summary(record.project_id, record.run_id)
        if summary is None:
            raise RuntimeExecutionError("workflow execution summary not found")
        if summary.nodes and summary.nodes[-1].status.value != "running":
            node = summary.nodes[-1]
            if (
                node.node_run_id != record.node_run_id
                or node.result_id != record.result.result_id
                or node.status.value != record.result.outcome.value
            ):
                raise RuntimeExecutionError("conflicting Step 7 completion evidence")
            return
        event = self._evidence_service.complete_node(
            summary, workflow, record.result, actor_id=record.actor_id,
            timestamp=record.completed_at or record.updated_at,
        )
        try:
            self._workflow_recorder.record(event)
        except Exception as exc:
            raise RuntimeExecutionPublicationError(record, "Step 7 evidence") from exc

    def _publish(self, record) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeExecutionPublicationError(record, "Mission Control") from exc
