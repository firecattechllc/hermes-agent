"""Governed service layer for Hermes Specialized Agent Roles.

This initial service slice manages role registration and lookup only. It
bootstraps the immutable built-in role catalog into a project journal and
allows governed custom-role registration.

Assignment lifecycle transitions, agent execution, scheduling, CLI wiring,
and Mission Control projections are intentionally outside this slice.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .models import (
    AgentRole,
    Assignment,
    AssignmentHandoff,
    AssignmentOutcome,
    AssignmentResult,
    AssignmentStatus,
    BuiltinRole,
    HandoffReason,
    RoleCapability,
    builtin_agent_roles,
    new_assignment_id,
    new_handoff_id,
    new_result_id,
)
from .store import (
    AgentRoleProjectState,
    AgentRoleStore,
    DuplicateRecordError,
)


class AgentRoleServiceError(RuntimeError):
    """Base error raised by the specialized-agent-role service."""


class RoleNotFoundError(AgentRoleServiceError):
    """Raised when a requested role is not registered."""


class RoleAlreadyRegisteredError(AgentRoleServiceError):
    """Raised when a role identifier is already registered."""


class InvalidRoleRegistrationError(AgentRoleServiceError):
    """Raised when a custom role violates registration policy."""


class BuiltinRoleConflictError(AgentRoleServiceError):
    """Raised when stored built-in role data differs from the catalog."""


class AssignmentNotFoundError(AgentRoleServiceError):
    """Raised when a requested assignment does not exist."""


class InvalidAssignmentError(AgentRoleServiceError):
    """Raised when an assignment violates role or capability policy."""


class InvalidAssignmentTransitionError(AgentRoleServiceError):
    """Raised when an assignment lifecycle transition is illegal."""


class AssignmentAgentMismatchError(AgentRoleServiceError):
    """Raised when the wrong agent attempts an assignment operation."""


class AgentRoleService:
    """Governed role-catalog operations for one append-only store."""

    def __init__(self, store: AgentRoleStore) -> None:
        self.store = store

    def get_project_state(
        self,
        project_id: str,
    ) -> AgentRoleProjectState:
        """Return the verified reconstructed state for one project."""
        return self.store.replay(project_id)

    def list_roles(
        self,
        project_id: str,
        *,
        active_only: bool = False,
    ) -> Tuple[AgentRole, ...]:
        """Return roles ordered deterministically by role identifier."""
        state = self.store.replay(project_id)
        roles = tuple(
            sorted(
                state.roles.values(),
                key=lambda role: role.role_id,
            )
        )

        if not active_only:
            return roles

        return tuple(role for role in roles if role.active)

    def get_role(
        self,
        project_id: str,
        role_id: str,
    ) -> AgentRole:
        """Return one registered role or fail closed."""
        role = self.store.replay(project_id).get_role(role_id)

        if role is None:
            raise RoleNotFoundError(
                f"role is not registered in project "
                f"{project_id!r}: {role_id!r}"
            )

        return role

    def find_role(
        self,
        project_id: str,
        role_id: str,
    ) -> Optional[AgentRole]:
        """Return one registered role when present."""
        return self.store.replay(project_id).get_role(role_id)

    def bootstrap_builtin_roles(
        self,
        project_id: str,
        *,
        timestamp: int,
    ) -> Tuple[AgentRole, ...]:
        """Ensure the exact built-in catalog exists for one project.

        Existing built-in definitions are verified against the current
        catalog. Missing definitions are appended. Any conflicting stored
        definition fails closed rather than being silently replaced.
        """
        catalog = builtin_agent_roles()
        catalog_by_id: Dict[str, AgentRole] = {
            role.role_id: role
            for role in catalog
        }

        state = self.store.replay(project_id)

        for role_id, expected_role in catalog_by_id.items():
            existing = state.roles.get(role_id)

            if existing is None:
                continue

            if existing != expected_role:
                raise BuiltinRoleConflictError(
                    f"stored built-in role differs from catalog: {role_id}"
                )

        for role in catalog:
            if role.role_id in state.roles:
                continue

            self.store.append_role(
                project_id,
                role,
                timestamp=timestamp,
            )

        return self.list_roles(project_id)

    def register_custom_role(
        self,
        project_id: str,
        role: AgentRole,
        *,
        timestamp: int,
    ) -> AgentRole:
        """Register one immutable custom role after policy validation."""
        self._validate_custom_role(role)

        if self.find_role(project_id, role.role_id) is not None:
            raise RoleAlreadyRegisteredError(
                f"role_id already registered: {role.role_id}"
            )

        try:
            self.store.append_role(
                project_id,
                role,
                timestamp=timestamp,
            )
        except DuplicateRecordError as error:
            raise RoleAlreadyRegisteredError(
                f"role_id already registered: {role.role_id}"
            ) from error

        return self.get_role(project_id, role.role_id)

    def list_assignments(
        self,
        project_id: str,
        *,
        role_id: Optional[str] = None,
        status: Optional[AssignmentStatus] = None,
    ) -> Tuple[Assignment, ...]:
        """Return latest assignment snapshots in deterministic order."""
        assignments = tuple(
            sorted(
                self.store.replay(project_id).assignments.values(),
                key=lambda assignment: assignment.assignment_id,
            )
        )

        if role_id is not None:
            assignments = tuple(
                assignment
                for assignment in assignments
                if assignment.role_id == role_id
            )

        if status is not None:
            assignments = tuple(
                assignment
                for assignment in assignments
                if assignment.status == status
            )

        return assignments

    def find_assignment(
        self,
        project_id: str,
        assignment_id: str,
    ) -> Optional[Assignment]:
        """Return the latest assignment snapshot when present."""
        return self.store.replay(project_id).get_assignment(
            assignment_id
        )

    def get_assignment(
        self,
        project_id: str,
        assignment_id: str,
    ) -> Assignment:
        """Return one assignment or fail closed."""
        assignment = self.find_assignment(
            project_id,
            assignment_id,
        )

        if assignment is None:
            raise AssignmentNotFoundError(
                f"assignment is not registered in project "
                f"{project_id!r}: {assignment_id!r}"
            )

        return assignment

    def create_assignment(
        self,
        project_id: str,
        role_id: str,
        *,
        timestamp: int,
        required_capabilities: Tuple[str, ...] = (),
        backlog_item_id: Optional[str] = None,
        instructions: Optional[str] = None,
        created_by: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        assignment_id: Optional[str] = None,
    ) -> Assignment:
        """Create a pending assignment for an active registered role."""
        role = self.get_role(project_id, role_id)

        if not role.active:
            raise InvalidAssignmentError(
                f"role is inactive and cannot receive assignments: "
                f"{role_id}"
            )

        available_capabilities = set(
            self.role_capability_ids(role)
        )
        missing_capabilities = tuple(
            capability_id
            for capability_id in required_capabilities
            if capability_id not in available_capabilities
        )

        if missing_capabilities:
            missing = ", ".join(missing_capabilities)
            raise InvalidAssignmentError(
                f"role {role_id!r} does not provide required "
                f"capabilities: {missing}"
            )

        assignment = Assignment(
            assignment_id=assignment_id or new_assignment_id(),
            project_id=project_id,
            role_id=role_id,
            backlog_item_id=backlog_item_id,
            status=AssignmentStatus.PENDING,
            required_capabilities=required_capabilities,
            instructions=instructions,
            created_at=timestamp,
            updated_at=timestamp,
            created_by=created_by,
            correlation_id=correlation_id,
            causation_id=causation_id,
            version=1,
            metadata={} if metadata is None else dict(metadata),
        )

        if self.find_assignment(
            project_id,
            assignment.assignment_id,
        ) is not None:
            raise InvalidAssignmentError(
                f"assignment_id already exists: "
                f"{assignment.assignment_id}"
            )

        self.store.append_assignment(
            assignment,
            timestamp=timestamp,
        )

        return self.get_assignment(
            project_id,
            assignment.assignment_id,
        )

    def assign_agent(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Assign one agent to a pending assignment."""
        current = self.get_assignment(
            project_id,
            assignment_id,
        )

        self._require_status(
            current,
            AssignmentStatus.PENDING,
            operation="assign agent",
        )

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.ASSIGNED,
            assigned_agent_id=agent_id,
            causation_id=causation_id,
        )

    def accept_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Record acceptance by the assigned agent."""
        current = self.get_assignment(
            project_id,
            assignment_id,
        )

        self._require_status(
            current,
            AssignmentStatus.ASSIGNED,
            operation="accept assignment",
        )
        self._require_assigned_agent(current, agent_id)

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.ACCEPTED,
            causation_id=causation_id,
        )

    def activate_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Move an accepted assignment into active execution."""
        current = self.get_assignment(
            project_id,
            assignment_id,
        )

        self._require_status(
            current,
            AssignmentStatus.ACCEPTED,
            operation="activate assignment",
        )
        self._require_assigned_agent(current, agent_id)

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.ACTIVE,
            causation_id=causation_id,
        )

    def list_handoffs(
        self,
        project_id: str,
        *,
        assignment_id: Optional[str] = None,
    ) -> Tuple[AssignmentHandoff, ...]:
        """Return immutable handoff records in append order."""
        handoffs = self.store.replay(project_id).handoffs

        if assignment_id is not None:
            handoffs = tuple(
                handoff
                for handoff in handoffs
                if handoff.assignment_id == assignment_id
            )

        return handoffs

    def list_results(
        self,
        project_id: str,
        *,
        assignment_id: Optional[str] = None,
    ) -> Tuple[AssignmentResult, ...]:
        """Return immutable assignment results in append order."""
        results = self.store.replay(project_id).results

        if assignment_id is not None:
            results = tuple(
                result
                for result in results
                if result.assignment_id == assignment_id
            )

        return results

    def block_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Move active work into the governed blocked state."""
        current = self.get_assignment(project_id, assignment_id)

        self._require_status(
            current,
            AssignmentStatus.ACTIVE,
            operation="block assignment",
        )
        self._require_assigned_agent(current, agent_id)

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.BLOCKED,
            causation_id=causation_id,
        )

    def unblock_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Return blocked work to active execution."""
        current = self.get_assignment(project_id, assignment_id)

        self._require_status(
            current,
            AssignmentStatus.BLOCKED,
            operation="unblock assignment",
        )
        self._require_assigned_agent(current, agent_id)

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.ACTIVE,
            causation_id=causation_id,
        )

    def request_handoff(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        to_role_id: str,
        reason: HandoffReason,
        summary: str,
        timestamp: int,
        evidence_refs: Tuple[str, ...] = (),
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        handoff_id: Optional[str] = None,
    ) -> Tuple[Assignment, AssignmentHandoff]:
        """Record a responsibility handoff request from active work."""
        current = self.get_assignment(project_id, assignment_id)

        self._require_status(
            current,
            AssignmentStatus.ACTIVE,
            operation="request handoff",
        )
        self._require_assigned_agent(current, agent_id)

        target_role = self.get_role(project_id, to_role_id)

        if not target_role.active:
            raise InvalidAssignmentError(
                f"handoff target role is inactive: {to_role_id}"
            )

        handoff = AssignmentHandoff(
            handoff_id=handoff_id or new_handoff_id(),
            assignment_id=current.assignment_id,
            project_id=current.project_id,
            from_role_id=current.role_id,
            to_role_id=target_role.role_id,
            reason=reason,
            summary=summary,
            evidence_refs=evidence_refs,
            requested_by=agent_id,
            timestamp=timestamp,
            correlation_id=(
                correlation_id
                if correlation_id is not None
                else current.correlation_id
            ),
            causation_id=causation_id,
            metadata={} if metadata is None else dict(metadata),
        )

        if any(
            existing.handoff_id == handoff.handoff_id
            for existing in self.list_handoffs(project_id)
        ):
            raise InvalidAssignmentError(
                f"handoff_id already exists: {handoff.handoff_id}"
            )

        if timestamp < current.updated_at:
            raise InvalidAssignmentTransitionError(
                "handoff timestamp must not move backwards"
            )

        self.store.append_handoff(handoff)

        updated = self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.HANDOFF_REQUESTED,
            causation_id=causation_id,
        )

        return updated, handoff

    def complete_handoff(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        timestamp: int,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Mark a requested responsibility transfer as handed off."""
        current = self.get_assignment(project_id, assignment_id)

        self._require_status(
            current,
            AssignmentStatus.HANDOFF_REQUESTED,
            operation="complete handoff",
        )
        self._require_assigned_agent(current, agent_id)

        handoffs = self.list_handoffs(
            project_id,
            assignment_id=assignment_id,
        )

        if not handoffs:
            raise InvalidAssignmentTransitionError(
                "cannot complete handoff without a recorded request"
            )

        return self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=AssignmentStatus.HANDED_OFF,
            causation_id=causation_id,
        )

    def complete_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        summary: str,
        timestamp: int,
        evidence_refs: Tuple[str, ...] = (),
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        result_id: Optional[str] = None,
    ) -> Tuple[Assignment, AssignmentResult]:
        """Complete active work and record its immutable evidence."""
        return self._terminalize_assignment(
            project_id,
            assignment_id,
            agent_id=agent_id,
            status=AssignmentStatus.COMPLETED,
            outcome=AssignmentOutcome.SUCCEEDED,
            summary=summary,
            timestamp=timestamp,
            evidence_refs=evidence_refs,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata,
            result_id=result_id,
            allowed_statuses=(AssignmentStatus.ACTIVE,),
        )

    def fail_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: str,
        summary: str,
        timestamp: int,
        evidence_refs: Tuple[str, ...] = (),
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        result_id: Optional[str] = None,
    ) -> Tuple[Assignment, AssignmentResult]:
        """Fail active or blocked work and preserve failure evidence."""
        return self._terminalize_assignment(
            project_id,
            assignment_id,
            agent_id=agent_id,
            status=AssignmentStatus.FAILED,
            outcome=AssignmentOutcome.FAILED,
            summary=summary,
            timestamp=timestamp,
            evidence_refs=evidence_refs,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata,
            result_id=result_id,
            allowed_statuses=(
                AssignmentStatus.ACTIVE,
                AssignmentStatus.BLOCKED,
            ),
        )

    def cancel_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        summary: str,
        timestamp: int,
        agent_id: Optional[str] = None,
        evidence_refs: Tuple[str, ...] = (),
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        metadata: Optional[Dict[str, object]] = None,
        result_id: Optional[str] = None,
    ) -> Tuple[Assignment, AssignmentResult]:
        """Cancel non-terminal work and record the cancellation."""
        current = self.get_assignment(project_id, assignment_id)

        allowed_statuses = (
            AssignmentStatus.PENDING,
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.ACCEPTED,
            AssignmentStatus.ACTIVE,
            AssignmentStatus.BLOCKED,
            AssignmentStatus.HANDOFF_REQUESTED,
        )

        if current.status not in allowed_statuses:
            raise InvalidAssignmentTransitionError(
                f"cannot cancel assignment while assignment is "
                f"{current.status.value}"
            )

        if current.assigned_agent_id is not None:
            if agent_id is None:
                raise AssignmentAgentMismatchError(
                    "assigned work requires agent_id for cancellation"
                )

            self._require_assigned_agent(current, agent_id)

        return self._terminalize_assignment(
            project_id,
            assignment_id,
            agent_id=agent_id,
            status=AssignmentStatus.CANCELLED,
            outcome=AssignmentOutcome.CANCELLED,
            summary=summary,
            timestamp=timestamp,
            evidence_refs=evidence_refs,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata,
            result_id=result_id,
            allowed_statuses=allowed_statuses,
        )

    def _terminalize_assignment(
        self,
        project_id: str,
        assignment_id: str,
        *,
        agent_id: Optional[str],
        status: AssignmentStatus,
        outcome: AssignmentOutcome,
        summary: str,
        timestamp: int,
        evidence_refs: Tuple[str, ...],
        correlation_id: Optional[str],
        causation_id: Optional[str],
        metadata: Optional[Dict[str, object]],
        result_id: Optional[str],
        allowed_statuses: Tuple[AssignmentStatus, ...],
    ) -> Tuple[Assignment, AssignmentResult]:
        """Record one legal terminal transition and its result."""
        current = self.get_assignment(project_id, assignment_id)

        if current.status not in allowed_statuses:
            expected = ", ".join(
                allowed.value for allowed in allowed_statuses
            )
            raise InvalidAssignmentTransitionError(
                f"cannot move assignment from {current.status.value} "
                f"to {status.value}; expected one of: {expected}"
            )

        if current.assigned_agent_id is not None:
            if agent_id is None:
                raise AssignmentAgentMismatchError(
                    "assigned work requires the assigned agent"
                )

            self._require_assigned_agent(current, agent_id)

        if timestamp < current.updated_at:
            raise InvalidAssignmentTransitionError(
                "result timestamp must not move backwards"
            )

        result = AssignmentResult(
            result_id=result_id or new_result_id(),
            assignment_id=current.assignment_id,
            project_id=current.project_id,
            role_id=current.role_id,
            outcome=outcome,
            summary=summary,
            evidence_refs=evidence_refs,
            produced_by=agent_id,
            completed_at=timestamp,
            correlation_id=(
                correlation_id
                if correlation_id is not None
                else current.correlation_id
            ),
            causation_id=causation_id,
            metadata={} if metadata is None else dict(metadata),
        )

        if any(
            existing.result_id == result.result_id
            for existing in self.list_results(project_id)
        ):
            raise InvalidAssignmentError(
                f"result_id already exists: {result.result_id}"
            )

        updated = self._record_assignment_update(
            current,
            timestamp=timestamp,
            status=status,
            causation_id=causation_id,
        )

        self.store.append_result(result)

        return updated, result

    def _record_assignment_update(
        self,
        current: Assignment,
        *,
        timestamp: int,
        status: AssignmentStatus,
        assigned_agent_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> Assignment:
        """Validate and append the next immutable assignment snapshot."""
        if timestamp < current.updated_at:
            raise InvalidAssignmentTransitionError(
                "assignment update timestamp must not move backwards"
            )

        payload = current.model_dump(mode="python")
        payload.update(
            {
                "status": status,
                "updated_at": timestamp,
                "version": current.version + 1,
            }
        )

        if assigned_agent_id is not None:
            payload["assigned_agent_id"] = assigned_agent_id

        if causation_id is not None:
            payload["causation_id"] = causation_id

        updated = Assignment.model_validate(payload)

        self.store.append_assignment(
            updated,
            timestamp=timestamp,
        )

        return self.get_assignment(
            current.project_id,
            current.assignment_id,
        )

    @staticmethod
    def _require_status(
        assignment: Assignment,
        expected: AssignmentStatus,
        *,
        operation: str,
    ) -> None:
        """Require one exact lifecycle state for an operation."""
        if assignment.status != expected:
            raise InvalidAssignmentTransitionError(
                f"cannot {operation} while assignment is "
                f"{assignment.status.value}; expected "
                f"{expected.value}"
            )

    @staticmethod
    def _require_assigned_agent(
        assignment: Assignment,
        agent_id: str,
    ) -> None:
        """Require the operation to be performed by the assigned agent."""
        if assignment.assigned_agent_id != agent_id:
            raise AssignmentAgentMismatchError(
                f"assignment belongs to agent "
                f"{assignment.assigned_agent_id!r}, not {agent_id!r}"
            )

    @staticmethod
    def _validate_custom_role(role: AgentRole) -> None:
        """Enforce custom-role registration invariants."""
        builtin_ids = {member.value for member in BuiltinRole}

        if role.built_in:
            raise InvalidRoleRegistrationError(
                "custom roles must not set built_in=True"
            )

        if role.role_id in builtin_ids:
            raise InvalidRoleRegistrationError(
                "custom roles must not reuse a built-in role_id"
            )

        if not role.active:
            raise InvalidRoleRegistrationError(
                "new custom roles must be active"
            )

        if not role.capabilities:
            raise InvalidRoleRegistrationError(
                "custom roles must declare at least one capability"
            )

        required_capabilities = tuple(
            capability
            for capability in role.capabilities
            if capability.required
        )

        if not required_capabilities:
            raise InvalidRoleRegistrationError(
                "custom roles must declare at least one required capability"
            )

    @staticmethod
    def role_capability_ids(role: AgentRole) -> Tuple[str, ...]:
        """Return capability identifiers in model order."""
        return tuple(
            capability.capability_id
            for capability in role.capabilities
        )

    @staticmethod
    def role_has_capability(
        role: AgentRole,
        capability_id: str,
    ) -> bool:
        """Return whether a role declares one capability."""
        return any(
            capability.capability_id == capability_id
            for capability in role.capabilities
        )

    @staticmethod
    def required_capabilities(
        role: AgentRole,
    ) -> Tuple[RoleCapability, ...]:
        """Return capabilities marked as required in model order."""
        return tuple(
            capability
            for capability in role.capabilities
            if capability.required
        )
