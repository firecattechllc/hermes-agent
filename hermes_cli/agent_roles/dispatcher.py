"""Governed dispatch between Autonomous Backlog and Agent Roles.

The dispatcher coordinates two independently persisted bounded contexts:

- Autonomous Backlog owns work eligibility and backlog lifecycle state.
- Agent Roles owns role policy and assignment lifecycle state.

This module owns no durable state and launches no agents.
"""

from __future__ import annotations

from typing import Optional, Tuple

from hermes_cli.autonomous_backlog import models as backlog_models
from hermes_cli.autonomous_backlog.service import (
    AutonomousBacklogService,
)

from .models import (
    AgentRole,
    Assignment,
    AssignmentStatus,
)
from .service import (
    AgentRoleService,
    InvalidAssignmentError,
)


class DispatcherError(RuntimeError):
    """Base error raised by governed dispatch operations."""


class BacklogItemNotFoundError(DispatcherError):
    """Raised when the requested backlog item does not exist."""


class BacklogItemNotEligibleError(DispatcherError):
    """Raised when a backlog item cannot currently be dispatched."""


class DependencyNotSatisfiedError(DispatcherError):
    """Raised when a backlog dependency has not completed."""


class MatchingRoleNotFoundError(DispatcherError):
    """Raised when no active role can govern the requested work."""


class DispatchPersistenceError(DispatcherError):
    """Raised when coordinated persistent updates cannot complete."""


class GovernedDispatcher:
    """Coordinate backlog claims with governed role assignments."""

    def __init__(
        self,
        backlog_service: AutonomousBacklogService,
        role_service: AgentRoleService,
    ) -> None:
        self.backlog_service = backlog_service
        self.role_service = role_service

    def dispatch_item(
        self,
        project_id: str,
        item_id: str,
        *,
        timestamp: int,
        role_id: Optional[str] = None,
        actor: str = "dispatcher",
    ) -> Assignment:
        """Create or recover one governed assignment for a backlog item."""
        item = self.backlog_service.store.get_item(
            project_id,
            item_id,
        )

        if item is None:
            raise BacklogItemNotFoundError(
                f"backlog item is not registered in project "
                f"{project_id!r}: {item_id!r}"
            )

        existing = self._active_assignment_for_item(
            project_id,
            item_id,
        )

        if item.status == backlog_models.BacklogStatus.CLAIMED:
            if existing is None:
                raise DispatchPersistenceError(
                    f"backlog item {item_id!r} is claimed without an "
                    "active governed assignment"
                )

            return existing

        self._require_eligible(item, timestamp=timestamp)
        self._require_dependencies(item)

        if existing is not None:
            return self._claim_for_assignment(
                item,
                existing,
                timestamp=timestamp,
                actor=actor,
                compensate=False,
            )

        role = self._select_role(
            project_id,
            item,
            requested_role_id=role_id,
        )

        try:
            assignment = self.role_service.create_assignment(
                project_id,
                role.role_id,
                timestamp=timestamp,
                required_capabilities=tuple(
                    item.required_capabilities
                ),
                backlog_item_id=item.item_id,
                instructions=self._assignment_instructions(item),
                created_by=actor,
                correlation_id=item.correlation_id,
                causation_id=item.item_id,
                risk_level=item.risk_level.value,
                requested_paths=tuple(item.allowed_paths),
                modifies_repository=bool(item.allowed_paths),
                human_approved=True,
                metadata={
                    "backlog_priority": item.priority.value,
                    "backlog_version": item.version,
                    "execution_policy_id": (
                        item.execution_policy_id
                    ),
                    "denied_paths": list(item.denied_paths),
                },
            )
        except InvalidAssignmentError as error:
            raise MatchingRoleNotFoundError(
                f"selected role {role.role_id!r} rejected backlog "
                f"item {item.item_id!r}: {error}"
            ) from error

        return self._claim_for_assignment(
            item,
            assignment,
            timestamp=timestamp,
            actor=actor,
            compensate=True,
        )

    def _claim_for_assignment(
        self,
        item: backlog_models.BacklogItem,
        assignment: Assignment,
        *,
        timestamp: int,
        actor: str,
        compensate: bool,
    ) -> Assignment:
        """Claim a backlog item or cancel the newly created assignment."""
        try:
            self.backlog_service.transition_item(
                item.project_id,
                item.item_id,
                target_status=backlog_models.BacklogStatus.CLAIMED,
                actor=actor,
                expected_version=item.version,
                correlation_id=item.correlation_id,
                causation_id=assignment.assignment_id,
                idempotency_key=(
                    f"dispatch:{item.project_id}:{item.item_id}"
                ),
                updated_at=timestamp,
            )
        except Exception as error:
            if compensate:
                try:
                    self.role_service.cancel_assignment(
                        item.project_id,
                        assignment.assignment_id,
                        summary=(
                            "Dispatch compensation: backlog claim "
                            "did not persist."
                        ),
                        timestamp=timestamp,
                        causation_id=item.item_id,
                        metadata={
                            "dispatch_compensation": True,
                            "backlog_item_id": item.item_id,
                        },
                    )
                except Exception as compensation_error:
                    raise DispatchPersistenceError(
                        f"backlog claim failed for {item.item_id!r}; "
                        "assignment compensation also failed"
                    ) from compensation_error

            raise DispatchPersistenceError(
                f"could not claim backlog item {item.item_id!r}"
            ) from error

        return self.role_service.get_assignment(
            item.project_id,
            assignment.assignment_id,
        )

    def _require_eligible(
        self,
        item: backlog_models.BacklogItem,
        *,
        timestamp: int,
    ) -> None:
        """Fail closed unless the item is eligible at the supplied time."""
        if item.status not in {
            backlog_models.BacklogStatus.APPROVED,
            backlog_models.BacklogStatus.SCHEDULED,
        }:
            raise BacklogItemNotEligibleError(
                f"backlog item {item.item_id!r} cannot dispatch while "
                f"{item.status.value}"
            )

        policy = item.schedule_policy

        if (
            policy.not_before is not None
            and timestamp < policy.not_before
        ):
            raise BacklogItemNotEligibleError(
                f"backlog item {item.item_id!r} is not eligible before "
                f"{policy.not_before}"
            )

        if (
            policy.expires_at is not None
            and timestamp > policy.expires_at
        ):
            raise BacklogItemNotEligibleError(
                f"backlog item {item.item_id!r} expired at "
                f"{policy.expires_at}"
            )

        if item.status == backlog_models.BacklogStatus.SCHEDULED:
            if (
                policy.mode
                != backlog_models.ScheduleMode.SCHEDULED
                or policy.scheduled_at is None
            ):
                raise BacklogItemNotEligibleError(
                    f"scheduled backlog item {item.item_id!r} has an "
                    "invalid schedule policy"
                )

            if timestamp < policy.scheduled_at:
                raise BacklogItemNotEligibleError(
                    f"backlog item {item.item_id!r} is scheduled for "
                    f"{policy.scheduled_at}"
                )

    def _require_dependencies(
        self,
        item: backlog_models.BacklogItem,
    ) -> None:
        """Require every declared dependency to be completed."""
        if item.blocked_by:
            blocked = ", ".join(item.blocked_by)
            raise DependencyNotSatisfiedError(
                f"backlog item {item.item_id!r} is blocked by: "
                f"{blocked}"
            )

        for dependency_id in item.dependencies:
            dependency = self.backlog_service.store.get_item(
                item.project_id,
                dependency_id,
            )

            if dependency is None:
                raise DependencyNotSatisfiedError(
                    f"backlog dependency is missing: "
                    f"{dependency_id!r}"
                )

            if (
                dependency.status
                != backlog_models.BacklogStatus.COMPLETED
            ):
                raise DependencyNotSatisfiedError(
                    f"backlog dependency {dependency_id!r} is "
                    f"{dependency.status.value}, not completed"
                )

    def _select_role(
        self,
        project_id: str,
        item: backlog_models.BacklogItem,
        *,
        requested_role_id: Optional[str],
    ) -> AgentRole:
        """Choose one deterministic active role satisfying all policy."""
        roles = self.role_service.list_roles(
            project_id,
            active_only=True,
        )

        if requested_role_id is not None:
            roles = tuple(
                role
                for role in roles
                if role.role_id == requested_role_id
            )

        matching = tuple(
            role
            for role in roles
            if self._role_matches_item(role, item)
        )

        if not matching:
            requested = (
                f" requested role {requested_role_id!r}"
                if requested_role_id is not None
                else ""
            )
            raise MatchingRoleNotFoundError(
                f"no active{requested} role can govern backlog item "
                f"{item.item_id!r}"
            )

        return sorted(
            matching,
            key=lambda role: role.role_id,
        )[0]

    @classmethod
    def _role_matches_item(
        cls,
        role: AgentRole,
        item: backlog_models.BacklogItem,
    ) -> bool:
        available = {
            capability.capability_id
            for capability in role.capabilities
        }

        if not set(item.required_capabilities).issubset(available):
            return False

        if item.risk_level.value not in role.policy.allowed_risk_levels:
            return False

        if item.allowed_paths and not role.policy.may_modify_repository:
            return False

        for path in item.allowed_paths:
            if any(
                cls._path_is_within(path, denied)
                for denied in role.policy.denied_paths
            ):
                return False

            if (
                role.policy.allowed_paths
                and not any(
                    cls._path_is_within(path, allowed)
                    for allowed in role.policy.allowed_paths
                )
            ):
                return False

        return True

    @staticmethod
    def _path_is_within(path: str, policy_path: str) -> bool:
        normalised_path = path.strip().replace("\\", "/").strip("/")
        normalised_policy = (
            policy_path.strip().replace("\\", "/").strip("/")
        )

        if not normalised_path or not normalised_policy:
            return False

        return (
            normalised_path == normalised_policy
            or normalised_path.startswith(
                f"{normalised_policy}/"
            )
        )

    def _active_assignment_for_item(
        self,
        project_id: str,
        item_id: str,
    ) -> Optional[Assignment]:
        active_statuses = {
            AssignmentStatus.PENDING,
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.ACCEPTED,
            AssignmentStatus.ACTIVE,
            AssignmentStatus.BLOCKED,
            AssignmentStatus.HANDOFF_REQUESTED,
        }

        matching = tuple(
            assignment
            for assignment in self.role_service.list_assignments(
                project_id
            )
            if assignment.backlog_item_id == item_id
            and assignment.status in active_statuses
        )

        if len(matching) > 1:
            raise DispatchPersistenceError(
                f"backlog item {item_id!r} has multiple active "
                "governed assignments"
            )

        return matching[0] if matching else None

    @staticmethod
    def _assignment_instructions(
        item: backlog_models.BacklogItem,
    ) -> str:
        sections = [
            item.title,
            "",
            item.description,
        ]

        if item.acceptance_criteria:
            sections.extend(
                [
                    "",
                    "Acceptance criteria:",
                    *(
                        f"- {criterion}"
                        for criterion in item.acceptance_criteria
                    ),
                ]
            )

        return "\n".join(sections)
