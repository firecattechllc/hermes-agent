"""Governed composition of existing Agent Roles into named, coordinated groups.

Hermes add-on Phase H. Builds the real multi-agent composition layer the
audit found missing: today ``hermes_cli/agent_roles`` defines roles
(``AgentRoleService``) and single-workflow orchestration
(``GovernedWorkflowCoordinator``/``GovernedWorkflow``) but nothing groups
several roles into a named, governed unit.

Deliberately not named "teams": ``plugins/teams_pipeline`` is an unrelated
Microsoft Teams meeting-transcript plugin, and reusing that word for agent
composition would collide with it in documentation, search, and support
conversations (see the duplication register in
``docs/roadmap/HERMES_ADDON_AUDIT.md``).

An :class:`AgentComposition` is a pure, descriptive grouping -- it grants no
authority, starts no work, and creates no new scheduler. Every member role
ID is validated against the real, durable role catalog
(``AgentRoleService.get_role``) at registration time, so a composition can
never reference a role that does not actually exist. Dispatching a plan
"for" a composition is nothing more than checking the plan's role is a
member and then delegating to the existing, already-tested
``GovernedWorkflowCoordinator`` -- no parallel execution path is introduced.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .execution_planning import RoleExecutionPlan
from .service import AgentRoleService
from .workflow import GovernedWorkflow
from .workflow_coordinator import GovernedWorkflowCoordinator

AGENT_COMPOSITION_SCHEMA_VERSION = 1


class CompositionError(RuntimeError):
    """Base failure for the agent composition layer."""


class CompositionValidationError(CompositionError):
    """A composition definition failed closed."""


class CompositionNotFoundError(CompositionError):
    """A referenced composition is not registered."""


class CompositionMembershipError(CompositionError):
    """A plan's role is not a member of the targeted composition."""


def _composition_id(project_id: str, display_name: str) -> str:
    payload = f"{project_id}:{display_name}".encode("utf-8")
    return f"comp_{hashlib.sha256(payload).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class AgentComposition:
    """An immutable, named grouping of existing, real Agent Roles.

    Grants no authority of its own -- see
    ``hermes_cli.prime.identity.FleetIdentity.grants_no_authority`` for the
    equivalent documented pattern elsewhere in this codebase. Whether a
    role may act is still decided entirely by that role's own
    ``AgentRole.policy``, unaffected by composition membership.
    """

    composition_id: str
    project_id: str
    display_name: str
    member_role_ids: Tuple[str, ...]
    created_at: int
    active: bool = True
    schema_version: int = AGENT_COMPOSITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_COMPOSITION_SCHEMA_VERSION:
            raise CompositionValidationError(
                "unsupported agent composition schema version"
            )
        if not self.project_id.strip():
            raise CompositionValidationError("project_id is required")
        if not self.display_name.strip():
            raise CompositionValidationError("display_name is required")
        if not self.member_role_ids:
            raise CompositionValidationError(
                "a composition must have at least one member role"
            )
        if len(set(self.member_role_ids)) != len(self.member_role_ids):
            raise CompositionValidationError("duplicate member role in composition")
        if self.created_at < 0:
            raise CompositionValidationError("created_at cannot be negative")

    def contains(self, role_id: str) -> bool:
        return role_id in self.member_role_ids


class AgentCompositionRegistry:
    """In-memory registry of governed compositions for one process lifetime.

    Deliberately not a new persistence layer or scheduler: this is a
    validated lookup table only. Durable role identity remains entirely
    owned by ``AgentRoleService``'s existing append-only store; this
    registry stores nothing that store doesn't already consider
    authoritative.
    """

    def __init__(self, role_service: AgentRoleService) -> None:
        self._roles = role_service
        self._by_id: Dict[str, AgentComposition] = {}

    def register(
        self,
        project_id: str,
        display_name: str,
        member_role_ids: Tuple[str, ...],
        *,
        created_at: int,
    ) -> AgentComposition:
        """Register a new composition, failing closed on any unknown role.

        Every ID in ``member_role_ids`` is resolved against the real role
        catalog via ``AgentRoleService.get_role`` -- a typo'd or
        never-registered role ID rejects the whole registration rather
        than silently admitting a composition with a dangling member.
        """

        for role_id in member_role_ids:
            self._roles.get_role(project_id, role_id)  # raises RoleNotFoundError if unknown

        composition_id = _composition_id(project_id, display_name)
        if composition_id in self._by_id:
            raise CompositionValidationError(
                f"a composition named {display_name!r} already exists in project {project_id!r}"
            )

        composition = AgentComposition(
            composition_id=composition_id,
            project_id=project_id,
            display_name=display_name,
            member_role_ids=tuple(member_role_ids),
            created_at=created_at,
        )
        self._by_id[composition_id] = composition
        return composition

    def get(self, composition_id: str) -> AgentComposition:
        composition = self._by_id.get(composition_id)
        if composition is None:
            raise CompositionNotFoundError(
                f"composition is not registered: {composition_id!r}"
            )
        return composition

    def deactivate(self, composition_id: str) -> AgentComposition:
        current = self.get(composition_id)
        updated = AgentComposition(
            composition_id=current.composition_id,
            project_id=current.project_id,
            display_name=current.display_name,
            member_role_ids=current.member_role_ids,
            created_at=current.created_at,
            active=False,
        )
        self._by_id[composition_id] = updated
        return updated

    def list_for_project(self, project_id: str) -> Tuple[AgentComposition, ...]:
        return tuple(
            sorted(
                (c for c in self._by_id.values() if c.project_id == project_id),
                key=lambda c: c.composition_id,
            )
        )


def dispatch_plan_for_composition(
    coordinator: GovernedWorkflowCoordinator,
    composition: AgentComposition,
    plan: RoleExecutionPlan,
    *,
    created_at: int,
) -> GovernedWorkflow:
    """Create a governed workflow, first checking the plan's role is a member.

    No new execution path: this validates membership and then calls the
    existing, already-tested ``GovernedWorkflowCoordinator.create`` --
    identical to calling it directly, except a plan whose role was never
    added to this composition is rejected before any workflow is created.
    """

    if not composition.active:
        raise CompositionMembershipError(
            f"composition {composition.composition_id!r} is not active"
        )
    if plan.project_id != composition.project_id:
        raise CompositionMembershipError(
            "plan project does not match composition project"
        )
    if not composition.contains(plan.role_id):
        raise CompositionMembershipError(
            f"role {plan.role_id!r} is not a member of composition {composition.composition_id!r}"
        )

    return coordinator.create(plan, created_at=created_at)
