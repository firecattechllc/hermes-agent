"""Step 12 governed workflow progression certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.execution import ExecutionOutcome
from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision,
    GovernedWorkflowService,
    WorkflowAuthorization,
    WorkflowDecision,
    WorkflowState,
)
from hermes_cli.agent_roles.workflow_progression import (
    WorkflowProgressionCoordinator,
    WorkflowProgressionError,
    WorkflowProgressionPublicationError,
)
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore

from .test_workflow import plan, result


def waiting_workflow(
    store: GovernedWorkflowStore,
    *,
    next_role_id: str = "reviewer",
):
    service = GovernedWorkflowService()
    builder = plan()
    workflow = service.create(builder, created_at=10)
    store.append(workflow, expected_version=0)

    waiting = service.record_result(
        workflow,
        builder,
        result(builder),
        timestamp=31,
        next_role_id=next_role_id,
    )
    store.append(waiting, expected_version=workflow.version)
    return waiting


def authorization(
    workflow,
    *,
    decision: WorkflowDecision = WorkflowDecision.ADVANCE,
    disposition: AuthorizationDecision = AuthorizationDecision.APPROVED,
    role_id: str | None = "reviewer",
    authorization_id: str = "auth_progression_1",
):
    return WorkflowAuthorization(
        authorization_id=authorization_id,
        workflow_id=workflow.workflow_id,
        project_id=workflow.project_id,
        expected_version=workflow.version,
        decision=decision,
        disposition=disposition,
        actor="human-reviewer",
        reason="explicit governed progression approval",
        timestamp=workflow.updated_at + 1,
        to_role_id=role_id,
    )


def test_approved_advance_persists_next_stage_once(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    next_plan = plan(
        role="reviewer",
        assignment="assign_reviewer",
        plan_id="plan_reviewer",
    )
    approved = authorization(waiting)

    coordinator = WorkflowProgressionCoordinator(workflows=store)

    progressed = coordinator.progress(
        project_id=waiting.project_id,
        workflow_id=waiting.workflow_id,
        authorization=approved,
        next_plan=next_plan,
    )
    repeated = coordinator.progress(
        project_id=waiting.project_id,
        workflow_id=waiting.workflow_id,
        authorization=approved,
        next_plan=next_plan,
    )

    assert repeated == progressed
    assert progressed.state == WorkflowState.ACTIVE
    assert progressed.version == waiting.version + 1
    assert progressed.current_stage == 1
    assert len(progressed.stages) == 2
    assert progressed.stages[-1].role_id == "reviewer"
    assert progressed.stages[-1].assignment_id == "assign_reviewer"
    assert progressed.authorizations == (approved,)
    assert store.get(
        waiting.project_id,
        waiting.workflow_id,
    ) == progressed


def test_approved_retry_preserves_role_authority(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    service = GovernedWorkflowService()
    builder = plan()

    workflow = service.create(builder, created_at=10)
    store.append(workflow, expected_version=0)

    waiting = service.record_result(
        workflow,
        builder,
        result(builder, ExecutionOutcome.FAILED),
        timestamp=31,
    )
    store.append(waiting, expected_version=workflow.version)

    retry_authorization = authorization(
        waiting,
        decision=WorkflowDecision.RETRY,
        role_id=None,
    )
    retry_plan = plan(
        role="builder",
        assignment="assign_retry",
        plan_id="plan_retry",
    )

    progressed = WorkflowProgressionCoordinator(
        workflows=store
    ).progress(
        project_id=waiting.project_id,
        workflow_id=waiting.workflow_id,
        authorization=retry_authorization,
        next_plan=retry_plan,
    )

    assert progressed.state == WorkflowState.ACTIVE
    assert progressed.stages[-1].role_id == "builder"
    assert progressed.stages[-1].assignment_id == "assign_retry"


def test_denied_authorization_blocks_without_plan(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    denied = authorization(
        waiting,
        disposition=AuthorizationDecision.DENIED,
    )

    blocked = WorkflowProgressionCoordinator(
        workflows=store
    ).progress(
        project_id=waiting.project_id,
        workflow_id=waiting.workflow_id,
        authorization=denied,
    )

    assert blocked.state == WorkflowState.BLOCKED
    assert len(blocked.stages) == 1
    assert blocked.authorizations == (denied,)


def test_denied_authorization_cannot_smuggle_plan(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    denied = authorization(
        waiting,
        disposition=AuthorizationDecision.DENIED,
    )
    next_plan = plan(
        role="reviewer",
        assignment="assign_reviewer",
        plan_id="plan_reviewer",
    )

    with pytest.raises(
        WorkflowProgressionError,
        match="denied authorization cannot carry",
    ):
        WorkflowProgressionCoordinator(
            workflows=store
        ).progress(
            project_id=waiting.project_id,
            workflow_id=waiting.workflow_id,
            authorization=denied,
            next_plan=next_plan,
        )

    assert store.get(
        waiting.project_id,
        waiting.workflow_id,
    ) == waiting


def test_reused_stage_authority_is_rejected(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    reused = plan(
        role="reviewer",
        assignment=waiting.stages[0].assignment_id,
        plan_id="plan_reviewer",
    )

    with pytest.raises(
        WorkflowProgressionError,
        match="new stage authority",
    ):
        WorkflowProgressionCoordinator(
            workflows=store
        ).progress(
            project_id=waiting.project_id,
            workflow_id=waiting.workflow_id,
            authorization=authorization(waiting),
            next_plan=reused,
        )


def test_stale_authorization_fails_closed(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    stale = authorization(waiting).model_copy(
        update={"expected_version": waiting.version - 1}
    )

    with pytest.raises(
        WorkflowProgressionError,
        match="progression failed",
    ):
        WorkflowProgressionCoordinator(
            workflows=store
        ).progress(
            project_id=waiting.project_id,
            workflow_id=waiting.workflow_id,
            authorization=stale,
            next_plan=plan(
                role="reviewer",
                assignment="assign_reviewer",
                plan_id="plan_reviewer",
            ),
        )


def test_conflicting_duplicate_authorization_is_rejected(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    approved = authorization(waiting)
    coordinator = WorkflowProgressionCoordinator(workflows=store)

    progressed = coordinator.progress(
        project_id=waiting.project_id,
        workflow_id=waiting.workflow_id,
        authorization=approved,
        next_plan=plan(
            role="reviewer",
            assignment="assign_reviewer",
            plan_id="plan_reviewer",
        ),
    )

    conflicting = approved.model_copy(
        update={"reason": "conflicting authority"}
    )

    with pytest.raises(
        WorkflowProgressionError,
        match="conflicting authority",
    ):
        coordinator.progress(
            project_id=progressed.project_id,
            workflow_id=progressed.workflow_id,
            authorization=conflicting,
        )


def test_publication_failure_preserves_durable_progression(
    tmp_path,
) -> None:
    store = GovernedWorkflowStore(tmp_path / "workflows")
    waiting = waiting_workflow(store)

    class FailingVisibility:
        def publish(self, workflow):
            raise RuntimeError("visibility unavailable")

    coordinator = WorkflowProgressionCoordinator(
        workflows=store,
        visibility=FailingVisibility(),
    )

    with pytest.raises(
        WorkflowProgressionPublicationError,
        match="publication failed",
    ):
        coordinator.progress(
            project_id=waiting.project_id,
            workflow_id=waiting.workflow_id,
            authorization=authorization(waiting),
            next_plan=plan(
                role="reviewer",
                assignment="assign_reviewer",
                plan_id="plan_reviewer",
            ),
        )

    persisted = store.get(
        waiting.project_id,
        waiting.workflow_id,
    )
    assert persisted is not None
    assert persisted.version == waiting.version + 1
    assert persisted.state == WorkflowState.ACTIVE
