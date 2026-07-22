"""Domain models for Hermes Specialized Agent Roles.

Agent Roles define the governed identities, capabilities, policies,
assignments, handoffs, and results used by later orchestration layers.

This subsystem does not execute work, schedule work, claim backlog items,
or launch agents. It defines the durable vocabulary required for those
operations.

All timestamps are UTC Unix integers. Unknown schema versions fail closed.
"""

from __future__ import annotations

import secrets
import time
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ── Schema versioning ────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


def _utc_now() -> int:
    """Return the current UTC Unix timestamp as an integer."""
    return int(time.time())


def _validate_schema(version: int) -> int:
    """Reject unknown model schema versions."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )

    return version


def _new_identifier(prefix: str) -> str:
    """Create a locally unique opaque domain identifier."""
    normalised_prefix = prefix.strip()

    if not normalised_prefix:
        raise ValueError("identifier prefix must not be empty")

    return f"{normalised_prefix}_{secrets.token_hex(8)}"


def new_role_id() -> str:
    """Create a custom agent-role identifier."""
    return _new_identifier("role")


def new_assignment_id() -> str:
    """Create an assignment identifier."""
    return _new_identifier("assign")


def new_handoff_id() -> str:
    """Create an assignment-handoff identifier."""
    return _new_identifier("handoff")


def new_result_id() -> str:
    """Create an assignment-result identifier."""
    return _new_identifier("result")


# ── Enums ────────────────────────────────────────────────────────────────────

class BuiltinRole(str, Enum):
    """Stable identifiers for Hermes built-in engineering roles."""

    PLANNER = "planner"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    SECURITY = "security"
    DOCUMENTATION = "documentation"
    RELEASE = "release"


class AssignmentStatus(str, Enum):
    """Governed lifecycle states for one role assignment."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDED_OFF = "handed_off"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AssignmentOutcome(str, Enum):
    """Final or intermediate outcome reported by an assignment."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


class HandoffReason(str, Enum):
    """Reason responsibility moves between governed roles."""

    STAGE_COMPLETE = "stage_complete"
    CAPABILITY_REQUIRED = "capability_required"
    POLICY_REQUIRED = "policy_required"
    REVIEW_REQUIRED = "review_required"
    VERIFICATION_REQUIRED = "verification_required"
    SECURITY_REVIEW_REQUIRED = "security_review_required"
    RELEASE_APPROVAL_REQUIRED = "release_approval_required"
    BLOCKED = "blocked"
    FAILURE = "failure"
    HUMAN_DIRECTION = "human_direction"
    OTHER = "other"


# ── Validation helpers ───────────────────────────────────────────────────────

def _normalise_text_tuple(values: Tuple[str, ...]) -> Tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        normalised = value.strip()

        if normalised and normalised not in seen:
            seen.add(normalised)
            output.append(normalised)

    return tuple(output)


def _validate_timestamp(value: int, field_name: str) -> int:
    if value < 0:
        raise ValueError(
            f"{field_name} must be a non-negative Unix timestamp"
        )

    return value


# ── Capabilities and policy ──────────────────────────────────────────────────

class RoleCapability(BaseModel):
    """One named capability advertised by an agent role."""

    model_config = ConfigDict(frozen=True)

    capability_id: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=2048)
    required: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("capability_id", "description")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalised = value.strip()

        if not normalised:
            raise ValueError("text fields must not be blank")

        return normalised


class RolePolicy(BaseModel):
    """Static governance constraints attached to one role."""

    model_config = ConfigDict(frozen=True)

    max_concurrent_assignments: int = Field(default=1, ge=1, le=100)
    requires_human_approval: bool = False
    may_delegate: bool = False
    may_modify_repository: bool = False
    allowed_risk_levels: Tuple[str, ...] = Field(
        default_factory=lambda: ("low", "medium")
    )
    allowed_paths: Tuple[str, ...] = Field(default_factory=tuple)
    denied_paths: Tuple[str, ...] = Field(default_factory=tuple)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "allowed_risk_levels",
        "allowed_paths",
        "denied_paths",
    )
    @classmethod
    def _normalise_lists(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(values)

    @model_validator(mode="after")
    def _validate_path_policy(self) -> "RolePolicy":
        overlap = set(self.allowed_paths).intersection(self.denied_paths)

        if overlap:
            raise ValueError(
                "paths may not appear in both allowed_paths "
                "and denied_paths"
            )

        return self


class AgentRole(BaseModel):
    """Governed definition of one specialized engineering role."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    role_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=4096)
    capabilities: Tuple[RoleCapability, ...] = Field(default_factory=tuple)
    policy: RolePolicy = Field(default_factory=RolePolicy)
    built_in: bool = False
    active: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator("role_id", "name", "description")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        normalised = value.strip()

        if not normalised:
            raise ValueError("role text fields must not be blank")

        return normalised

    @field_validator("capabilities")
    @classmethod
    def _unique_capabilities(
        cls,
        values: Tuple[RoleCapability, ...],
    ) -> Tuple[RoleCapability, ...]:
        seen: set[str] = set()

        for capability in values:
            if capability.capability_id in seen:
                raise ValueError(
                    "role capabilities must have unique capability IDs"
                )

            seen.add(capability.capability_id)

        return values


# ── Assignment models ────────────────────────────────────────────────────────

class Assignment(BaseModel):
    """Immutable snapshot of responsibility for one unit of work."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=256)
    role_id: str = Field(..., min_length=1, max_length=128)
    backlog_item_id: Optional[str] = Field(None, max_length=128)
    assigned_agent_id: Optional[str] = Field(None, max_length=256)
    status: AssignmentStatus = AssignmentStatus.PENDING
    required_capabilities: Tuple[str, ...] = Field(default_factory=tuple)
    instructions: Optional[str] = Field(None, max_length=16384)
    created_at: int = Field(default_factory=_utc_now)
    updated_at: int = Field(default_factory=_utc_now)
    created_by: Optional[str] = Field(None, max_length=256)
    correlation_id: Optional[str] = Field(None, max_length=256)
    causation_id: Optional[str] = Field(None, max_length=256)
    version: int = Field(default=1, ge=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator(
        "assignment_id",
        "project_id",
        "role_id",
        "backlog_item_id",
        "assigned_agent_id",
        "instructions",
        "created_by",
        "correlation_id",
        "causation_id",
    )
    @classmethod
    def _strip_optional_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("assignment text fields must not be blank")

        return normalised

    @field_validator("required_capabilities")
    @classmethod
    def _normalise_capabilities(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(values)

    @field_validator("created_at")
    @classmethod
    def _check_created_at(cls, value: int) -> int:
        return _validate_timestamp(value, "created_at")

    @field_validator("updated_at")
    @classmethod
    def _check_updated_at(cls, value: int) -> int:
        return _validate_timestamp(value, "updated_at")

    @model_validator(mode="after")
    def _validate_assignment(self) -> "Assignment":
        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at must not be earlier than created_at"
            )

        statuses_requiring_agent = {
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.ACCEPTED,
            AssignmentStatus.ACTIVE,
            AssignmentStatus.HANDOFF_REQUESTED,
        }

        if (
            self.status in statuses_requiring_agent
            and self.assigned_agent_id is None
        ):
            raise ValueError(
                f"{self.status.value} assignments require "
                "assigned_agent_id"
            )

        return self


class AssignmentHandoff(BaseModel):
    """Immutable record describing a responsibility handoff."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    handoff_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=256)
    from_role_id: str = Field(..., min_length=1, max_length=128)
    to_role_id: str = Field(..., min_length=1, max_length=128)
    reason: HandoffReason
    summary: str = Field(..., min_length=1, max_length=4096)
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)
    requested_by: Optional[str] = Field(None, max_length=256)
    timestamp: int = Field(default_factory=_utc_now)
    correlation_id: Optional[str] = Field(None, max_length=256)
    causation_id: Optional[str] = Field(None, max_length=256)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator(
        "handoff_id",
        "assignment_id",
        "project_id",
        "from_role_id",
        "to_role_id",
        "summary",
        "requested_by",
        "correlation_id",
        "causation_id",
    )
    @classmethod
    def _strip_optional_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("handoff text fields must not be blank")

        return normalised

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_evidence_refs(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(values)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: int) -> int:
        return _validate_timestamp(value, "timestamp")

    @model_validator(mode="after")
    def _validate_roles(self) -> "AssignmentHandoff":
        if self.from_role_id == self.to_role_id:
            raise ValueError(
                "handoff must transfer responsibility "
                "to a different role"
            )

        return self


class AssignmentResult(BaseModel):
    """Immutable evidence-bearing result produced by an assignment."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    result_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=256)
    role_id: str = Field(..., min_length=1, max_length=128)
    outcome: AssignmentOutcome
    summary: str = Field(..., min_length=1, max_length=8192)
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)
    produced_by: Optional[str] = Field(None, max_length=256)
    completed_at: int = Field(default_factory=_utc_now)
    correlation_id: Optional[str] = Field(None, max_length=256)
    causation_id: Optional[str] = Field(None, max_length=256)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, value: int) -> int:
        return _validate_schema(value)

    @field_validator(
        "result_id",
        "assignment_id",
        "project_id",
        "role_id",
        "summary",
        "produced_by",
        "correlation_id",
        "causation_id",
    )
    @classmethod
    def _strip_optional_text(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        normalised = value.strip()

        if not normalised:
            raise ValueError("result text fields must not be blank")

        return normalised

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_evidence_refs(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(values)

    @field_validator("completed_at")
    @classmethod
    def _check_completed_at(cls, value: int) -> int:
        return _validate_timestamp(value, "completed_at")


# ── Built-in role catalog ────────────────────────────────────────────────────

def builtin_agent_roles() -> Tuple[AgentRole, ...]:
    """Return the stable built-in Hermes engineering role catalog."""
    definitions = (
        (
            BuiltinRole.PLANNER,
            "Planner",
            "Converts governed work into an executable engineering plan.",
            (
                RoleCapability(
                    capability_id="planning",
                    description="Create bounded implementation plans.",
                ),
                RoleCapability(
                    capability_id="dependency-analysis",
                    description="Identify dependencies and execution order.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=4,
                may_delegate=True,
            ),
        ),
        (
            BuiltinRole.BUILDER,
            "Builder",
            "Implements approved changes inside the permitted scope.",
            (
                RoleCapability(
                    capability_id="code-change",
                    description="Modify implementation files.",
                ),
                RoleCapability(
                    capability_id="focused-testing",
                    description="Run focused tests for changed behavior.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=2,
                may_modify_repository=True,
            ),
        ),
        (
            BuiltinRole.REVIEWER,
            "Reviewer",
            "Independently evaluates correctness, scope, and maintainability.",
            (
                RoleCapability(
                    capability_id="code-review",
                    description="Review implementation and evidence.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=4,
                requires_human_approval=False,
            ),
        ),
        (
            BuiltinRole.TESTER,
            "Tester",
            "Verifies behavior and produces reproducible test evidence.",
            (
                RoleCapability(
                    capability_id="test-execution",
                    description="Execute focused and regression tests.",
                ),
                RoleCapability(
                    capability_id="failure-analysis",
                    description="Diagnose reproducible test failures.",
                ),
            ),
            RolePolicy(max_concurrent_assignments=4),
        ),
        (
            BuiltinRole.SECURITY,
            "Security",
            "Evaluates security-sensitive changes and policy compliance.",
            (
                RoleCapability(
                    capability_id="security-review",
                    description="Review threats and security boundaries.",
                ),
                RoleCapability(
                    capability_id="policy-validation",
                    description="Validate security policy compliance.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=2,
                requires_human_approval=True,
                allowed_risk_levels=(
                    "low",
                    "medium",
                    "high",
                    "critical",
                ),
            ),
        ),
        (
            BuiltinRole.DOCUMENTATION,
            "Documentation",
            "Maintains user-facing and engineering documentation.",
            (
                RoleCapability(
                    capability_id="documentation",
                    description="Create and update durable documentation.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=4,
                may_modify_repository=True,
            ),
        ),
        (
            BuiltinRole.RELEASE,
            "Release",
            "Validates promotion evidence and prepares governed releases.",
            (
                RoleCapability(
                    capability_id="release-readiness",
                    description="Evaluate readiness for promotion.",
                ),
                RoleCapability(
                    capability_id="promotion-evidence",
                    description="Validate required promotion evidence.",
                ),
            ),
            RolePolicy(
                max_concurrent_assignments=1,
                requires_human_approval=True,
                allowed_risk_levels=(
                    "low",
                    "medium",
                    "high",
                    "critical",
                ),
            ),
        ),
    )

    return tuple(
        AgentRole(
            role_id=role.value,
            name=name,
            description=description,
            capabilities=capabilities,
            policy=policy,
            built_in=True,
        )
        for role, name, description, capabilities, policy in definitions
    )
