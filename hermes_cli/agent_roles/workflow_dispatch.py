"""Governed admission from claimed workflow intent to runtime preparation.

This boundary is deliberately non-executing.  It validates a current Step 8
claim, prepares the existing launch/handoff/session artifacts, and records the
durable outcome before completing coordination.  It never starts an agent,
invokes a provider, retries work, or promotes an artifact.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .execution_planning import RoleExecutionPlan
from .launch import LaunchContract
from .launch_builder import LaunchContractBuilder
from .launch_validation import (
    LaunchContractValidator,
    LaunchValidationResult,
    RuntimeCompatibility,
)
from .runtime_handoff import RuntimeHandoffReceipt, RuntimeHandoffService
from .runtime_session import RuntimeSession, RuntimeSessionService, RuntimeSessionState
from .service import AgentRoleService
from .workflow import AuthorizationDecision, WorkflowDecision, WorkflowState
from .workflow_execution import (
    EvidenceActorSource,
    ExecutionEventType,
    WorkflowExecutionEvidenceService,
    WorkflowRunStatus,
)
from .workflow_execution_store import WorkflowExecutionStore
from .workflow_scheduling import CoordinationStatus, WorkflowExecutionIntent
from .workflow_store import GovernedWorkflowStore


WORKFLOW_DISPATCH_SCHEMA_VERSION = 1


class WorkflowDispatchStatus(str, Enum):
    PREPARED = "prepared"
    REFUSED = "refused"


class WorkflowDispatchOutcome(BaseModel):
    """Immutable terminal admission outcome for one claimed scheduling intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = WORKFLOW_DISPATCH_SCHEMA_VERSION
    dispatch_id: str = Field(..., min_length=1, max_length=128)
    status: WorkflowDispatchStatus
    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    run_id: str = Field(..., min_length=1, max_length=128)
    intent_id: str = Field(..., min_length=1, max_length=128)
    intent_version: int = Field(..., ge=1)
    intent_fingerprint: str = Field(..., min_length=64, max_length=64)
    claim_id: str = Field(..., min_length=1, max_length=128)
    claimed_by: str = Field(..., min_length=1, max_length=256)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=128)
    created_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)
    contract: Optional[LaunchContract] = None
    validation: Optional[LaunchValidationResult] = None
    receipt: Optional[RuntimeHandoffReceipt] = None
    session: Optional[RuntimeSession] = None
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @field_validator(
        "dispatch_id", "project_id", "workflow_id", "run_id", "intent_id",
        "claim_id", "claimed_by", "assignment_id", "plan_id", "role_id",
        "agent_id", "authorization_id", "actor_id", "correlation_id",
        "causation_id", "reason",
    )
    @classmethod
    def _normalise_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workflow dispatch text must not be blank")
        lowered = value.lower()
        if any(marker in lowered for marker in (
            "api_key=", "api-key=", "authorization: bearer ", "password=",
            "private key-----", "secret=", "token=",
        )):
            raise ValueError("workflow dispatch text must not contain secrets")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_evidence_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        result = []
        for raw in values:
            value = raw.strip()
            if not value or len(value) > 512:
                raise ValueError("workflow dispatch evidence reference is invalid")
            if value not in result:
                result.append(value)
        return tuple(result)

    @model_validator(mode="after")
    def _validate_outcome(self) -> "WorkflowDispatchOutcome":
        if self.schema_version != WORKFLOW_DISPATCH_SCHEMA_VERSION:
            raise ValueError("unsupported workflow dispatch schema version")
        if self.correlation_id != self.run_id:
            raise ValueError("workflow dispatch correlation_id must equal run_id")
        if self.contract is not None:
            if (
                self.validation is None
                or self.contract.project_id != self.project_id
                or self.contract.contract_id != self.validation.contract_id
                or self.contract.assignment_id != self.assignment_id
                or self.contract.role_id != self.role_id
                or self.contract.agent_id != self.agent_id
            ):
                raise ValueError("workflow dispatch contract association mismatch")
        elif self.validation is not None:
            raise ValueError("workflow dispatch validation requires contract")
        artifacts = (self.contract, self.validation, self.receipt, self.session)
        if self.status == WorkflowDispatchStatus.PREPARED:
            if any(item is None for item in artifacts):
                raise ValueError("prepared dispatch requires complete runtime artifacts")
            assert self.contract and self.validation and self.receipt and self.session
            if not self.validation.valid or not self.receipt.accepted:
                raise ValueError("prepared dispatch requires accepted validation and handoff")
            if self.session.state != RuntimeSessionState.READY:
                raise ValueError("prepared dispatch requires ready runtime session")
            if (
                self.contract.project_id != self.project_id
                or
                self.contract.contract_id != self.validation.contract_id
                or self.contract.contract_id != self.receipt.contract_id
                or self.contract.contract_id != self.session.contract_id
                or self.session.project_id != self.project_id
                or self.contract.assignment_id != self.assignment_id
                or self.session.assignment_id != self.assignment_id
                or self.contract.role_id != self.role_id
                or self.session.role_id != self.role_id
                or self.contract.agent_id != self.agent_id
                or self.session.agent_id != self.agent_id
                or self.receipt.receipt_id != self.session.receipt_id
                or self.receipt.request_fingerprint != self.session.request_fingerprint
            ):
                raise ValueError("workflow dispatch runtime artifact association mismatch")
        elif any(item is not None for item in (self.receipt, self.session)):
            raise ValueError("refused dispatch cannot contain handoff or session")
        nested = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        if any(marker in nested for marker in (
            "api_key=", "api-key=", "authorization: bearer ", "password=",
            "private key-----", "secret=", "token=",
        )):
            raise ValueError("workflow dispatch outcome must not contain secrets")
        return self

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class _DispatchStore(Protocol):
    def append(self, outcome: WorkflowDispatchOutcome) -> WorkflowDispatchOutcome: ...
    def get(self, project_id: str, dispatch_id: str) -> Optional[WorkflowDispatchOutcome]: ...
    def find_by_intent(self, project_id: str, intent_id: str) -> Optional[WorkflowDispatchOutcome]: ...


class _DispatchVisibility(Protocol):
    def publish(self, outcome: WorkflowDispatchOutcome): ...


class _SchedulingCoordinator(Protocol):
    def get(self, project_id: str, intent_id: str) -> Optional[WorkflowExecutionIntent]: ...
    def complete(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, evidence_refs: Tuple[str, ...], expected_claim_id: str) -> WorkflowExecutionIntent: ...
    def refuse(self, project_id: str, intent_id: str, *, actor_id: str, timestamp: int, reason: str, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent: ...


class WorkflowDispatchError(RuntimeError):
    """Fail-closed dispatch admission violation."""


class WorkflowDispatchVisibilityError(WorkflowDispatchError):
    def __init__(self, outcome: WorkflowDispatchOutcome) -> None:
        super().__init__(
            "workflow dispatch outcome persisted but Mission Control publication failed; reconcile visibility"
        )
        self.outcome = outcome


class GovernedWorkflowDispatchCoordinator:
    """Prepare runtime artifacts only for a current, durably authorized claim."""

    def __init__(
        self,
        *,
        roles: AgentRoleService,
        workflows: GovernedWorkflowStore,
        evidence: WorkflowExecutionStore,
        scheduling: _SchedulingCoordinator,
        dispatches: _DispatchStore,
        handoff: RuntimeHandoffService,
        visibility: Optional[_DispatchVisibility] = None,
    ) -> None:
        self._roles = roles
        self._workflows = workflows
        self._evidence = evidence
        self._scheduling = scheduling
        self._dispatches = dispatches
        self._handoff = handoff
        self._visibility = visibility
        self._builder = LaunchContractBuilder()
        self._validator = LaunchContractValidator()
        self._sessions = RuntimeSessionService()

    @staticmethod
    def dispatch_id_for(intent: WorkflowExecutionIntent) -> str:
        seed = f"{intent.project_id}|{intent.intent_id}|{intent.claim_id}"
        return "dispatch_" + hashlib.sha256(seed.encode()).hexdigest()[:24]

    def prepare(
        self,
        *,
        project_id: str,
        intent_id: str,
        expected_claim_id: str,
        plan: RoleExecutionPlan,
        compatibility: RuntimeCompatibility,
        repository_root: str,
        runtime: str,
        actor_id: str,
        timestamp: int,
        base_ref: Optional[str] = None,
        engine: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        environment: Tuple[Tuple[str, str], ...] = (),
    ) -> WorkflowDispatchOutcome:
        existing = self._dispatches.find_by_intent(project_id, intent_id)
        if existing is not None:
            self._validate_retry(
                existing, expected_claim_id=expected_claim_id, plan=plan,
                compatibility=compatibility, repository_root=repository_root,
                runtime=runtime, base_ref=base_ref, engine=engine,
                provider=provider, model=model, environment=environment,
            )
            self._publish(existing)
            self._finish_coordination(existing, timestamp=timestamp)
            return existing
        intent = self._require_claim(
            project_id, intent_id, expected_claim_id, timestamp
        )
        if actor_id.strip() != intent.claimed_by:
            raise WorkflowDispatchError("workflow dispatch actor must equal claimed_by")
        dispatch_id = self.dispatch_id_for(intent)

        try:
            self._validate_environment(environment)
            self._validate_authority(intent, plan)
            assignment = self._roles.get_assignment(project_id, intent.assignment_id)
            role = self._roles.get_role(project_id, intent.role_id)
            self._validate_launch_authority(
                assignment.metadata, repository_root=repository_root,
                runtime=runtime, base_ref=base_ref, engine=engine,
                provider=provider, model=model, environment=environment,
            )
            contract = self._builder.build(
                assignment,
                role,
                repository_root=repository_root,
                runtime=runtime,
                timestamp=timestamp,
                contract_id=plan.contract_id,
                base_ref=base_ref,
                engine=engine,
                provider=provider,
                model=model,
                environment=environment,
            )
            if (
                plan.project_id != contract.project_id
                or plan.assignment_id != contract.assignment_id
                or plan.role_id != contract.role_id
                or plan.agent_id != contract.agent_id
            ):
                raise WorkflowDispatchError("execution plan does not match launch contract")
            validation = self._validator.validate(contract, compatibility)
            if not validation.valid:
                reason = "; ".join(issue.message for issue in validation.errors)
                outcome = self._outcome(
                    intent, dispatch_id, actor_id, timestamp,
                    WorkflowDispatchStatus.REFUSED,
                    reason or "launch compatibility validation failed",
                    contract=contract, validation=validation,
                )
            else:
                receipt = self._handoff.dry_run(
                    contract, validation, requested_at=timestamp
                )
                if not receipt.accepted:
                    outcome = self._outcome(
                        intent, dispatch_id, actor_id, timestamp,
                        WorkflowDispatchStatus.REFUSED,
                        "; ".join(receipt.reasons),
                        contract=contract, validation=validation,
                    )
                else:
                    created = self._sessions.create(
                        contract, receipt, created_at=timestamp
                    )
                    ready = self._sessions.mark_ready(created, ready_at=timestamp)
                    outcome = self._outcome(
                        intent, dispatch_id, actor_id, timestamp,
                        WorkflowDispatchStatus.PREPARED,
                        "runtime admission prepared without starting execution",
                        contract=contract, validation=validation,
                        receipt=receipt, session=ready,
                    )
        except WorkflowDispatchError:
            raise
        except (KeyError, PermissionError, ValueError) as exc:
            raise WorkflowDispatchError(str(exc)) from exc

        persisted = self._dispatches.append(outcome)
        self._publish(persisted)
        self._finish_coordination(persisted, timestamp=timestamp)
        return persisted

    def reconcile_visibility(
        self, project_id: str, dispatch_id: str, *, timestamp: Optional[int] = None
    ) -> WorkflowDispatchOutcome:
        outcome = self._dispatches.get(project_id, dispatch_id)
        if outcome is None:
            raise WorkflowDispatchError("workflow dispatch outcome not found")
        self._publish(outcome)
        self._finish_coordination(
            outcome, timestamp=outcome.created_at if timestamp is None else timestamp
        )
        return outcome

    @staticmethod
    def _validate_environment(environment: Tuple[Tuple[str, str], ...]) -> None:
        if len(environment) > 32:
            raise WorkflowDispatchError("workflow dispatch environment exceeds limit")
        for key, value in environment:
            if len(key) > 128 or len(value) > 1024:
                raise WorkflowDispatchError("workflow dispatch environment value exceeds limit")
            lowered = f"{key}={value}".lower()
            if any(marker in lowered for marker in (
                "api_key=", "api-key=", "authorization: bearer ", "password=",
                "private key-----", "secret=", "token=",
            )):
                raise WorkflowDispatchError("workflow dispatch environment must not contain secrets")
            if value:
                raise WorkflowDispatchError(
                    "workflow dispatch environment values must not be persisted"
                )

    @staticmethod
    def _validate_launch_authority(
        metadata: object,
        *,
        repository_root: str,
        runtime: str,
        base_ref: Optional[str],
        engine: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        environment: Tuple[Tuple[str, str], ...],
    ) -> None:
        if not isinstance(metadata, dict):
            raise WorkflowDispatchError("durable launch authority metadata is required")
        expected_environment = metadata.get("environment", ())
        if isinstance(expected_environment, list):
            expected_environment = tuple(tuple(item) for item in expected_environment)
        actual = {
            "repository_root": repository_root.strip(),
            "runtime": runtime.strip(),
            "base_ref": base_ref.strip() if base_ref else None,
            "engine": engine.strip() if engine else None,
            "provider": provider.strip() if provider else None,
            "model": model.strip() if model else None,
            "environment": environment,
        }
        expected = {
            "repository_root": metadata.get("repository_root"),
            "runtime": metadata.get("runtime"),
            "base_ref": metadata.get("base_ref"),
            "engine": metadata.get("engine"),
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "environment": expected_environment,
        }
        if (
            not expected["repository_root"]
            or not expected["runtime"]
            or actual != expected
        ):
            raise WorkflowDispatchError(
                "launch preparation exceeds durable assignment authority"
            )

    def _validate_retry(
        self,
        existing: WorkflowDispatchOutcome,
        *,
        expected_claim_id: str,
        plan: RoleExecutionPlan,
        compatibility: RuntimeCompatibility,
        repository_root: str,
        runtime: str,
        base_ref: Optional[str],
        engine: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        environment: Tuple[Tuple[str, str], ...],
    ) -> None:
        self._validate_environment(environment)
        contract = existing.contract
        if (
            contract is None
            or existing.claim_id != expected_claim_id
            or existing.project_id != plan.project_id
            or existing.assignment_id != plan.assignment_id
            or existing.plan_id != plan.plan_id
            or existing.role_id != plan.role_id
            or existing.agent_id != plan.agent_id
            or contract.contract_id != plan.contract_id
            or contract.workspace.repository_root != repository_root.strip()
            or contract.workspace.base_ref != (base_ref.strip() if base_ref else None)
            or contract.environment.runtime != runtime.strip()
            or contract.environment.engine != (engine.strip() if engine else None)
            or contract.environment.provider != (provider.strip() if provider else None)
            or contract.environment.model != (model.strip() if model else None)
            or contract.environment.environment != environment
            or existing.validation != self._validator.validate(contract, compatibility)
        ):
            raise WorkflowDispatchError("workflow dispatch retry identity mismatch")

    def _require_claim(
        self, project_id: str, intent_id: str, expected_claim_id: str, timestamp: int
    ) -> WorkflowExecutionIntent:
        intent = self._scheduling.get(project_id, intent_id)
        if intent is None or intent.status != CoordinationStatus.CLAIMED:
            raise WorkflowDispatchError("current claimed scheduling intent is required")
        if intent.claim_id != expected_claim_id or not intent.claimed_by:
            raise WorkflowDispatchError("workflow dispatch claim identity mismatch")
        if intent.lease_expires_at is None or timestamp >= intent.lease_expires_at:
            raise WorkflowDispatchError("workflow dispatch claim lease has expired")
        if timestamp < intent.updated_at:
            raise WorkflowDispatchError("workflow dispatch timestamp predates claim")
        return intent

    def _validate_authority(
        self, intent: WorkflowExecutionIntent, plan: RoleExecutionPlan
    ) -> None:
        if (
            plan.project_id != intent.project_id
            or plan.assignment_id != intent.assignment_id
            or plan.plan_id != intent.plan_id
            or plan.role_id != intent.role_id
            or plan.agent_id != intent.agent_id
        ):
            raise WorkflowDispatchError("execution plan does not match scheduling intent")
        workflow = self._workflows.get(intent.project_id, intent.workflow_id)
        summary = self._evidence.get_summary(intent.project_id, intent.run_id)
        if workflow is None or summary is None:
            raise WorkflowDispatchError("durable workflow and execution evidence are required")
        stage = workflow.stages[workflow.current_stage]
        if (
            workflow.state != WorkflowState.ACTIVE
            or workflow.version != intent.workflow_version
            or stage.sequence != intent.stage_sequence
            or stage.assignment_id != plan.assignment_id
            or stage.plan_id != plan.plan_id
            or stage.role_id != plan.role_id
            or not workflow.authorizations
            or workflow.authorizations[-1] != intent.authorization
            or intent.authorization.disposition != AuthorizationDecision.APPROVED
            or intent.node_run_id != WorkflowExecutionEvidenceService.node_run_id_for(
                intent.run_id, plan.assignment_id, plan.plan_id
            )
        ):
            raise WorkflowDispatchError("current workflow authority does not match intent")
        authorization_events = tuple(
            event for event in self._evidence.events_for_run(intent.project_id, intent.run_id)
            if event.event_type == ExecutionEventType.AUTHORIZATION_GRANTED
            and event.authorization_id == intent.authorization_id
        )
        if len(authorization_events) != 1:
            raise WorkflowDispatchError("durable authorization-granted evidence is required")
        events = self._evidence.events_for_run(intent.project_id, intent.run_id)
        granted = authorization_events[0]
        request = events[-2] if len(events) >= 2 else None
        expected_request_type = (
            ExecutionEventType.RETRY_REQUESTED
            if intent.decision == WorkflowDecision.RETRY
            else ExecutionEventType.TRANSITION_REQUESTED
        )
        if (
            len(events) != summary.event_count
            or events[-1] != granted
            or summary.status != WorkflowRunStatus.RUNNING
            or summary.last_authorized_decision != intent.decision
            or summary.last_event_id != granted.event_id
            or summary.workflow_id != intent.workflow_id
            or summary.workflow_version != workflow.version
            or granted.project_id != intent.project_id
            or granted.workflow_id != intent.workflow_id
            or granted.run_id != intent.run_id
            or granted.workflow_version != workflow.version
            or granted.decision != intent.authorization.decision
            or granted.to_role_id != intent.authorization.to_role_id
            or granted.actor_id != intent.authorization.actor
            or granted.actor_source != EvidenceActorSource.HUMAN
            or granted.timestamp != intent.authorization.timestamp
            or granted.reason != intent.authorization.reason
            or granted.correlation_id != intent.run_id
            or request is None
            or request.event_type != expected_request_type
            or granted.causation_id != request.event_id
            or request.project_id != intent.project_id
            or request.workflow_id != intent.workflow_id
            or request.run_id != intent.run_id
            or request.workflow_version != intent.authorization.expected_version
            or request.decision != intent.authorization.decision
            or request.to_role_id != intent.authorization.to_role_id
            or request.actor_source != EvidenceActorSource.ORCHESTRATOR
            or request.correlation_id != intent.run_id
        ):
            raise WorkflowDispatchError("authorization execution provenance mismatch")

    def _outcome(
        self,
        intent: WorkflowExecutionIntent,
        dispatch_id: str,
        actor_id: str,
        timestamp: int,
        status: WorkflowDispatchStatus,
        reason: str,
        *,
        contract: Optional[LaunchContract] = None,
        validation: Optional[LaunchValidationResult] = None,
        receipt: Optional[RuntimeHandoffReceipt] = None,
        session: Optional[RuntimeSession] = None,
    ) -> WorkflowDispatchOutcome:
        refs = tuple(
            value for value in (
                intent.intent_id,
                intent.authorization_id,
                contract.contract_id if contract else None,
                receipt.receipt_id if receipt else None,
                session.session_id if session else None,
            ) if value is not None
        )
        return WorkflowDispatchOutcome(
            dispatch_id=dispatch_id, status=status,
            project_id=intent.project_id, workflow_id=intent.workflow_id,
            run_id=intent.run_id, intent_id=intent.intent_id,
            intent_version=intent.version, intent_fingerprint=intent.fingerprint,
            claim_id=intent.claim_id or "", claimed_by=intent.claimed_by or "",
            assignment_id=intent.assignment_id, plan_id=intent.plan_id,
            role_id=intent.role_id, agent_id=intent.agent_id,
            authorization_id=intent.authorization_id, actor_id=actor_id,
            correlation_id=intent.run_id, causation_id=intent.fingerprint,
            created_at=timestamp, reason=reason,
            contract=contract, validation=validation, receipt=receipt,
            session=session, evidence_refs=refs,
        )

    def _publish(self, outcome: WorkflowDispatchOutcome) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(outcome)
        except Exception as exc:
            raise WorkflowDispatchVisibilityError(outcome) from exc

    def _finish_coordination(
        self, outcome: WorkflowDispatchOutcome, *, timestamp: int
    ) -> None:
        current = self._scheduling.get(outcome.project_id, outcome.intent_id)
        if current is None or current.status == CoordinationStatus.COMPLETED:
            if (
                current is None
                or outcome.status != WorkflowDispatchStatus.PREPARED
                or outcome.dispatch_id not in current.evidence_refs
                or current.actor_id != outcome.actor_id
                or current.reason != outcome.reason
            ):
                raise WorkflowDispatchError("dispatch completion coordination mismatch")
            return
        if current.status == CoordinationStatus.REFUSED:
            if (
                outcome.status != WorkflowDispatchStatus.REFUSED
                or current.actor_id != outcome.actor_id
                or current.reason != outcome.reason
            ):
                raise WorkflowDispatchError("dispatch refusal coordination mismatch")
            return
        if current.status != CoordinationStatus.CLAIMED or current.claim_id != outcome.claim_id:
            raise WorkflowDispatchError("scheduling claim changed before dispatch completion")
        refs = outcome.evidence_refs + (outcome.dispatch_id,)
        if outcome.status == WorkflowDispatchStatus.PREPARED:
            self._scheduling.complete(
                outcome.project_id, outcome.intent_id,
                actor_id=outcome.actor_id,
                timestamp=timestamp, reason=outcome.reason,
                evidence_refs=refs, expected_claim_id=outcome.claim_id,
            )
        else:
            self._scheduling.refuse(
                outcome.project_id, outcome.intent_id,
                actor_id=outcome.actor_id,
                timestamp=timestamp, reason=outcome.reason,
                expected_claim_id=outcome.claim_id,
            )
