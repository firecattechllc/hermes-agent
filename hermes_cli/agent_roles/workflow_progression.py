"""Governed authorization-driven workflow progression.

This coordinator consumes explicit workflow authorization and advances the
append-only governed workflow exactly once. It does not create assignments,
build plans, schedule work, dispatch workers, or grant authorization.
"""

from __future__ import annotations

from typing import Optional, Protocol

from .execution_planning import RoleExecutionPlan
from .workflow import (
    AuthorizationDecision,
    GovernedWorkflow,
    GovernedWorkflowService,
    WorkflowAuthorization,
    WorkflowDecision,
)


class _WorkflowStore(Protocol):
    def get(
        self,
        project_id: str,
        workflow_id: str,
    ) -> Optional[GovernedWorkflow]: ...

    def append(
        self,
        workflow: GovernedWorkflow,
        *,
        expected_version: Optional[int] = None,
    ) -> None: ...


class _WorkflowVisibility(Protocol):
    def publish(self, workflow: GovernedWorkflow): ...


class WorkflowProgressionError(RuntimeError):
    """Fail-closed governed workflow progression violation."""


class WorkflowProgressionPublicationError(WorkflowProgressionError):
    """Progression persisted but downstream publication failed."""

    def __init__(self, workflow: GovernedWorkflow, target: str) -> None:
        super().__init__(
            f"workflow progression persisted but {target} publication failed; "
            "reconcile"
        )
        self.workflow = workflow
        self.target = target


class WorkflowProgressionCoordinator:
    """Persist exactly one authorization-driven workflow revision."""

    def __init__(
        self,
        *,
        workflows: _WorkflowStore,
        visibility: Optional[_WorkflowVisibility] = None,
        service: Optional[GovernedWorkflowService] = None,
    ) -> None:
        self._workflows = workflows
        self._visibility = visibility
        self._service = service or GovernedWorkflowService()

    def progress(
        self,
        *,
        project_id: str,
        workflow_id: str,
        authorization: WorkflowAuthorization,
        next_plan: Optional[RoleExecutionPlan] = None,
    ) -> GovernedWorkflow:
        workflow = self._workflows.get(project_id, workflow_id)
        if workflow is None:
            raise WorkflowProgressionError("governed workflow not found")

        if (
            workflow.project_id != project_id
            or workflow.workflow_id != workflow_id
            or authorization.project_id != project_id
            or authorization.workflow_id != workflow_id
        ):
            raise WorkflowProgressionError(
                "workflow progression authority association mismatch"
            )

        existing = next(
            (
                item
                for item in workflow.authorizations
                if item.authorization_id == authorization.authorization_id
            ),
            None,
        )
        if existing is not None:
            if existing == authorization:
                self._publish(workflow)
                return workflow
            raise WorkflowProgressionError(
                "authorization identifier contains conflicting authority"
            )

        self._validate_plan_use(
            workflow=workflow,
            authorization=authorization,
            next_plan=next_plan,
        )

        try:
            progressed = self._service.authorize(
                workflow,
                authorization,
                next_plan=next_plan,
            )
            self._workflows.append(
                progressed,
                expected_version=workflow.version,
            )
        except WorkflowProgressionError:
            raise
        except Exception as exc:
            raise WorkflowProgressionError(
                "governed workflow progression failed"
            ) from exc

        self._publish(progressed)
        return progressed

    @staticmethod
    def _validate_plan_use(
        *,
        workflow: GovernedWorkflow,
        authorization: WorkflowAuthorization,
        next_plan: Optional[RoleExecutionPlan],
    ) -> None:
        approved = (
            authorization.disposition
            == AuthorizationDecision.APPROVED
        )
        requires_plan = (
            approved
            and authorization.decision
            in {
                WorkflowDecision.ADVANCE,
                WorkflowDecision.RETRY,
            }
        )

        if not approved and next_plan is not None:
            raise WorkflowProgressionError(
                "denied authorization cannot carry an execution plan"
            )

        if requires_plan and next_plan is None:
            raise WorkflowProgressionError(
                "approved workflow progression requires an execution plan"
            )

        if not requires_plan and next_plan is not None:
            raise WorkflowProgressionError(
                "terminal workflow progression cannot carry an execution plan"
            )

        if next_plan is None:
            return

        if next_plan.project_id != workflow.project_id:
            raise WorkflowProgressionError(
                "next execution plan is outside the governed project"
            )

        if any(
            stage.assignment_id == next_plan.assignment_id
            or stage.plan_id == next_plan.plan_id
            for stage in workflow.stages
        ):
            raise WorkflowProgressionError(
                "next execution plan must represent new stage authority"
            )

    def _publish(self, workflow: GovernedWorkflow) -> None:
        if self._visibility is None:
            return

        try:
            self._visibility.publish(workflow)
        except Exception as exc:
            raise WorkflowProgressionPublicationError(
                workflow,
                "Mission Control",
            ) from exc
