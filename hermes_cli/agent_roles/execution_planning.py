"""Deterministic, governance-first plans for specialized agent execution."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.agent_roles.launch import LaunchContract
from hermes_cli.agent_roles.models import AgentRole, Assignment, BuiltinRole


EXECUTION_PLAN_SCHEMA_VERSION = 1


class ExecutionAction(str, Enum):
    PLAN = "plan"
    MODIFY_IMPLEMENTATION = "modify_implementation"
    REVIEW = "review"
    VERIFY = "verify"
    SECURITY_ASSESS = "security_assess"
    MODIFY_DOCUMENTATION = "modify_documentation"
    ASSESS_RELEASE = "assess_release"
    PROMOTE = "promote"


class ExecutionPlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(..., ge=1)
    action: ExecutionAction
    responsibility: str = Field(..., min_length=1, max_length=512)
    modifies_repository: bool = False
    required_evidence: Tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("responsibility")
    @classmethod
    def _strip_responsibility(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("responsibility must not be blank")
        return value


class RoleExecutionPlan(BaseModel):
    """Immutable plan whose authority is derived from one registered role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = EXECUTION_PLAN_SCHEMA_VERSION
    plan_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    contract_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    responsibilities: Tuple[str, ...] = Field(min_length=1)
    allowed_actions: Tuple[ExecutionAction, ...] = Field(min_length=1)
    allowed_next_roles: Tuple[str, ...]
    steps: Tuple[ExecutionPlanStep, ...] = Field(min_length=1)
    created_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_plan(self) -> "RoleExecutionPlan":
        if self.schema_version != EXECUTION_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported execution plan schema version")
        if tuple(step.sequence for step in self.steps) != tuple(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("execution plan steps must be contiguous")
        allowed = set(self.allowed_actions)
        if any(step.action not in allowed for step in self.steps):
            raise ValueError("execution step exceeds role authority")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"plan_id"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


_ROLE_SPEC: Mapping[
    BuiltinRole,
    tuple[
        tuple[str, ...],
        tuple[ExecutionAction, ...],
        tuple[BuiltinRole, ...],
        tuple[str, ...],
    ],
] = {
    BuiltinRole.PLANNER: (
        ("Bound scope and dependencies", "Produce an ordered execution plan"),
        (ExecutionAction.PLAN,),
        (BuiltinRole.BUILDER,),
        ("plan",),
    ),
    BuiltinRole.BUILDER: (
        ("Implement only authorized paths", "Record focused validation"),
        (ExecutionAction.MODIFY_IMPLEMENTATION, ExecutionAction.VERIFY),
        (BuiltinRole.REVIEWER, BuiltinRole.TESTER),
        ("change_summary", "focused_test"),
    ),
    BuiltinRole.REVIEWER: (
        ("Review correctness and scope without modifying implementation",),
        (ExecutionAction.REVIEW,),
        (BuiltinRole.BUILDER, BuiltinRole.TESTER),
        ("review_findings",),
    ),
    BuiltinRole.TESTER: (
        ("Execute reproducible verification", "Record bounded test evidence"),
        (ExecutionAction.VERIFY,),
        (BuiltinRole.BUILDER, BuiltinRole.SECURITY, BuiltinRole.DOCUMENTATION),
        ("test_result",),
    ),
    BuiltinRole.SECURITY: (
        ("Assess security policy", "Block unsafe promotion"),
        (ExecutionAction.SECURITY_ASSESS,),
        (BuiltinRole.BUILDER, BuiltinRole.DOCUMENTATION, BuiltinRole.RELEASE),
        ("security_decision",),
    ),
    BuiltinRole.DOCUMENTATION: (
        ("Update authorized documentation", "Record documentation completion"),
        (ExecutionAction.MODIFY_DOCUMENTATION,),
        (BuiltinRole.RELEASE,),
        ("documentation_change",),
    ),
    BuiltinRole.RELEASE: (
        ("Validate approvals and evidence", "Promote only when governance permits"),
        (ExecutionAction.ASSESS_RELEASE, ExecutionAction.PROMOTE),
        (),
        ("approval", "verification", "security_decision", "documentation_change"),
    ),
}


class RoleExecutionPlanner:
    """Build deterministic plans and reject authority expansion."""

    def create(
        self,
        assignment: Assignment,
        role: AgentRole,
        contract: LaunchContract,
        *,
        created_at: int,
    ) -> RoleExecutionPlan:
        if (
            assignment.project_id != contract.project_id
            or assignment.assignment_id != contract.assignment_id
        ):
            raise ValueError("assignment does not match launch contract")
        if assignment.role_id != role.role_id or contract.role_id != role.role_id:
            raise ValueError("role does not match assignment and launch contract")
        if assignment.assigned_agent_id != contract.agent_id:
            raise ValueError("assigned agent does not match launch contract")
        try:
            builtin = BuiltinRole(role.role_id)
            responsibilities, actions, transitions, evidence = _ROLE_SPEC[builtin]
        except (ValueError, KeyError) as exc:
            raise ValueError("no governed execution specification for role") from exc

        modification_actions = {
            ExecutionAction.MODIFY_IMPLEMENTATION,
            ExecutionAction.MODIFY_DOCUMENTATION,
        }
        modifies = any(action in modification_actions for action in actions)
        if contract.policy.modifies_repository != modifies:
            raise ValueError("launch modification authority does not match role plan")
        if modifies and not role.policy.may_modify_repository:
            raise ValueError("role policy forbids repository modification")

        steps = tuple(
            ExecutionPlanStep(
                sequence=index,
                action=action,
                responsibility=responsibilities[
                    min(index - 1, len(responsibilities) - 1)
                ],
                modifies_repository=action in modification_actions,
                required_evidence=evidence,
            )
            for index, action in enumerate(actions, 1)
        )
        seed = "|".join((
            assignment.project_id,
            assignment.assignment_id,
            contract.contract_id,
            role.role_id,
            contract.agent_id,
        ))
        plan_id = f"plan_{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
        return RoleExecutionPlan(
            plan_id=plan_id,
            project_id=assignment.project_id,
            assignment_id=assignment.assignment_id,
            contract_id=contract.contract_id,
            role_id=role.role_id,
            agent_id=contract.agent_id,
            responsibilities=responsibilities,
            allowed_actions=actions,
            allowed_next_roles=tuple(item.value for item in transitions),
            steps=steps,
            created_at=created_at,
        )

    @staticmethod
    def require_action(plan: RoleExecutionPlan, action: ExecutionAction) -> None:
        if action not in plan.allowed_actions:
            raise PermissionError(
                f"role {plan.role_id} is not authorized for action {action.value}"
            )

    @staticmethod
    def require_transition(plan: RoleExecutionPlan, to_role_id: str) -> None:
        if to_role_id not in plan.allowed_next_roles:
            raise PermissionError(
                f"role {plan.role_id} cannot hand off to {to_role_id}"
            )
