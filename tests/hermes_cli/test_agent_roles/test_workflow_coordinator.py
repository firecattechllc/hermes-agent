"""Step 6 durable orchestration boundary certification tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermes_cli.agent_roles.models import AssignmentStatus
from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision,
    WorkflowAuthorization,
    WorkflowDecision,
)
from hermes_cli.agent_roles.workflow_coordinator import (
    GovernedWorkflowCoordinator,
    WorkflowCoordinationError,
    WorkflowVisibilityError,
)
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore
from hermes_cli.agent_roles.workflow_visibility import WorkflowVisibilityService
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow import plan, result


@dataclass(frozen=True)
class FakeAssignment:
    project_id: str
    assignment_id: str
    role_id: str
    assigned_agent_id: str
    status: AssignmentStatus = AssignmentStatus.ASSIGNED


class FakeRoles:
    def __init__(self) -> None:
        self.assignments: dict[str, FakeAssignment] = {}

    def add(self, assignment: FakeAssignment) -> None:
        self.assignments[assignment.assignment_id] = assignment

    def get_assignment(self, project_id: str, assignment_id: str) -> FakeAssignment:
        assignment = self.assignments[assignment_id]
        if assignment.project_id != project_id:
            raise KeyError(assignment_id)
        return assignment


def add_plan(roles: FakeRoles, role: str, assignment: str, plan_id: str):
    execution_plan = plan(role=role, assignment=assignment, plan_id=plan_id)
    roles.add(FakeAssignment(
        project_id=execution_plan.project_id,
        assignment_id=execution_plan.assignment_id,
        role_id=execution_plan.role_id,
        assigned_agent_id=execution_plan.agent_id,
    ))
    return execution_plan


def coordinator(tmp_path, roles: FakeRoles, *, visibility=True):
    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    visibility_service = None
    if visibility:
        mission = MissionControlService(
            store=MissionControlStore(tmp_path / "mission")
        )
        visibility_service = WorkflowVisibilityService(mission)
    return GovernedWorkflowCoordinator(
        role_service=roles,  # type: ignore[arg-type]
        workflow_store=workflow_store,
        visibility=visibility_service,
    ), workflow_store, visibility_service


def test_coordinator_persists_and_publishes_each_revision(tmp_path) -> None:
    roles = FakeRoles()
    builder = add_plan(roles, "builder", "assign_1", "plan_1")
    app, store, visibility = coordinator(tmp_path, roles)
    created = app.create(builder, created_at=10)
    waiting = app.record_result(
        "project_1",
        created.workflow_id,
        builder,
        result(builder),
        timestamp=31,
        next_role_id="reviewer",
    )
    assert store.get("project_1", created.workflow_id) == waiting
    assert visibility is not None
    record = visibility.list_records("project_1")[0]
    assert record.version == 2
    assert record.proposed_decision == "advance"


def test_advance_requires_registered_matching_assignment(tmp_path) -> None:
    roles = FakeRoles()
    builder = add_plan(roles, "builder", "assign_1", "plan_1")
    app, _, _ = coordinator(tmp_path, roles)
    created = app.create(builder, created_at=10)
    waiting = app.record_result(
        "project_1", created.workflow_id, builder, result(builder),
        timestamp=31, next_role_id="reviewer",
    )
    reviewer = plan(role="reviewer", assignment="assign_2", plan_id="plan_2")
    authorization = WorkflowAuthorization(
        authorization_id="auth_advance",
        workflow_id=waiting.workflow_id,
        project_id=waiting.project_id,
        expected_version=waiting.version,
        decision=WorkflowDecision.ADVANCE,
        disposition=AuthorizationDecision.APPROVED,
        actor="human",
        reason="review is explicitly authorized",
        timestamp=40,
        to_role_id="reviewer",
    )
    with pytest.raises(KeyError):
        app.authorize(
            "project_1", waiting.workflow_id, authorization,
            next_plan=reviewer,
        )
    assert app.get("project_1", waiting.workflow_id).version == 2


def test_plan_cannot_expand_durable_assignment_authority(tmp_path) -> None:
    roles = FakeRoles()
    builder = add_plan(roles, "builder", "assign_1", "plan_1")
    roles.assignments["assign_1"] = FakeAssignment(
        project_id="project_1",
        assignment_id="assign_1",
        role_id="reviewer",
        assigned_agent_id=builder.agent_id,
    )
    app, _, _ = coordinator(tmp_path, roles)
    with pytest.raises(WorkflowCoordinationError, match="authority"):
        app.create(builder, created_at=10)


def test_visibility_failure_preserves_revision_for_reconciliation(tmp_path) -> None:
    roles = FakeRoles()
    builder = add_plan(roles, "builder", "assign_1", "plan_1")
    app, store, _ = coordinator(tmp_path, roles, visibility=False)

    class FailingVisibility:
        def publish(self, workflow):
            raise RuntimeError("temporary Mission Control failure")

    app._visibility = FailingVisibility()  # type: ignore[assignment]
    with pytest.raises(WorkflowVisibilityError) as captured:
        app.create(builder, created_at=10)
    persisted = store.get("project_1", captured.value.workflow.workflow_id)
    assert persisted == captured.value.workflow

    mission = MissionControlService(
        store=MissionControlStore(tmp_path / "mission-recovered")
    )
    app._visibility = WorkflowVisibilityService(mission)
    with pytest.raises(WorkflowCoordinationError, match="reconcile"):
        app.record_result(
            "project_1",
            persisted.workflow_id,
            builder,
            result(builder),
            timestamp=31,
            next_role_id="reviewer",
        )
    records = app.reconcile_visibility("project_1")
    assert len(records) == 1
    assert records[0].version == 1


def test_duplicate_create_is_rejected_without_new_revision(tmp_path) -> None:
    roles = FakeRoles()
    builder = add_plan(roles, "builder", "assign_1", "plan_1")
    app, store, _ = coordinator(tmp_path, roles)
    workflow = app.create(builder, created_at=10)
    with pytest.raises(WorkflowCoordinationError, match="already exists"):
        app.create(builder, created_at=10)
    assert store.get("project_1", workflow.workflow_id).version == 1
