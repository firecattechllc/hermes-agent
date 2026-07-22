"""Step 11 governed runtime-result finalization certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.execution import ExecutionOutcome
from hermes_cli.agent_roles.runtime_execution_store import RuntimeExecutionStore
from hermes_cli.agent_roles.workflow_result import (
    WorkflowResultCoordinator,
    WorkflowResultError,
    WorkflowResultPublicationError,
)
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore

from .test_runtime_execution import admit, runtime_app, start


def test_terminal_runtime_result_finalizes_workflow_once(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    ready = admit(runtime, dispatch, plan)
    running = start(runtime, ready, plan)

    terminal = runtime.complete(
        project_id=dispatch.project_id,
        execution_id=running.execution_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=50,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="builder completed governed work",
        evidence=(),
    )

    workflows = runtime._workflows
    executions = runtime._executions
    initial = workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    )
    assert initial is not None

    coordinator = WorkflowResultCoordinator(
        executions=executions,
        workflows=workflows,
    )

    finalized = coordinator.record_result(
        project_id=dispatch.project_id,
        execution_id=terminal.execution_id,
        plan=plan,
        timestamp=51,
    )

    repeated = coordinator.record_result(
        project_id=dispatch.project_id,
        execution_id=terminal.execution_id,
        plan=plan,
        timestamp=52,
    )

    assert repeated == finalized
    assert finalized.version == initial.version + 1
    assert finalized.stages[finalized.current_stage].result_id == (
        terminal.result.result_id
    )
    assert finalized.stages[finalized.current_stage].outcome == (
        ExecutionOutcome.SUCCEEDED
    )
    assert workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    ) == finalized


def test_non_terminal_runtime_execution_is_rejected(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    running = start(runtime, admit(runtime, dispatch, plan), plan)

    coordinator = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=runtime._workflows,
    )


    with pytest.raises(
        WorkflowResultError,
        match="terminal runtime execution result is required",
    ):
        coordinator.record_result(
            project_id=dispatch.project_id,
            execution_id=running.execution_id,
            plan=plan,
            timestamp=51,
        )


def test_forged_execution_authority_is_rejected(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    running = start(runtime, admit(runtime, dispatch, plan), plan)
    terminal = runtime.complete(
        project_id=dispatch.project_id,
        execution_id=running.execution_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=50,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="builder completed governed work",
        evidence=(),
    )
    forged_plan = plan.model_copy(update={"agent_id": "forged-agent"})

    coordinator = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=runtime._workflows,
    )


    with pytest.raises(
        WorkflowResultError,
        match="runtime result authority association mismatch",
    ):
        coordinator.record_result(
            project_id=dispatch.project_id,
            execution_id=terminal.execution_id,
            plan=forged_plan,
            timestamp=51,
        )


def test_conflicting_duplicate_result_is_rejected(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    running = start(runtime, admit(runtime, dispatch, plan), plan)
    terminal = runtime.complete(
        project_id=dispatch.project_id,
        execution_id=running.execution_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=50,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="builder completed governed work",
        evidence=(),
    )

    coordinator = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=runtime._workflows,
    )

    finalized = coordinator.record_result(
        project_id=dispatch.project_id,
        execution_id=terminal.execution_id,
        plan=plan,
        timestamp=51,
    )

    conflicting_result = terminal.result.model_copy(
        update={
            "result_id": "result_conflicting",
            "outcome": ExecutionOutcome.FAILED,
        }
    )
    conflicting_execution = terminal.model_copy(
        update={
            "revision": terminal.revision + 1,
            "result": conflicting_result,
        }
    )
    class ConflictingExecutionStore:
        def get(self, project_id, execution_id):
            if (
                project_id == conflicting_execution.project_id
                and execution_id == conflicting_execution.execution_id
            ):
                return conflicting_execution
            return None

    conflicting_coordinator = WorkflowResultCoordinator(
        executions=ConflictingExecutionStore(),
        workflows=runtime._workflows,
    )

    with pytest.raises(
        WorkflowResultError,
        match="workflow stage already contains a conflicting result",
    ):
        conflicting_coordinator.record_result(
            project_id=dispatch.project_id,
            execution_id=terminal.execution_id,
            plan=plan,
            timestamp=52,
        )

    assert runtime._workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    ) == finalized


def test_workflow_revision_drift_is_rejected(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    running = start(runtime, admit(runtime, dispatch, plan), plan)
    terminal = runtime.complete(
        project_id=dispatch.project_id,
        execution_id=running.execution_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=50,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="builder completed governed work",
        evidence=(),
    )

    workflow = runtime._workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    )
    assert workflow is not None

    drifted = workflow.model_copy(
        update={
            "version": workflow.version + 1,
            "updated_at": 51,
        }
    )
    class DriftedWorkflowStore:
        def get(self, project_id, workflow_id):
            if (
                project_id == drifted.project_id
                and workflow_id == drifted.workflow_id
            ):
                return drifted
            return None

        def append(self, workflow, *, expected_version=None):
            raise AssertionError("drifted workflow must not be persisted")

    coordinator = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=DriftedWorkflowStore(),
    )

    with pytest.raises(
        WorkflowResultError,
        match="workflow",
    ):
        coordinator.record_result(
            project_id=dispatch.project_id,
            execution_id=terminal.execution_id,
            plan=plan,
            timestamp=52,
        )

    assert runtime._workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    ) == workflow


def test_publication_failure_preserves_durable_workflow_result(tmp_path) -> None:
    runtime, _, dispatch, plan, _ = runtime_app(
        tmp_path,
        record_step7=False,
    )
    running = start(runtime, admit(runtime, dispatch, plan), plan)
    terminal = runtime.complete(
        project_id=dispatch.project_id,
        execution_id=running.execution_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=50,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="builder completed governed work",
        evidence=(),
    )

    initial = runtime._workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    )
    assert initial is not None

    class FailingVisibility:
        def publish(self, workflow):
            raise RuntimeError("visibility unavailable")

    coordinator = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=runtime._workflows,
        visibility=FailingVisibility(),
    )

    with pytest.raises(
        WorkflowResultPublicationError,
        match="publication failed",
    ):
        coordinator.record_result(
            project_id=dispatch.project_id,
            execution_id=terminal.execution_id,
            plan=plan,
            timestamp=51,
        )

    persisted = runtime._workflows.get(
        dispatch.project_id,
        dispatch.workflow_id,
    )
    assert persisted is not None
    assert persisted.version == initial.version + 1
    assert persisted.stages[persisted.current_stage].result_id == (
        terminal.result.result_id
    )
    assert persisted.stages[persisted.current_stage].outcome == (
        ExecutionOutcome.SUCCEEDED
    )
