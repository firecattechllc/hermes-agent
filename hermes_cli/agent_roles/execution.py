"""Immutable execution results, bounded evidence, and governed retry decisions."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.agent_roles.execution_planning import ExecutionAction, RoleExecutionPlan
from hermes_cli.agent_roles.launch import LaunchContract
from hermes_cli.agent_roles.runtime_handoff import RuntimeHandoffReceipt
from hermes_cli.agent_roles.runtime_session import (
    RuntimeSession,
    RuntimeSessionService,
    RuntimeSessionState,
)


EXECUTION_RESULT_SCHEMA_VERSION = 1
MAX_EVIDENCE_RECORDS = 100


class ExecutionOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"


class FailureCategory(str, Enum):
    NONE = "none"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DEPENDENCY = "dependency"
    ENVIRONMENT = "environment"
    POLICY = "policy"
    APPROVAL = "approval"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PolicyDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    NOT_APPLICABLE = "not_applicable"


class ExecutionEvidence(BaseModel):
    """One bounded, secret-free summary of an attempted action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(..., min_length=1, max_length=128)
    evidence_type: str = Field(..., min_length=1, max_length=128)
    action: ExecutionAction
    attempted: str = Field(..., min_length=1, max_length=1024)
    output_summary: str = Field(..., min_length=1, max_length=4096)
    timestamp: int = Field(..., ge=0)
    successful: bool
    policy_decision: PolicyDecision = PolicyDecision.NOT_APPLICABLE
    reason: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    resulting_state: Optional[str] = Field(default=None, min_length=1, max_length=64)

    @field_validator(
        "evidence_type", "attempted", "output_summary", "reason", "resulting_state"
    )
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
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
            raise ValueError("execution evidence must not contain secrets")
        return value

    @model_validator(mode="after")
    def _validate_decision(self) -> "ExecutionEvidence":
        if self.policy_decision == PolicyDecision.DENIED and not self.reason:
            raise ValueError("denied evidence requires a reason")
        return self


class RetryDecision(BaseModel):
    """Governed retry representation; never an instruction to auto-run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    category: FailureCategory
    reason: str = Field(..., min_length=1, max_length=1024)
    requires_approval: bool = True
    automatic: bool = False

    @model_validator(mode="after")
    def _never_automatic(self) -> "RetryDecision":
        if self.automatic:
            raise ValueError("governed retries cannot execute automatically")
        if self.eligible and not self.requires_approval:
            raise ValueError("eligible retry must require approval")
        return self


class ExecutionResult(BaseModel):
    """Immutable terminal record connecting every execution boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = EXECUTION_RESULT_SCHEMA_VERSION
    result_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    contract_id: str = Field(..., min_length=1, max_length=128)
    receipt_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    outcome: ExecutionOutcome
    failure_category: FailureCategory = FailureCategory.NONE
    summary: str = Field(..., min_length=1, max_length=8192)
    blocking_reasons: Tuple[str, ...] = Field(default_factory=tuple)
    evidence: Tuple[ExecutionEvidence, ...] = Field(
        default_factory=tuple, max_length=MAX_EVIDENCE_RECORDS
    )
    retry: RetryDecision
    started_at: int = Field(..., ge=0)
    completed_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_result(self) -> "ExecutionResult":
        if self.schema_version != EXECUTION_RESULT_SCHEMA_VERSION:
            raise ValueError("unsupported execution result schema version")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.outcome == ExecutionOutcome.SUCCEEDED:
            if self.failure_category != FailureCategory.NONE or self.retry.eligible:
                raise ValueError("successful result cannot report failure or retry")
        elif self.failure_category == FailureCategory.NONE:
            raise ValueError("non-success result requires a failure category")
        if (
            self.outcome in {ExecutionOutcome.BLOCKED, ExecutionOutcome.POLICY_DENIED}
            and not self.blocking_reasons
        ):
            raise ValueError("blocked or denied result requires blocking reasons")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("execution evidence IDs must be unique")
        if any(
            item.timestamp < self.started_at or item.timestamp > self.completed_at
            for item in self.evidence
        ):
            raise ValueError("execution evidence timestamp is outside session bounds")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"result_id"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class GovernedExecutionService:
    """Validate associations and produce one terminal result without auto-retry."""

    _STATE_BY_OUTCOME = {
        ExecutionOutcome.SUCCEEDED: RuntimeSessionState.SUCCEEDED,
        ExecutionOutcome.FAILED: RuntimeSessionState.FAILED,
        ExecutionOutcome.BLOCKED: RuntimeSessionState.BLOCKED,
        ExecutionOutcome.CANCELLED: RuntimeSessionState.CANCELLED,
        ExecutionOutcome.POLICY_DENIED: RuntimeSessionState.POLICY_DENIED,
    }

    def __init__(self, session_service: Optional[RuntimeSessionService] = None) -> None:
        self._sessions = session_service or RuntimeSessionService()

    def start(
        self, session: RuntimeSession, plan: RoleExecutionPlan, *, started_at: int
    ) -> RuntimeSession:
        self._validate_plan_session(plan, session)
        return self._sessions.start(session, started_at=started_at)

    def complete(
        self,
        session: RuntimeSession,
        plan: RoleExecutionPlan,
        contract: LaunchContract,
        receipt: RuntimeHandoffReceipt,
        *,
        outcome: ExecutionOutcome,
        summary: str,
        evidence: Tuple[ExecutionEvidence, ...],
        completed_at: int,
        failure_category: FailureCategory = FailureCategory.NONE,
        blocking_reasons: Tuple[str, ...] = (),
        approvals: Tuple[str, ...] = (),
    ) -> tuple[RuntimeSession, ExecutionResult]:
        self._validate_associations(session, plan, contract, receipt)
        if session.state != RuntimeSessionState.RUNNING:
            raise ValueError("only running sessions can produce execution results")
        if any(item.action not in plan.allowed_actions for item in evidence):
            raise PermissionError("execution evidence contains unauthorized action")
        if plan.role_id == "reviewer" and any(
            item.action == ExecutionAction.MODIFY_IMPLEMENTATION for item in evidence
        ):
            raise PermissionError("reviewer cannot modify implementation")
        if plan.role_id == "security" and any(
            item.policy_decision == PolicyDecision.DENIED for item in evidence
        ):
            outcome = ExecutionOutcome.POLICY_DENIED
            failure_category = FailureCategory.POLICY
            blocking_reasons = blocking_reasons or ("security policy denied promotion",)
        if plan.role_id == "release" and outcome == ExecutionOutcome.SUCCEEDED:
            required = {
                "human_approval",
                "review",
                "verification",
                "security",
                "documentation",
            }
            missing = sorted(required.difference(approvals))
            if missing:
                outcome = ExecutionOutcome.POLICY_DENIED
                failure_category = FailureCategory.APPROVAL
                blocking_reasons = (
                    "missing release requirements: " + ", ".join(missing),
                )

        if outcome == ExecutionOutcome.SUCCEEDED:
            required_evidence = {
                evidence_type
                for step in plan.steps
                for evidence_type in step.required_evidence
            }
            supplied_evidence = {item.evidence_type for item in evidence}
            missing_evidence = sorted(required_evidence - supplied_evidence)
            if missing_evidence:
                raise ValueError(
                    "successful execution missing required evidence: "
                    + ", ".join(missing_evidence)
                )

        retry = self._retry_decision(outcome, failure_category)
        result_seed = json.dumps(
            {
                "session_id": session.session_id,
                "plan_id": plan.plan_id,
                "outcome": outcome.value,
                "failure_category": failure_category.value,
                "summary": summary,
                "blocking_reasons": blocking_reasons,
                "evidence": [item.model_dump(mode="json") for item in evidence],
                "completed_at": completed_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        result = ExecutionResult(
            result_id=f"execution_{hashlib.sha256(result_seed.encode()).hexdigest()[:24]}",
            project_id=session.project_id,
            assignment_id=session.assignment_id,
            contract_id=session.contract_id,
            receipt_id=session.receipt_id,
            session_id=session.session_id,
            plan_id=plan.plan_id,
            role_id=session.role_id,
            agent_id=session.agent_id,
            outcome=outcome,
            failure_category=failure_category,
            summary=summary,
            blocking_reasons=blocking_reasons,
            evidence=evidence,
            retry=retry,
            started_at=session.events[2].timestamp,
            completed_at=completed_at,
        )
        terminal = self._sessions.finish(
            session,
            state=self._STATE_BY_OUTCOME[outcome],
            finished_at=completed_at,
            reason=summary,
        )
        return terminal, result

    @staticmethod
    def _retry_decision(
        outcome: ExecutionOutcome, category: FailureCategory
    ) -> RetryDecision:
        eligible = outcome == ExecutionOutcome.FAILED and category in {
            FailureCategory.EXECUTION,
            FailureCategory.ENVIRONMENT,
            FailureCategory.UNKNOWN,
        }
        return RetryDecision(
            eligible=eligible,
            category=category,
            reason=(
                "retry may be requested with governance approval"
                if eligible
                else "outcome is not eligible for governed retry"
            ),
            requires_approval=True,
            automatic=False,
        )

    @staticmethod
    def _validate_plan_session(
        plan: RoleExecutionPlan, session: RuntimeSession
    ) -> None:
        values = (
            plan.project_id == session.project_id,
            plan.assignment_id == session.assignment_id,
            plan.contract_id == session.contract_id,
            plan.role_id == session.role_id,
            plan.agent_id == session.agent_id,
        )
        if not all(values):
            raise ValueError("execution plan does not match runtime session")

    @classmethod
    def _validate_associations(
        cls,
        session: RuntimeSession,
        plan: RoleExecutionPlan,
        contract: LaunchContract,
        receipt: RuntimeHandoffReceipt,
    ) -> None:
        cls._validate_plan_session(plan, session)
        if (
            contract.contract_id != session.contract_id
            or receipt.receipt_id != session.receipt_id
        ):
            raise ValueError("execution artifacts do not match runtime session")
        if (
            contract.project_id != session.project_id
            or contract.assignment_id != session.assignment_id
            or contract.role_id != session.role_id
            or contract.agent_id != session.agent_id
        ):
            raise ValueError("launch contract does not match runtime session")
        if receipt.contract_id != contract.contract_id:
            raise ValueError("handoff receipt does not match launch contract")
