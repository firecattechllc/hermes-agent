"""Governed finalization of terminal runtime execution results.

This coordinator bridges a durable terminal RuntimeExecutionRecord into the
existing governed workflow result transition. It never launches workers,
creates retries, schedules successor roles, authorizes decisions, or promotes
artifacts automatically.
"""

from __future__ import annotations

from typing import Optional, Protocol

from .execution_planning import RoleExecutionPlan
from .runtime_execution import (
    TERMINAL_RUNTIME_EXECUTION_STATES,
    RuntimeExecutionRecord,
)
from .workflow import GovernedWorkflow, GovernedWorkflowService


class _RuntimeExecutionStore(Protocol):
    def get(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[RuntimeExecutionRecord]: ...


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


class WorkflowResultError(RuntimeError):
    """Fail-closed governed workflow-result finalization violation."""


class WorkflowResultPublicationError(WorkflowResultError):
    """Workflow result persisted but a downstream publication failed."""

    def __init__(self, workflow: GovernedWorkflow, target: str) -> None:
        super().__init__(
            f"workflow result persisted but {target} publication failed; reconcile"
        )
        self.workflow = workflow
        self.target = target


class WorkflowResultCoordinator:
    """Finalize terminal runtime results into governed workflow revisions."""

    def __init__(
        self,
        *,
        executions: _RuntimeExecutionStore,
        workflows: _WorkflowStore,
        visibility: Optional[_WorkflowVisibility] = None,
        service: Optional[GovernedWorkflowService] = None,
    ) -> None:
        self._executions = executions
        self._workflows = workflows
        self._visibility = visibility
        self._service = service or GovernedWorkflowService()

    def record_result(
        self,
        *,
        project_id: str,
        execution_id: str,
        plan: RoleExecutionPlan,
        timestamp: int,
        next_role_id: Optional[str] = None,
    ) -> GovernedWorkflow:
        execution = self._executions.get(project_id, execution_id)
        if execution is None:
            raise WorkflowResultError("runtime execution not found")
        if (
            execution.state not in TERMINAL_RUNTIME_EXECUTION_STATES
            or execution.result is None
            or execution.completed_at is None
        ):
            raise WorkflowResultError(
                "terminal runtime execution result is required"
            )
        if (
            execution.project_id != project_id
            or execution.workflow_id.strip() == ""
            or execution.project_id != plan.project_id
            or execution.assignment_id != plan.assignment_id
            or execution.plan_id != plan.plan_id
            or execution.role_id != plan.role_id
            or execution.agent_id != plan.agent_id
            or execution.result.project_id != project_id
            or execution.result.assignment_id != plan.assignment_id
            or execution.result.plan_id != plan.plan_id
            or execution.result.role_id != plan.role_id
            or execution.result.agent_id != plan.agent_id
        ):
            raise WorkflowResultError(
                "runtime result authority association mismatch"
            )

        workflow = self._workflows.get(project_id, execution.workflow_id)
        if workflow is None:
            raise WorkflowResultError("governed workflow not found")
        if (
            workflow.project_id != project_id
            or workflow.workflow_id != execution.workflow_id
        ):
            raise WorkflowResultError("workflow authority association mismatch")

        stage = workflow.stages[workflow.current_stage]
        if stage.result_id is not None:
            if (
                stage.result_id == execution.result.result_id
                and stage.outcome == execution.result.outcome
            ):
                self._publish(workflow)
                return workflow
            raise WorkflowResultError(
                "workflow stage already contains a conflicting result"
            )

        if workflow.version != execution.workflow_version:
            raise WorkflowResultError(
                "workflow version drift detected"
            )

        try:
            finalized = self._service.record_result(
                workflow,
                plan,
                execution.result,
                timestamp=timestamp,
                next_role_id=next_role_id,
            )
            self._workflows.append(
                finalized,
                expected_version=workflow.version,
            )
        except WorkflowResultError:
            raise
        except Exception as exc:
            raise WorkflowResultError(
                "workflow result finalization failed"
            ) from exc

        self._publish(finalized)
        return finalized

    def _publish(self, workflow: GovernedWorkflow) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(workflow)
        except Exception as exc:
            raise WorkflowResultPublicationError(
                workflow,
                "Mission Control",
            ) from exc
