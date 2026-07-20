"""Application service for durable governed workflow orchestration.

This coordinator is the Step 6 production boundary.  It validates every plan
against the durable Agent Roles assignment registry, applies a pure workflow
transition, persists the next immutable revision, and then publishes the
revision to Mission Control.  It never creates assignments or starts work.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .execution import ExecutionResult
from .execution_planning import RoleExecutionPlan
from .models import Assignment, AssignmentStatus
from .service import AgentRoleService
from .workflow import (
    GovernedWorkflow,
    GovernedWorkflowService,
    WorkflowAuthorization,
)
from .workflow_store import GovernedWorkflowStore
from .workflow_visibility import (
    WorkflowVisibilityRecord,
    WorkflowVisibilityService,
)


class WorkflowCoordinationError(RuntimeError):
    """Base failure raised by the durable orchestration boundary."""


class WorkflowNotFoundError(WorkflowCoordinationError):
    """Raised when a workflow is not registered in the requested project."""


class WorkflowVisibilityError(WorkflowCoordinationError):
    """Raised after persistence when Mission Control publication fails."""

    def __init__(self, workflow: GovernedWorkflow) -> None:
        super().__init__(
            "workflow revision persisted but Mission Control publication failed; "
            "reconcile visibility before continuing"
        )
        self.workflow = workflow


class GovernedWorkflowCoordinator:
    """Coordinate persisted workflow revisions without implicit side effects."""

    _PLAN_ASSIGNMENT_STATES = {
        AssignmentStatus.ASSIGNED,
        AssignmentStatus.ACCEPTED,
        AssignmentStatus.ACTIVE,
    }

    def __init__(
        self,
        *,
        role_service: AgentRoleService,
        workflow_store: GovernedWorkflowStore,
        visibility: Optional[WorkflowVisibilityService] = None,
        workflow_service: Optional[GovernedWorkflowService] = None,
    ) -> None:
        self._roles = role_service
        self._store = workflow_store
        self._visibility = visibility
        self._workflows = workflow_service or GovernedWorkflowService()

    def create(
        self,
        plan: RoleExecutionPlan,
        *,
        created_at: int,
    ) -> GovernedWorkflow:
        self._validate_plan_assignment(plan)
        workflow = self._workflows.create(plan, created_at=created_at)
        if self._store.get(plan.project_id, workflow.workflow_id) is not None:
            raise WorkflowCoordinationError(
                f"workflow already exists: {workflow.workflow_id}"
            )
        self._store.append(workflow, expected_version=0)
        self._publish(workflow)
        return workflow

    def get(self, project_id: str, workflow_id: str) -> GovernedWorkflow:
        workflow = self._store.get(project_id.strip(), workflow_id.strip())
        if workflow is None:
            raise WorkflowNotFoundError(
                f"workflow is not registered in project {project_id!r}: "
                f"{workflow_id!r}"
            )
        return workflow

    def record_result(
        self,
        project_id: str,
        workflow_id: str,
        plan: RoleExecutionPlan,
        result: ExecutionResult,
        *,
        timestamp: int,
        next_role_id: Optional[str] = None,
    ) -> GovernedWorkflow:
        current = self.get(project_id, workflow_id)
        self._require_visibility_current(current)
        self._validate_plan_assignment(plan, allow_terminal=True)
        updated = self._workflows.record_result(
            current,
            plan,
            result,
            timestamp=timestamp,
            next_role_id=next_role_id,
        )
        self._store.append(updated, expected_version=current.version)
        self._publish(updated)
        return updated

    def authorize(
        self,
        project_id: str,
        workflow_id: str,
        authorization: WorkflowAuthorization,
        *,
        next_plan: Optional[RoleExecutionPlan] = None,
    ) -> GovernedWorkflow:
        current = self.get(project_id, workflow_id)
        self._require_visibility_current(current)
        if next_plan is not None:
            self._validate_plan_assignment(next_plan)
        updated = self._workflows.authorize(
            current,
            authorization,
            next_plan=next_plan,
        )
        self._store.append(updated, expected_version=current.version)
        self._publish(updated)
        return updated

    def reconcile_visibility(
        self,
        project_id: str,
    ) -> Tuple[WorkflowVisibilityRecord, ...]:
        """Idempotently publish every latest durable workflow revision."""
        if self._visibility is None:
            return ()
        return tuple(
            self._visibility.publish(workflow)
            for workflow in self._store.list(project_id.strip())
        )

    def _validate_plan_assignment(
        self,
        plan: RoleExecutionPlan,
        *,
        allow_terminal: bool = False,
    ) -> Assignment:
        assignment = self._roles.get_assignment(
            plan.project_id,
            plan.assignment_id,
        )
        if (
            assignment.project_id != plan.project_id
            or assignment.role_id != plan.role_id
            or assignment.assigned_agent_id != plan.agent_id
        ):
            raise WorkflowCoordinationError(
                "execution plan does not match durable assignment authority"
            )
        allowed = set(self._PLAN_ASSIGNMENT_STATES)
        if allow_terminal:
            allowed.update({
                AssignmentStatus.COMPLETED,
                AssignmentStatus.FAILED,
                AssignmentStatus.BLOCKED,
                AssignmentStatus.CANCELLED,
            })
        if assignment.status not in allowed:
            raise WorkflowCoordinationError(
                f"assignment state cannot support workflow plan: "
                f"{assignment.status.value}"
            )
        return assignment

    def _publish(self, workflow: GovernedWorkflow) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(workflow)
        except Exception as exc:
            raise WorkflowVisibilityError(workflow) from exc

    def _require_visibility_current(self, workflow: GovernedWorkflow) -> None:
        if self._visibility is None:
            return
        records = self._visibility.list_records(workflow.project_id)
        record = next(
            (
                item
                for item in records
                if item.workflow_id == workflow.workflow_id
            ),
            None,
        )
        if record is None or record.version != workflow.version:
            raise WorkflowCoordinationError(
                "Mission Control visibility is not current; reconcile before "
                "continuing workflow"
            )
