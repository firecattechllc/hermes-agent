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
    BuiltinRole,
    RoleCapability,
    builtin_agent_roles,
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
