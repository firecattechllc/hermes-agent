"""Step 8 governed workflow scheduling certification."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermes_cli.agent_roles.models import AssignmentStatus
from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision, GovernedWorkflowService, WorkflowAuthorization,
    WorkflowDecision,
)
from hermes_cli.agent_roles.execution import ExecutionOutcome
from hermes_cli.agent_roles.workflow_execution import (
    EvidenceActorSource, WorkflowExecutionEvidenceService,
    WorkflowRunStatus,
)
from hermes_cli.agent_roles.workflow_execution_store import WorkflowExecutionStore
from hermes_cli.agent_roles.workflow_scheduling import (
    CoordinationStatus, GovernedWorkflowSchedulingCoordinator,
    SchedulingVisibilityError, WorkflowSchedulingError,
)
from hermes_cli.agent_roles.workflow_scheduling_store import WorkflowSchedulingStore
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore

from .test_workflow import plan, result


@dataclass(frozen=True)
class FakeAssignment:
    project_id: str
    assignment_id: str
    role_id: str
    assigned_agent_id: str
    status: AssignmentStatus = AssignmentStatus.ASSIGNED


class FakeRoles:
    def __init__(self, *assignments: FakeAssignment) -> None:
        self.assignments = {item.assignment_id: item for item in assignments}

    def get_assignment(self, project_id: str, assignment_id: str) -> FakeAssignment:
        item = self.assignments[assignment_id]
        if item.project_id != project_id:
            raise KeyError(assignment_id)
        return item


def authorized_advance(tmp_path, *, visibility=None):
    workflow_service = GovernedWorkflowService()
    evidence_service = WorkflowExecutionEvidenceService()
    builder = plan(role="builder", assignment="assign_1", plan_id="plan_1")
    reviewer = plan(role="reviewer", assignment="assign_2", plan_id="plan_2")
    initial = workflow_service.create(builder, created_at=10)
    created, summary = evidence_service.create_run(initial, actor_id="orchestrator", timestamp=10)
    events = [created]
    started = evidence_service.start_node(summary, initial, builder, actor_id="runtime", timestamp=20)
    events.append(started)
    summary = evidence_service.projector.replay(tuple(events))
    completed = evidence_service.complete_node(summary, initial, result(builder), actor_id="runtime", timestamp=30)
    events.append(completed)
    summary = evidence_service.projector.replay(tuple(events))
    waiting = workflow_service.record_result(initial, builder, result(builder), timestamp=31, next_role_id="reviewer")
    requested = evidence_service.request_decision(summary, waiting, actor_id="orchestrator", timestamp=31)
    events.append(requested)
    summary = evidence_service.projector.replay(tuple(events))
    authorization = WorkflowAuthorization(
        authorization_id="auth_advance", workflow_id=waiting.workflow_id,
        project_id=waiting.project_id, expected_version=waiting.version,
        decision=WorkflowDecision.ADVANCE,
        disposition=AuthorizationDecision.APPROVED, actor="human-reviewer",
        reason="review stage explicitly approved", timestamp=40,
        to_role_id="reviewer",
    )
    active = workflow_service.authorize(waiting, authorization, next_plan=reviewer)
    granted = evidence_service.record_authorization(summary, active, authorization)
    events.append(granted)
    summary = evidence_service.projector.replay(tuple(events))

    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    workflow_store.append(initial)
    workflow_store.append(waiting)
    workflow_store.append(active)
    evidence_store = WorkflowExecutionStore(tmp_path / "evidence")
    for event in events:
        evidence_store.append(event)
    roles = FakeRoles(FakeAssignment(
        reviewer.project_id, reviewer.assignment_id, reviewer.role_id, reviewer.agent_id
    ))
    scheduling = WorkflowSchedulingStore(tmp_path / "scheduling", capacity=4)
    coordinator = GovernedWorkflowSchedulingCoordinator(
        roles=roles, workflows=workflow_store, evidence=evidence_store,
        scheduling=scheduling, visibility=visibility,
    )
    return coordinator, scheduling, active, summary, reviewer, authorization, events


def authorized_retry(tmp_path, *, visibility=None):
    workflow_service = GovernedWorkflowService()
    evidence_service = WorkflowExecutionEvidenceService()
    builder = plan(role="builder", assignment="assign_1", plan_id="plan_1")
    retry_plan = plan(
        role="builder", assignment="assign_retry", plan_id="plan_retry"
    )
    initial = workflow_service.create(builder, created_at=10)
    created, summary = evidence_service.create_run(
        initial, actor_id="orchestrator", timestamp=10
    )
    events = [created]
    events.append(evidence_service.start_node(
        summary, initial, builder, actor_id="runtime", timestamp=20
    ))
    summary = evidence_service.projector.replay(tuple(events))
    failed_result = result(builder, ExecutionOutcome.FAILED)
    events.append(evidence_service.complete_node(
        summary, initial, failed_result, actor_id="runtime", timestamp=30
    ))
    summary = evidence_service.projector.replay(tuple(events))
    waiting = workflow_service.record_result(
        initial, builder, failed_result, timestamp=31
    )
    events.append(evidence_service.request_decision(
        summary, waiting, actor_id="orchestrator", timestamp=31
    ))
    summary = evidence_service.projector.replay(tuple(events))
    authorization = WorkflowAuthorization(
        authorization_id="auth_retry",
        workflow_id=waiting.workflow_id,
        project_id=waiting.project_id,
        expected_version=waiting.version,
        decision=WorkflowDecision.RETRY,
        disposition=AuthorizationDecision.APPROVED,
        actor="human-reviewer",
        reason="one retry attempt explicitly approved",
        timestamp=40,
    )
    active = workflow_service.authorize(
        waiting, authorization, next_plan=retry_plan
    )
    events.append(evidence_service.record_authorization(
        summary, active, authorization
    ))
    summary = evidence_service.projector.replay(tuple(events))
    workflow_store = GovernedWorkflowStore(tmp_path / "workflows")
    for workflow in (initial, waiting, active):
        workflow_store.append(workflow)
    evidence_store = WorkflowExecutionStore(tmp_path / "evidence")
    for event in events:
        evidence_store.append(event)
    roles = FakeRoles(FakeAssignment(
        retry_plan.project_id,
        retry_plan.assignment_id,
        retry_plan.role_id,
        retry_plan.agent_id,
    ))
    scheduling = WorkflowSchedulingStore(tmp_path / "scheduling", capacity=4)
    coordinator = GovernedWorkflowSchedulingCoordinator(
        roles=roles,
        workflows=workflow_store,
        evidence=evidence_store,
        scheduling=scheduling,
        visibility=visibility,
    )
    return (
        coordinator, scheduling, active, summary, retry_plan,
        authorization, events,
    )


def schedule_authorized(tmp_path, *, visibility=None):
    app, store, workflow, summary, next_plan, authorization, events = authorized_advance(
        tmp_path, visibility=visibility
    )
    intent = app.schedule(
        project_id=workflow.project_id, workflow_id=workflow.workflow_id,
        run_id=summary.run_id, plan=next_plan,
        authorization_id=authorization.authorization_id,
        actor_id="workflow-coordinator", timestamp=41,
    )
    return app, store, intent, workflow, summary, next_plan, authorization, events


def test_valid_authorized_advance_creates_deterministic_non_executing_intent(tmp_path) -> None:
    app, store, intent, workflow, summary, next_plan, authorization, _ = schedule_authorized(tmp_path)
    assert intent.status == CoordinationStatus.SCHEDULED
    assert (intent.project_id, intent.workflow_id, intent.run_id) == (
        workflow.project_id, workflow.workflow_id, summary.run_id
    )
    assert (intent.assignment_id, intent.plan_id, intent.role_id, intent.agent_id) == (
        next_plan.assignment_id, next_plan.plan_id, next_plan.role_id, next_plan.agent_id
    )
    assert intent.authorization_id == authorization.authorization_id
    assert intent.authorization == authorization
    assert intent.node_run_id == WorkflowExecutionEvidenceService.node_run_id_for(
        summary.run_id, next_plan.assignment_id, next_plan.plan_id
    )
    assert app.list_eligible("project_1", timestamp=41) == (intent,)
    assert store.get("project_1", intent.intent_id) == intent
    duplicate = app.schedule(
        project_id=workflow.project_id, workflow_id=workflow.workflow_id,
        run_id=summary.run_id, plan=next_plan,
        authorization_id=authorization.authorization_id,
        actor_id="workflow-coordinator", timestamp=41,
    )
    assert duplicate == intent


def test_valid_retry_preserves_role_and_uses_new_attempt_identity(tmp_path) -> None:
    app, _, workflow, summary, retry_plan, authorization, _ = authorized_retry(
        tmp_path
    )
    scheduled = app.schedule(
        project_id=workflow.project_id,
        workflow_id=workflow.workflow_id,
        run_id=summary.run_id,
        plan=retry_plan,
        authorization_id=authorization.authorization_id,
        actor_id="coordinator",
        timestamp=41,
    )
    assert scheduled.decision == WorkflowDecision.RETRY
    assert scheduled.role_id == workflow.stages[-2].role_id == "builder"
    assert scheduled.assignment_id == "assign_retry"
    assert scheduled.plan_id == "plan_retry"
    assert scheduled.node_run_id == WorkflowExecutionEvidenceService.node_run_id_for(
        summary.run_id, retry_plan.assignment_id, retry_plan.plan_id
    )
    assert scheduled.attempt_id.startswith("attempt_")
    assert scheduled.attempt_id != summary.nodes[-1].node_run_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "forged-human"),
        ("reason", "different authorization reason"),
        ("timestamp", 39),
        ("to_role_id", "tester"),
        ("decision", WorkflowDecision.RETRY),
    ],
)
def test_partial_authorization_event_forgery_is_rejected(
    tmp_path, field, value
) -> None:
    app, _, workflow, summary, next_plan, authorization, events = (
        authorized_advance(tmp_path)
    )
    forged_events = events[:-1] + [events[-1].model_copy(update={field: value})]

    class ForgedEvidence:
        def get_summary(self, project_id, run_id):
            return summary

        def events_for_run(self, project_id, run_id):
            return tuple(forged_events)

    app._evidence = ForgedEvidence()
    with pytest.raises(WorkflowSchedulingError, match="authorization-granted"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


def test_mismatched_authorization_causation_is_rejected(tmp_path) -> None:
    app, _, workflow, summary, next_plan, authorization, events = (
        authorized_advance(tmp_path)
    )
    forged = events[:-1] + [events[-1].model_copy(update={
        "causation_id": events[0].event_id,
    })]

    class ForgedEvidence:
        def get_summary(self, project_id, run_id):
            return summary

        def events_for_run(self, project_id, run_id):
            return tuple(forged)

    app._evidence = ForgedEvidence()
    with pytest.raises(WorkflowSchedulingError, match="authorization-granted"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


@pytest.mark.parametrize("field,value", [
    ("project_id", "project_2"), ("assignment_id", "other_assignment"),
    ("plan_id", "other_plan"), ("role_id", "tester"), ("agent_id", "other_agent"),
])
def test_schedule_rejects_identity_mismatch(tmp_path, field, value) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(tmp_path)
    bad = next_plan.model_copy(update={field: value})
    with pytest.raises((WorkflowSchedulingError, KeyError), match="project|stage|assignment|authority|other_assignment"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=bad,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=41,
        )


def test_schedule_rejects_missing_stale_or_forged_authorization(tmp_path) -> None:
    app, _, workflow, summary, next_plan, authorization, events = authorized_advance(tmp_path)
    with pytest.raises(WorkflowSchedulingError, match="authorization"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan, authorization_id="forged",
            actor_id="coordinator", timestamp=41,
        )


def test_schedule_rejects_stale_replay_summary(tmp_path) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )
    real_evidence = app._evidence

    class StaleEvidence:
        def get_summary(self, project_id, run_id):
            return summary.model_copy(update={
                "workflow_version": workflow.version - 1,
            })

        def events_for_run(self, project_id, run_id):
            return real_evidence.events_for_run(project_id, run_id)

    app._evidence = StaleEvidence()
    with pytest.raises(WorkflowSchedulingError, match="stale"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=41,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"actor_source": EvidenceActorSource.HUMAN},
        {"workflow_version": 99},
        {"correlation_id": "other_run"},
    ],
)
def test_schedule_rejects_partially_forged_authorization_request_evidence(
    tmp_path, updates
) -> None:
    app, _, workflow, summary, next_plan, authorization, events = (
        authorized_advance(tmp_path)
    )
    forged_events = (
        *events[:-2],
        events[-2].model_copy(update=updates),
        events[-1],
    )

    class ForgedEvidence:
        def get_summary(self, project_id, run_id):
            return summary

        def events_for_run(self, project_id, run_id):
            return forged_events

    app._evidence = ForgedEvidence()
    with pytest.raises(WorkflowSchedulingError, match="matching authorization"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=41,
        )


@pytest.mark.parametrize(
    "updates", [{"actor": "forged"}, {"reason": "forged reason"}]
)
def test_schedule_rejects_workflow_authorization_event_provenance_mismatch(
    tmp_path, updates
) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )
    forged_workflow = workflow.model_copy(update={
        "events": (
            *workflow.events[:-1],
            workflow.events[-1].model_copy(update=updates),
        ),
    })

    class ForgedWorkflows:
        def get(self, project_id, workflow_id):
            return forged_workflow

    app._workflows = ForgedWorkflows()
    with pytest.raises(WorkflowSchedulingError, match="explicit matching"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=41,
        )


@pytest.mark.parametrize(
    "status",
    [
        WorkflowRunStatus.AWAITING_AUTHORIZATION,
        WorkflowRunStatus.SUCCEEDED,
        WorkflowRunStatus.BLOCKED,
        WorkflowRunStatus.CANCELLED,
    ],
)
def test_non_running_replay_states_are_not_schedulable(tmp_path, status) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )

    class IneligibleEvidence:
        def get_summary(self, project_id, run_id):
            return summary.model_copy(update={"status": status})

        def events_for_run(self, project_id, run_id):
            return app._evidence.events_for_run(project_id, run_id)

    original = app._evidence
    proxy = IneligibleEvidence()
    proxy.events_for_run = original.events_for_run
    app._evidence = proxy
    with pytest.raises(WorkflowSchedulingError, match="not eligible"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


def test_active_node_and_historical_stage_mismatch_are_rejected(tmp_path) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )
    running = summary.nodes[-1].model_copy(update={
        "status": "running", "completed_at": None, "result_id": None,
    })

    class ActiveEvidence:
        def get_summary(self, project_id, run_id):
            return summary.model_copy(update={"nodes": (running,)})

        def events_for_run(self, project_id, run_id):
            return ()

    app._evidence = ActiveEvidence()
    with pytest.raises(WorkflowSchedulingError, match="active node"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )

    mismatched_node = summary.nodes[-1].model_copy(update={
        "assignment_id": "wrong_historical_assignment",
    })

    class MismatchedHistory:
        def get_summary(self, project_id, run_id):
            return summary.model_copy(update={"nodes": (mismatched_node,)})

        def events_for_run(self, project_id, run_id):
            return ()

    app._evidence = MismatchedHistory()
    with pytest.raises(WorkflowSchedulingError, match="history"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


@pytest.mark.parametrize(
    "state,decision,disposition",
    [
        ("completed", WorkflowDecision.PROMOTE, AuthorizationDecision.APPROVED),
        ("cancelled", WorkflowDecision.CANCEL, AuthorizationDecision.APPROVED),
        ("blocked", WorkflowDecision.ADVANCE, AuthorizationDecision.DENIED),
    ],
)
def test_terminal_promote_cancel_and_denied_authority_are_rejected(
    tmp_path, state, decision, disposition
) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )
    terminal = workflow.model_copy(update={
        "state": state,
        "authorizations": (
            authorization.model_copy(update={
                "decision": decision,
                "disposition": disposition,
                "to_role_id": None if decision != WorkflowDecision.ADVANCE else "reviewer",
            }),
        ),
    })

    class TerminalWorkflows:
        def get(self, project_id, workflow_id):
            return terminal

    app._workflows = TerminalWorkflows()
    with pytest.raises(WorkflowSchedulingError, match="not eligible"):
        app.schedule(
            project_id=workflow.project_id,
            workflow_id=workflow.workflow_id,
            run_id=summary.run_id,
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


@pytest.mark.parametrize(
    "project_id,workflow_id,run_id",
    [
        ("project_2", "ignored", "ignored"),
        ("project_1", "workflow_missing", "ignored"),
        ("project_1", "ignored", "run_missing"),
    ],
)
def test_missing_project_workflow_or_run_is_rejected(
    tmp_path, project_id, workflow_id, run_id
) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(
        tmp_path
    )
    with pytest.raises(WorkflowSchedulingError, match="durable workflow"):
        app.schedule(
            project_id=project_id,
            workflow_id=(workflow.workflow_id if workflow_id == "ignored" else workflow_id),
            run_id=(summary.run_id if run_id == "ignored" else run_id),
            plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator",
            timestamp=41,
        )


def test_claim_lifecycle_requires_identity_and_expired_lease(tmp_path) -> None:
    app, _, intent, *_ = schedule_authorized(tmp_path)
    claimed = app.claim("project_1", intent.intent_id, claimed_by="worker-1", timestamp=42, lease_seconds=10)
    assert claimed.status == CoordinationStatus.CLAIMED
    with pytest.raises(ValueError, match="not claimable"):
        app.claim("project_1", intent.intent_id, claimed_by="worker-2", timestamp=42, lease_seconds=10)
    with pytest.raises(WorkflowSchedulingError, match="current"):
        app.expire_claim("project_1", intent.intent_id, actor_id="coordinator", timestamp=51, reason="too early", expected_claim_id=claimed.claim_id or "")
    expired = app.expire_claim("project_1", intent.intent_id, actor_id="coordinator", timestamp=52, reason="lease elapsed", expected_claim_id=claimed.claim_id or "")
    assert expired.status == CoordinationStatus.EXPIRED
    with pytest.raises(ValueError, match="immutable"):
        app.cancel("project_1", intent.intent_id, actor_id="human", timestamp=53, reason="late cancel")


def test_stale_claimant_and_backdated_schedule_fail_closed(tmp_path) -> None:
    app, _, workflow, summary, next_plan, authorization, _ = authorized_advance(tmp_path)
    with pytest.raises(WorkflowSchedulingError, match="timestamp"):
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=39,
        )
    intent = app.schedule(
        project_id=workflow.project_id, workflow_id=workflow.workflow_id,
        run_id=summary.run_id, plan=next_plan,
        authorization_id=authorization.authorization_id,
        actor_id="coordinator", timestamp=41,
    )
    claimed = app.claim(
        "project_1", intent.intent_id, claimed_by="worker",
        timestamp=42, lease_seconds=2,
    )
    with pytest.raises(ValueError, match="lease has expired"):
        app.complete(
            "project_1", intent.intent_id, actor_id="worker", timestamp=44,
            reason="late completion", evidence_refs=("result_1",),
            expected_claim_id=claimed.claim_id or "",
        )


def test_defer_cancel_refuse_complete_and_illegal_transitions(tmp_path) -> None:
    app, _, first, *_ = schedule_authorized(tmp_path)
    deferred = app.defer("project_1", first.intent_id, actor_id="operator", timestamp=42, available_at=50, reason="capacity gate")
    assert app.list_eligible("project_1", timestamp=49) == ()
    claimed = app.claim("project_1", first.intent_id, claimed_by="worker", timestamp=50, lease_seconds=10)
    with pytest.raises(ValueError, match="claim identity"):
        app.complete("project_1", first.intent_id, actor_id="worker", timestamp=51, reason="done", evidence_refs=("result_1",), expected_claim_id="wrong")
    done = app.complete("project_1", first.intent_id, actor_id="worker", timestamp=51, reason="done", evidence_refs=("result_1",), expected_claim_id=claimed.claim_id or "")
    assert done.status == CoordinationStatus.COMPLETED
    assert done.causation_id == claimed.fingerprint


def test_invalid_lease_completion_evidence_and_timestamp_rollback(tmp_path) -> None:
    app, _, intent, *_ = schedule_authorized(tmp_path)
    for lease_seconds in (0, 86_401):
        with pytest.raises(ValueError, match="lease"):
            app.claim(
                "project_1", intent.intent_id, claimed_by="worker",
                timestamp=42, lease_seconds=lease_seconds,
            )
    claimed = app.claim(
        "project_1", intent.intent_id, claimed_by="worker",
        timestamp=42, lease_seconds=10,
    )
    with pytest.raises(WorkflowSchedulingError, match="evidence"):
        app.complete(
            "project_1", intent.intent_id, actor_id="worker", timestamp=43,
            reason="done", evidence_refs=(),
            expected_claim_id=claimed.claim_id or "",
        )
    with pytest.raises(ValueError, match="backwards"):
        app.defer(
            "project_1", intent.intent_id, actor_id="worker", timestamp=41,
            available_at=50, reason="rollback",
            expected_claim_id=claimed.claim_id,
        )
    with pytest.raises(ValueError, match="future"):
        app.defer(
            "project_1", intent.intent_id, actor_id="worker", timestamp=43,
            available_at=43, reason="not actually deferred",
            expected_claim_id=claimed.claim_id,
        )


def test_refusal_and_cancellation_never_execute_or_retry(tmp_path) -> None:
    app, store, first, *_ = schedule_authorized(tmp_path)
    refused = app.refuse(
        "project_1", first.intent_id, actor_id="policy",
        timestamp=42, reason="runtime precondition refused",
    )
    assert refused.status == CoordinationStatus.REFUSED
    assert store.list("project_1", status=CoordinationStatus.SCHEDULED) == ()
    assert not hasattr(app, "execute")
    assert not hasattr(app, "retry")
    cancel_app, _, cancel_intent, *_ = schedule_authorized(tmp_path / "cancel")
    cancelled = cancel_app.cancel(
        "project_1", cancel_intent.intent_id, actor_id="human",
        timestamp=42, reason="operator cancelled intent",
    )
    assert cancelled.status == CoordinationStatus.CANCELLED


def test_visibility_failure_is_persistence_first_and_reconcilable(tmp_path) -> None:
    class FailingVisibility:
        def publish(self, intent):
            raise RuntimeError("mission unavailable")

    app, store, workflow, summary, next_plan, authorization, _ = authorized_advance(tmp_path, visibility=FailingVisibility())
    with pytest.raises(SchedulingVisibilityError) as captured:
        app.schedule(
            project_id=workflow.project_id, workflow_id=workflow.workflow_id,
            run_id=summary.run_id, plan=next_plan,
            authorization_id=authorization.authorization_id,
            actor_id="coordinator", timestamp=41,
        )
    assert store.get("project_1", captured.value.intent.intent_id) is not None

    from hermes_cli.agent_roles.workflow_scheduling_visibility import WorkflowSchedulingVisibilityService
    from hermes_cli.mission_control.service import MissionControlService
    from hermes_cli.mission_control.store import MissionControlStore

    recovered = WorkflowSchedulingVisibilityService(
        MissionControlService(store=MissionControlStore(tmp_path / "mission-recovered"))
    )
    app._visibility = recovered
    with pytest.raises(WorkflowSchedulingError, match="reconcile"):
        app.claim(
            "project_1", captured.value.intent.intent_id,
            claimed_by="worker", timestamp=42, lease_seconds=10,
        )
    assert app.reconcile_visibility("project_1") == (captured.value.intent,)
    assert recovered.list_records("project_1")[0].intent_id == captured.value.intent.intent_id
    count = recovered._mission_control.event_count("project_1")
    assert app.reconcile_visibility("project_1") == (captured.value.intent,)
    assert recovered._mission_control.event_count("project_1") == count


def test_same_version_visibility_payload_cannot_authorize_progression(
    tmp_path,
) -> None:
    app, _, scheduled, *_ = schedule_authorized(tmp_path)
    from hermes_cli.agent_roles.workflow_scheduling_visibility import (
        WorkflowSchedulingVisibilityAdapter,
    )

    forged = scheduled.model_copy(update={"actor_id": "forged-coordinator"})
    record = WorkflowSchedulingVisibilityAdapter().from_events((
        WorkflowSchedulingVisibilityAdapter().to_event(forged),
    ))[0]

    class ForgedVisibility:
        def list_records(self, project_id):
            return (record,)

        def publish(self, intent):
            raise AssertionError("claim must not progress")

    app._visibility = ForgedVisibility()
    with pytest.raises(WorkflowSchedulingError, match="stale"):
        app.claim(
            "project_1", scheduled.intent_id,
            claimed_by="worker", timestamp=42, lease_seconds=10,
        )
