"""Step 7 workflow execution evidence and replay certification."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision,
    GovernedWorkflowService,
    WorkflowAuthorization,
    WorkflowDecision,
)
from hermes_cli.agent_roles.workflow_execution import (
    EvidenceActorSource,
    ExecutionEventType,
    WorkflowExecutionEvidenceService,
    WorkflowExecutionEvent,
    WorkflowExecutionProjector,
    WorkflowRunStatus,
)

from .test_workflow import plan, result


def append(projector, events, event):
    events = events + (event,)
    return events, projector.replay(events)


def release_run():
    workflow_service = GovernedWorkflowService()
    evidence_service = WorkflowExecutionEvidenceService()
    projector = evidence_service.projector
    execution_plan = plan(role="release")
    execution_result = result(execution_plan)
    workflow = workflow_service.create(execution_plan, created_at=10)
    created, summary = evidence_service.create_run(
        workflow,
        actor_id="workflow-orchestrator",
        timestamp=10,
    )
    events = (created,)
    started = evidence_service.start_node(
        summary,
        workflow,
        execution_plan,
        actor_id="runtime-adapter",
        timestamp=20,
    )
    events, summary = append(projector, events, started)
    completed = evidence_service.complete_node(
        summary,
        workflow,
        execution_result,
        actor_id="runtime-adapter",
        timestamp=execution_result.completed_at,
    )
    events, summary = append(projector, events, completed)
    waiting = workflow_service.record_result(
        workflow,
        execution_plan,
        execution_result,
        timestamp=31,
    )
    requested = evidence_service.request_decision(
        summary,
        waiting,
        actor_id="workflow-orchestrator",
        timestamp=31,
    )
    events, summary = append(projector, events, requested)
    authorization = WorkflowAuthorization(
        authorization_id="authorization_release",
        workflow_id=waiting.workflow_id,
        project_id=waiting.project_id,
        expected_version=waiting.version,
        decision=WorkflowDecision.PROMOTE,
        disposition=AuthorizationDecision.APPROVED,
        actor="release-manager",
        reason="release evidence reviewed and explicitly approved",
        timestamp=40,
    )
    governed_complete = workflow_service.authorize(waiting, authorization)
    granted = evidence_service.record_authorization(
        summary,
        governed_complete,
        authorization,
    )
    events, summary = append(projector, events, granted)
    terminal = evidence_service.terminal_event(
        summary,
        governed_complete,
        event_type=ExecutionEventType.RUN_COMPLETED,
        actor_id="workflow-orchestrator",
        actor_source=EvidenceActorSource.ORCHESTRATOR,
        timestamp=41,
    )
    events, summary = append(projector, events, terminal)
    return events, summary, governed_complete


def test_authorized_promotion_replays_to_success() -> None:
    events, summary, workflow = release_run()
    assert summary.status == WorkflowRunStatus.SUCCEEDED
    assert summary.workflow_id == workflow.workflow_id
    assert summary.event_count == 6
    assert len(summary.nodes) == 1
    assert summary.nodes[0].status.value == "succeeded"
    assert summary.last_authorized_decision == WorkflowDecision.PROMOTE
    assert events[-1].causation_id == events[-2].event_id


def test_event_and_run_identifiers_are_deterministic() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    service = WorkflowExecutionEvidenceService()
    first, first_summary = service.create_run(
        workflow, actor_id="orchestrator", timestamp=10
    )
    second, second_summary = service.create_run(
        workflow, actor_id="orchestrator", timestamp=10
    )
    assert first == second
    assert first_summary == second_summary
    assert first.fingerprint == second.fingerprint
    assert first_summary.fingerprint == second_summary.fingerprint


def test_node_completion_cannot_precede_start() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    service = WorkflowExecutionEvidenceService()
    _, summary = service.create_run(workflow, actor_id="orchestrator", timestamp=10)
    with pytest.raises(ValueError, match="active node"):
        service.complete_node(
            summary,
            workflow,
            result(plan(role="release")),
            actor_id="runtime",
            timestamp=30,
        )


def test_replay_rejects_broken_causation_chain() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    service = WorkflowExecutionEvidenceService()
    created, summary = service.create_run(workflow, actor_id="orchestrator", timestamp=10)
    started = service.start_node(
        summary,
        workflow,
        plan(role="release"),
        actor_id="runtime",
        timestamp=20,
    ).model_copy(update={"causation_id": "wrong_event"})
    with pytest.raises(ValueError, match="causation"):
        WorkflowExecutionProjector().replay((created, started))


def test_authorization_must_match_pending_governance_decision() -> None:
    events, _, _ = release_run()
    wrong = events[4].model_copy(update={"decision": WorkflowDecision.RETRY})
    with pytest.raises(ValueError, match="does not match"):
        WorkflowExecutionProjector().replay(events[:4] + (wrong,))


def test_run_completion_without_promotion_authorization_is_rejected() -> None:
    events, _, _ = release_run()
    with pytest.raises(ValueError, match="authorized promotion"):
        WorkflowExecutionProjector().replay(events[:3] + (events[-1].model_copy(
            update={
                "sequence": 4,
                "causation_id": events[2].event_id,
            }
        ),))


def test_event_rejects_secret_bearing_reason() -> None:
    with pytest.raises(ValidationError, match="must not contain secrets"):
        WorkflowExecutionEvent(
            event_id="workflow_event_secret",
            event_type=ExecutionEventType.RUN_FAILED,
            sequence=2,
            project_id="project_1",
            workflow_id="workflow_1",
            workflow_version=1,
            run_id="run_1",
            actor_id="system",
            actor_source=EvidenceActorSource.SYSTEM,
            timestamp=20,
            correlation_id="run_1",
            causation_id="event_1",
            reason="token=do-not-store",
        )


def test_cross_project_event_replay_is_rejected() -> None:
    events, _, _ = release_run()
    foreign = events[1].model_copy(update={"project_id": "project_2"})
    with pytest.raises(ValueError, match="cross run or project"):
        WorkflowExecutionProjector().replay((events[0], foreign))


def test_retry_requires_distinct_retry_request_event() -> None:
    with pytest.raises(ValidationError, match="retry decisions require"):
        WorkflowExecutionEvent(
            event_id="workflow_event_retry",
            event_type=ExecutionEventType.TRANSITION_REQUESTED,
            sequence=2,
            project_id="project_1",
            workflow_id="workflow_1",
            workflow_version=1,
            run_id="run_1",
            actor_id="orchestrator",
            actor_source=EvidenceActorSource.ORCHESTRATOR,
            timestamp=20,
            correlation_id="run_1",
            causation_id="event_1",
            decision=WorkflowDecision.RETRY,
        )


def test_terminal_evidence_cannot_leave_active_node() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    service = WorkflowExecutionEvidenceService()
    created, summary = service.create_run(
        workflow, actor_id="orchestrator", timestamp=10
    )
    started = service.start_node(
        summary,
        workflow,
        plan(role="release"),
        actor_id="runtime",
        timestamp=20,
    )
    failed = WorkflowExecutionEvent(
        event_id="workflow_event_failed",
        event_type=ExecutionEventType.RUN_FAILED,
        sequence=3,
        project_id=workflow.project_id,
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        run_id=summary.run_id,
        actor_id="orchestrator",
        actor_source=EvidenceActorSource.ORCHESTRATOR,
        timestamp=21,
        correlation_id=summary.run_id,
        causation_id=started.event_id,
        reason="runtime failed",
    )
    with pytest.raises(ValueError, match="active node"):
        WorkflowExecutionProjector().replay((created, started, failed))


def test_terminal_event_must_match_governed_workflow_state() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    service = WorkflowExecutionEvidenceService()
    _, summary = service.create_run(workflow, actor_id="orchestrator", timestamp=10)
    with pytest.raises(ValueError, match="workflow state"):
        service.terminal_event(
            summary,
            workflow,
            event_type=ExecutionEventType.RUN_COMPLETED,
            actor_id="orchestrator",
            actor_source=EvidenceActorSource.ORCHESTRATOR,
            timestamp=20,
        )


def test_unrecorded_authorization_is_rejected() -> None:
    events, _, complete = release_run()
    summary = WorkflowExecutionProjector().replay(events[:4])
    fabricated = WorkflowAuthorization(
        authorization_id="fabricated",
        workflow_id=complete.workflow_id,
        project_id=complete.project_id,
        expected_version=2,
        decision=WorkflowDecision.PROMOTE,
        disposition=AuthorizationDecision.APPROVED,
        actor="human",
        reason="not actually recorded",
        timestamp=40,
    )
    with pytest.raises(ValueError, match="not recorded"):
        WorkflowExecutionEvidenceService().record_authorization(
            summary,
            complete,
            fabricated,
        )


def test_conflicting_authorization_with_recorded_id_is_rejected() -> None:
    events, _, complete = release_run()
    summary = WorkflowExecutionProjector().replay(events[:4])
    recorded = complete.authorizations[-1]
    conflicting = recorded.model_copy(update={"actor": "different-actor"})
    with pytest.raises(ValueError, match="not recorded"):
        WorkflowExecutionEvidenceService().record_authorization(
            summary,
            complete,
            conflicting,
        )
