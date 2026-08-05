"""Tests for the Hermes add-on agent composition layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.agent_roles import models as m
from hermes_cli.agent_roles import service as svc
from hermes_cli.agent_roles import store as s
from hermes_cli.agent_roles.composition import (
    AgentComposition,
    AgentCompositionRegistry,
    CompositionMembershipError,
    CompositionNotFoundError,
    CompositionValidationError,
    dispatch_plan_for_composition,
)
from hermes_cli.agent_roles.execution import ExecutionAction
from hermes_cli.agent_roles.execution_planning import (
    ExecutionPlanStep,
    RoleExecutionPlan,
)
from hermes_cli.agent_roles.workflow_coordinator import GovernedWorkflowCoordinator
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore

PROJECT = "hermes-platform"


def _service(tmp_path: Path) -> svc.AgentRoleService:
    store = s.AgentRoleStore(tmp_path / "agent-role-store")
    return svc.AgentRoleService(store)


def _register_role(service: svc.AgentRoleService, role_id: str, *, timestamp: int = 1) -> m.AgentRole:
    role = m.AgentRole(
        role_id=role_id,
        name=role_id.title(),
        description=f"Handles {role_id} work.",
        capabilities=(
            m.RoleCapability(capability_id=f"{role_id}-capability", description="Do the work."),
        ),
        policy=m.RolePolicy(),
        built_in=False,
        active=True,
    )
    service.store.append_role(PROJECT, role, timestamp=timestamp)
    return role


def _register_assignment(
    service: svc.AgentRoleService,
    role_id: str,
    *,
    assignment_id: str = "assign-1",
    timestamp: int = 1,
) -> None:
    assignment = m.Assignment(
        assignment_id=assignment_id,
        project_id=PROJECT,
        role_id=role_id,
        assigned_agent_id="agent-1",
        status=m.AssignmentStatus.ASSIGNED,
    )
    service.store.append_assignment(assignment, timestamp=timestamp)


def _plan(role_id: str, *, plan_id: str = "plan-1", assignment_id: str = "assign-1") -> RoleExecutionPlan:
    return RoleExecutionPlan(
        plan_id=plan_id,
        project_id=PROJECT,
        assignment_id=assignment_id,
        contract_id="contract-1",
        role_id=role_id,
        agent_id="agent-1",
        responsibilities=("do the thing",),
        allowed_actions=(ExecutionAction.PLAN,),
        allowed_next_roles=(),
        steps=(
            ExecutionPlanStep(sequence=1, action=ExecutionAction.PLAN, responsibility="do the thing"),
        ),
        created_at=0,
    )


def test_composition_requires_at_least_one_member() -> None:
    with pytest.raises(CompositionValidationError, match="at least one"):
        AgentComposition(
            composition_id="comp_x",
            project_id=PROJECT,
            display_name="Empty",
            member_role_ids=(),
            created_at=0,
        )


def test_composition_rejects_duplicate_members() -> None:
    with pytest.raises(CompositionValidationError, match="duplicate"):
        AgentComposition(
            composition_id="comp_x",
            project_id=PROJECT,
            display_name="Dup",
            member_role_ids=("builder", "builder"),
            created_at=0,
        )


def test_registry_registers_composition_with_real_registered_roles(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    _register_role(service, "reviewer")
    registry = AgentCompositionRegistry(service)

    composition = registry.register(
        PROJECT, "Release Squad", ("builder", "reviewer"), created_at=100
    )

    assert composition.member_role_ids == ("builder", "reviewer")
    assert registry.get(composition.composition_id) == composition


def test_registry_rejects_unknown_role(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    registry = AgentCompositionRegistry(service)

    with pytest.raises(svc.RoleNotFoundError):
        registry.register(PROJECT, "Broken", ("builder", "nonexistent-role"), created_at=1)

    # The failed registration must not have partially registered anything.
    assert registry.list_for_project(PROJECT) == ()


def test_registry_rejects_duplicate_composition_name(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    registry = AgentCompositionRegistry(service)
    registry.register(PROJECT, "Squad", ("builder",), created_at=1)

    with pytest.raises(CompositionValidationError, match="already exists"):
        registry.register(PROJECT, "Squad", ("builder",), created_at=2)


def test_registry_get_unknown_composition_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    registry = AgentCompositionRegistry(service)

    with pytest.raises(CompositionNotFoundError):
        registry.get("comp_does_not_exist")


def test_deactivate_marks_composition_inactive(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    registry = AgentCompositionRegistry(service)
    composition = registry.register(PROJECT, "Squad", ("builder",), created_at=1)

    deactivated = registry.deactivate(composition.composition_id)

    assert deactivated.active is False
    assert registry.get(composition.composition_id).active is False


def test_dispatch_delegates_to_real_coordinator_for_member_role(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    _register_assignment(service, "builder")
    registry = AgentCompositionRegistry(service)
    composition = registry.register(PROJECT, "Squad", ("builder",), created_at=1)

    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    coordinator = GovernedWorkflowCoordinator(
        role_service=service,  # type: ignore[arg-type]
        workflow_store=workflow_store,
    )
    plan = _plan("builder")

    workflow = dispatch_plan_for_composition(coordinator, composition, plan, created_at=10)

    assert workflow_store.get(PROJECT, workflow.workflow_id) == workflow


def test_dispatch_rejects_plan_for_non_member_role(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    _register_role(service, "reviewer")
    registry = AgentCompositionRegistry(service)
    composition = registry.register(PROJECT, "Squad", ("builder",), created_at=1)

    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    coordinator = GovernedWorkflowCoordinator(
        role_service=service,  # type: ignore[arg-type]
        workflow_store=workflow_store,
    )
    plan = _plan("reviewer")  # not a member of the composition

    with pytest.raises(CompositionMembershipError, match="not a member"):
        dispatch_plan_for_composition(coordinator, composition, plan, created_at=10)

    assert workflow_store.get(PROJECT, "plan-1") is None


def test_dispatch_rejects_inactive_composition(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _register_role(service, "builder")
    registry = AgentCompositionRegistry(service)
    composition = registry.register(PROJECT, "Squad", ("builder",), created_at=1)
    inactive = registry.deactivate(composition.composition_id)

    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    coordinator = GovernedWorkflowCoordinator(
        role_service=service,  # type: ignore[arg-type]
        workflow_store=workflow_store,
    )
    plan = _plan("builder")

    with pytest.raises(CompositionMembershipError, match="not active"):
        dispatch_plan_for_composition(coordinator, inactive, plan, created_at=10)
