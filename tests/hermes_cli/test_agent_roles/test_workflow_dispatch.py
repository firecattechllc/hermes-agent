"""Step 9 governed workflow dispatch admission certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.launch_validation import RuntimeCompatibility
from hermes_cli.agent_roles.models import Assignment, AssignmentStatus, builtin_agent_roles
from hermes_cli.agent_roles.runtime_handoff import (
    DeterministicDryRunAdapter,
    RuntimeHandoffService,
)
from hermes_cli.agent_roles.workflow_dispatch import (
    GovernedWorkflowDispatchCoordinator,
    WorkflowDispatchError,
    WorkflowDispatchStatus,
    WorkflowDispatchVisibilityError,
)
from hermes_cli.agent_roles.workflow_dispatch_store import WorkflowDispatchStore

from .test_workflow_scheduling import schedule_authorized


class Roles:
    def __init__(self, assignment):
        self.assignment = assignment
        self.role = next(role for role in builtin_agent_roles() if role.role_id == assignment.role_id)

    def get_assignment(self, project_id, assignment_id):
        if (project_id, assignment_id) != (
            self.assignment.project_id, self.assignment.assignment_id
        ):
            raise KeyError(assignment_id)
        return self.assignment

    def get_role(self, project_id, role_id):
        if (project_id, role_id) != (self.assignment.project_id, self.role.role_id):
            raise KeyError(role_id)
        return self.role


def prepared_app(tmp_path, *, visibility=None):
    scheduling_app, scheduling, intent, workflow, summary, plan, _, _ = (
        schedule_authorized(tmp_path)
    )
    claimed = scheduling_app.claim(
        intent.project_id, intent.intent_id, claimed_by="dispatch-worker",
        timestamp=42, lease_seconds=30,
    )
    assignment = Assignment(
        assignment_id=plan.assignment_id, project_id=plan.project_id,
        role_id=plan.role_id, assigned_agent_id=plan.agent_id,
        status=AssignmentStatus.ACCEPTED,
        instructions="Review the governed workflow evidence.",
        created_at=10, updated_at=40,
        metadata={
            "repository_root": "/repo/project_1",
            "runtime": "hermes-agent",
        },
    )
    store = WorkflowDispatchStore(tmp_path / "dispatch")
    app = GovernedWorkflowDispatchCoordinator(
        roles=Roles(assignment), workflows=scheduling_app._workflows,
        evidence=scheduling_app._evidence, scheduling=scheduling_app,
        dispatches=store,
        handoff=RuntimeHandoffService(DeterministicDryRunAdapter()),
        visibility=visibility,
    )
    compatibility = RuntimeCompatibility(runtime="hermes-agent")
    return app, store, scheduling, claimed, plan, compatibility


def prepare(app, claimed, plan, compatibility, *, timestamp=43):
    return app.prepare(
        project_id=claimed.project_id, intent_id=claimed.intent_id,
        expected_claim_id=claimed.claim_id or "", plan=plan,
        compatibility=compatibility, repository_root="/repo/project_1",
        runtime="hermes-agent", actor_id="dispatch-worker", timestamp=timestamp,
    )


def test_claimed_authorized_intent_prepares_ready_nonexecuting_session(tmp_path) -> None:
    app, store, scheduling, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(app, claimed, plan, compatibility)
    assert outcome.status == WorkflowDispatchStatus.PREPARED
    assert outcome.session is not None
    assert outcome.session.state.value == "ready"
    assert outcome.session.execution_started is False
    assert outcome.receipt is not None and outcome.receipt.execution_started is False
    assert store.get(outcome.project_id, outcome.dispatch_id) == outcome
    coordinated = scheduling.get(outcome.project_id, outcome.intent_id)
    assert coordinated.status.value == "completed"
    assert outcome.dispatch_id in coordinated.evidence_refs


def test_dispatch_is_idempotent_after_durable_coordination_completion(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    first = prepare(app, claimed, plan, compatibility)
    second = prepare(app, claimed, plan, compatibility)
    assert second == first
    assert store.list(claimed.project_id) == (first,)


def test_durable_retry_rejects_forged_plan_and_launch_inputs(tmp_path) -> None:
    app, _, _, claimed, plan, compatibility = prepared_app(tmp_path)
    prepare(app, claimed, plan, compatibility)
    forged = plan.model_copy(update={"agent_id": "forged-agent"})
    with pytest.raises(WorkflowDispatchError, match="retry identity"):
        prepare(app, claimed, forged, compatibility)
    with pytest.raises(WorkflowDispatchError, match="retry identity"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claimed.claim_id or "", plan=plan,
            compatibility=compatibility, repository_root="/forged/repository",
            runtime="hermes-agent", actor_id="dispatch-worker", timestamp=44,
        )


@pytest.mark.parametrize("claim", ["wrong", ""])
def test_forged_claim_fails_closed_without_persistence(tmp_path, claim) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="claim identity"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claim, plan=plan, compatibility=compatibility,
            repository_root="/repo/project_1", runtime="hermes-agent",
            actor_id="worker", timestamp=43,
        )
    assert store.list(claimed.project_id) == ()


def test_expired_claim_fails_closed(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="expired"):
        prepare(app, claimed, plan, compatibility, timestamp=72)
    assert store.list(claimed.project_id) == ()


def test_plan_forgery_fails_closed(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    forged = plan.model_copy(update={"agent_id": "forged-agent"})
    with pytest.raises(WorkflowDispatchError, match="plan does not match"):
        prepare(app, claimed, forged, compatibility)
    assert store.list(claimed.project_id) == ()


def test_authorization_event_field_forgery_fails_closed(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    evidence = app._evidence

    class ForgedEvidence:
        def get_summary(self, project_id, run_id):
            return evidence.get_summary(project_id, run_id)

        def events_for_run(self, project_id, run_id):
            events = evidence.events_for_run(project_id, run_id)
            return events[:-1] + (
                events[-1].model_copy(update={"reason": "forged grant reason"}),
            )

    app._evidence = ForgedEvidence()
    with pytest.raises(WorkflowDispatchError, match="provenance mismatch"):
        prepare(app, claimed, plan, compatibility)
    assert store.list(claimed.project_id) == ()


def test_secret_environment_fails_closed_without_persistence(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="must not contain secrets"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claimed.claim_id or "", plan=plan,
            compatibility=compatibility, repository_root="/repo/project_1",
            runtime="hermes-agent", actor_id="dispatch-worker", timestamp=43,
            environment=(("API_KEY", "secret-value"),),
        )
    assert store.list(claimed.project_id) == ()


def test_nonsecret_environment_value_is_not_persisted(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="must not be persisted"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claimed.claim_id or "", plan=plan,
            compatibility=compatibility, repository_root="/repo/project_1",
            runtime="hermes-agent", actor_id="dispatch-worker", timestamp=43,
            environment=(("MODE", "governed"),),
        )
    assert store.list(claimed.project_id) == ()


def test_launch_selection_must_match_durable_assignment_authority(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="exceeds durable assignment"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claimed.claim_id or "", plan=plan,
            compatibility=compatibility, repository_root="/forged/repository",
            runtime="hermes-agent", actor_id="dispatch-worker", timestamp=43,
        )
    assert store.list(claimed.project_id) == ()


def test_dispatch_actor_and_timestamp_are_bound_to_claim(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    with pytest.raises(WorkflowDispatchError, match="actor must equal claimed_by"):
        app.prepare(
            project_id=claimed.project_id, intent_id=claimed.intent_id,
            expected_claim_id=claimed.claim_id or "", plan=plan,
            compatibility=compatibility, repository_root="/repo/project_1",
            runtime="hermes-agent", actor_id="forged-worker", timestamp=43,
        )
    with pytest.raises(WorkflowDispatchError, match="timestamp predates claim"):
        prepare(app, claimed, plan, compatibility, timestamp=41)
    assert store.list(claimed.project_id) == ()


def test_incompatible_runtime_records_refusal_and_refuses_coordination(tmp_path) -> None:
    app, store, scheduling, claimed, plan, _ = prepared_app(tmp_path)
    incompatible = RuntimeCompatibility(runtime="different-runtime")
    outcome = prepare(app, claimed, plan, incompatible)
    assert outcome.status == WorkflowDispatchStatus.REFUSED
    assert outcome.session is None and outcome.receipt is None
    assert store.get(claimed.project_id, outcome.dispatch_id) == outcome
    assert scheduling.get(claimed.project_id, claimed.intent_id).status.value == "refused"


def test_persistence_precedes_visibility_and_reconciliation_is_idempotent(tmp_path) -> None:
    class FailingVisibility:
        def __init__(self):
            self.fail = True
            self.items = []

        def publish(self, outcome):
            if self.fail:
                raise RuntimeError("offline")
            self.items.append(outcome)

    visibility = FailingVisibility()
    app, store, scheduling, claimed, plan, compatibility = prepared_app(
        tmp_path, visibility=visibility
    )
    with pytest.raises(WorkflowDispatchVisibilityError) as exc:
        prepare(app, claimed, plan, compatibility)
    outcome = exc.value.outcome
    assert store.get(claimed.project_id, outcome.dispatch_id) == outcome
    assert scheduling.get(claimed.project_id, claimed.intent_id).status.value == "claimed"
    visibility.fail = False
    assert app.reconcile_visibility(claimed.project_id, outcome.dispatch_id) == outcome
    assert visibility.items == [outcome]
    assert scheduling.get(claimed.project_id, claimed.intent_id).status.value == "completed"
