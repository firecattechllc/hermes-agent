"""Step 10 governed runtime execution lifecycle certification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.agent_roles.execution import (
    ExecutionEvidence,
    ExecutionOutcome,
    FailureCategory,
    PolicyDecision,
)
from hermes_cli.agent_roles.runtime_execution import (
    GovernedRuntimeExecutionCoordinator,
    RuntimeExecutionError,
    RuntimeExecutionPublicationError,
    RuntimeExecutionState,
    RuntimeExecutionRecord,
)
from hermes_cli.agent_roles.runtime_execution_store import RuntimeExecutionStore
from hermes_cli.agent_roles.workflow_execution_store import WorkflowExecutionRecorder

from .test_workflow_dispatch import prepare, prepared_app


def runtime_app(tmp_path, *, visibility=None, record_step7=True):
    dispatch_app, dispatch_store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(dispatch_app, claimed, plan, compatibility)
    store = RuntimeExecutionStore(tmp_path / "runtime-execution")
    recorder = (
        WorkflowExecutionRecorder(dispatch_app._evidence) if record_step7 else None
    )
    app = GovernedRuntimeExecutionCoordinator(
        roles=dispatch_app._roles, dispatches=dispatch_store, scheduling=dispatch_app._scheduling,
        workflows=dispatch_app._workflows,
        workflow_evidence=dispatch_app._evidence, executions=store,
        workflow_recorder=recorder, visibility=visibility,
    )
    return app, store, outcome, plan, dispatch_app


def admit(app, outcome, plan, *, timestamp=44):
    return app.admit(
        project_id=outcome.project_id, dispatch_id=outcome.dispatch_id,
        plan=plan, actor_id=outcome.claimed_by, timestamp=timestamp,
    )


def start(app, record, plan, *, timestamp=45):
    return app.start(
        project_id=record.project_id, execution_id=record.execution_id,
        plan=plan, actor_id=record.actor_id, timestamp=timestamp,
    )


def required_evidence(plan, *, timestamp=46):
    kinds = tuple(dict.fromkeys(
        kind for step in plan.steps for kind in step.required_evidence
    ))
    return tuple(
        ExecutionEvidence(
            evidence_id=f"evidence_{index}", evidence_type=kind,
            action=plan.allowed_actions[0], attempted=f"perform {kind}",
            output_summary=f"bounded {kind} result", timestamp=timestamp,
            successful=True, policy_decision=PolicyDecision.ALLOWED,
        )
        for index, kind in enumerate(kinds)
    )


def test_prepared_dispatch_admits_starts_and_succeeds_without_execution(tmp_path) -> None:
    app, store, outcome, plan, dispatch_app = runtime_app(tmp_path)
    ready = admit(app, outcome, plan)
    assert ready.state == RuntimeExecutionState.READY
    assert ready.session_id == outcome.session.session_id
    assert ready.runtime == outcome.contract.environment.runtime
    running = start(app, ready, plan)
    assert running.state == RuntimeExecutionState.RUNNING
    assert running.session.state.value == "running"
    summary = dispatch_app._evidence.get_summary(outcome.project_id, outcome.run_id)
    assert summary.nodes[-1].node_run_id == running.node_run_id
    terminal = app.complete(
        project_id=running.project_id, execution_id=running.execution_id,
        plan=plan, actor_id=running.actor_id, timestamp=46,
        outcome=ExecutionOutcome.SUCCEEDED, summary="execution succeeded",
        evidence=required_evidence(plan),
    )
    assert terminal.state == RuntimeExecutionState.SUCCEEDED
    assert terminal.session.state.value == "succeeded"
    assert terminal.result.retry.automatic is False
    assert store.history(terminal.project_id, terminal.execution_id) == (
        ready, running, terminal,
    )
    summary = dispatch_app._evidence.get_summary(outcome.project_id, outcome.run_id)
    assert summary.nodes[-1].status.value == "succeeded"


def test_admission_is_idempotent_and_rejects_forged_authority(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path)
    first = admit(app, outcome, plan)
    assert admit(app, outcome, plan) == first
    forged = plan.model_copy(update={"agent_id": "forged-agent"})
    with pytest.raises(RuntimeExecutionError, match="association"):
        admit(app, outcome, forged)
    with pytest.raises(RuntimeExecutionError, match="actor"):
        app.admit(
            project_id=outcome.project_id, dispatch_id=outcome.dispatch_id,
            plan=plan, actor_id="forged-worker", timestamp=44,
        )
    assert store.list(outcome.project_id) == (first,)


def test_refused_and_cross_project_dispatches_fail_closed(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path)
    refused = outcome.model_copy(update={
        "dispatch_id": "dispatch_refused", "status": "refused",
        "receipt": None, "session": None, "reason": "runtime refused",
    })

    class RefusedStore:
        def get(self, project_id, dispatch_id):
            return refused if (project_id, dispatch_id) == (
                refused.project_id, refused.dispatch_id
            ) else None

    original = app._dispatches
    app._dispatches = RefusedStore()
    with pytest.raises(RuntimeExecutionError, match="PREPARED"):
        app.admit(
            project_id=outcome.project_id, dispatch_id=refused.dispatch_id,
            plan=plan, actor_id=outcome.claimed_by, timestamp=44,
        )
    app._dispatches = original
    with pytest.raises(RuntimeExecutionError, match="PREPARED"):
        app.admit(
            project_id="another-project", dispatch_id=outcome.dispatch_id,
            plan=plan, actor_id=outcome.claimed_by, timestamp=44,
        )
    assert store.list(outcome.project_id) == ()


def test_duplicate_and_concurrent_start_allow_exactly_one_transition(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start, app, ready, plan) for _ in range(2)]
    results, errors = [], []
    for future in futures:
        try:
            results.append(future.result())
        except (RuntimeExecutionError, ValueError) as exc:
            errors.append(exc)
    assert len(results) == 1
    assert len(errors) == 1
    assert len(store.history(outcome.project_id, ready.execution_id)) == 2
    with pytest.raises(RuntimeExecutionError, match="only ready"):
        start(app, ready, plan, timestamp=46)


def test_heartbeat_cancellation_and_terminal_immutability(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    running = start(app, admit(app, outcome, plan), plan)
    heartbeat = app.heartbeat(
        project_id=running.project_id, execution_id=running.execution_id,
        plan=plan, actor_id=running.actor_id, timestamp=46,
    )
    cancelled = app.cancel(
        project_id=heartbeat.project_id, execution_id=heartbeat.execution_id,
        plan=plan, actor_id=heartbeat.actor_id, timestamp=47,
        summary="explicit cancellation", blocking_reasons=(),
    )
    assert cancelled.state == RuntimeExecutionState.CANCELLED
    assert cancelled.last_heartbeat_at == 46
    with pytest.raises(RuntimeExecutionError, match="only running"):
        app.complete(
            project_id=cancelled.project_id, execution_id=cancelled.execution_id,
            plan=plan, actor_id=cancelled.actor_id, timestamp=48,
            outcome=ExecutionOutcome.FAILED, summary="double terminalization",
            evidence=(), failure_category=FailureCategory.EXECUTION,
        )
    assert store.get(cancelled.project_id, cancelled.execution_id) == cancelled


def test_explicit_failure_records_bounded_result_without_automatic_retry(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    running = start(app, admit(app, outcome, plan), plan)
    failed = app.complete(
        project_id=running.project_id, execution_id=running.execution_id,
        plan=plan, actor_id=running.actor_id, timestamp=46,
        outcome=ExecutionOutcome.FAILED, summary="provider boundary reported failure",
        evidence=(), failure_category=FailureCategory.EXECUTION,
    )
    assert failed.state == RuntimeExecutionState.FAILED
    assert failed.result.retry.eligible is True
    assert failed.result.retry.requires_approval is True
    assert failed.result.retry.automatic is False


def test_persistence_precedes_visibility_failure_and_reconciliation(tmp_path) -> None:
    class Visibility:
        def __init__(self):
            self.fail = True
            self.items = []

        def publish(self, record):
            if self.fail:
                raise RuntimeError("offline")
            self.items.append(record)

    visibility = Visibility()
    app, store, outcome, plan, _ = runtime_app(
        tmp_path, visibility=visibility, record_step7=False
    )
    with pytest.raises(RuntimeExecutionPublicationError) as exc:
        admit(app, outcome, plan)
    record = exc.value.record
    assert store.get(record.project_id, record.execution_id) == record
    visibility.fail = False
    assert app.reconcile(record.project_id, record.execution_id, plan=plan, actor_id=record.actor_id, timestamp=44) == record
    assert visibility.items == [record]


def test_step7_failure_is_reconcilable_after_start_persistence(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)

    class FailingRecorder:
        def record(self, event):
            raise RuntimeError("offline")

    app._workflow_recorder = FailingRecorder()
    with pytest.raises(RuntimeExecutionPublicationError, match="Step 7") as exc:
        start(app, ready, plan)
    running = exc.value.record
    assert store.get(running.project_id, running.execution_id) == running
    app._workflow_recorder = WorkflowExecutionRecorder(app._workflow_evidence)
    assert app.reconcile(running.project_id, running.execution_id, plan=plan, actor_id=running.actor_id, timestamp=45) == running


def test_policy_denial_is_recorded_as_governed_terminal_state(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    running = start(app, admit(app, outcome, plan), plan)
    terminal = app.complete(
        project_id=running.project_id, execution_id=running.execution_id,
        plan=plan, actor_id=running.actor_id, timestamp=46,
        outcome=ExecutionOutcome.POLICY_DENIED, summary="explicit policy denial",
        evidence=(), failure_category=FailureCategory.POLICY,
        blocking_reasons=("current policy denied execution",),
    )
    assert terminal.state == RuntimeExecutionState.POLICY_DENIED
    assert terminal.session.state.value == "policy_denied"


def test_reconcile_requires_current_actor_authority(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    record = admit(app, outcome, plan)
    with pytest.raises(RuntimeExecutionError, match="actor"):
        app.reconcile(
            record.project_id, record.execution_id, plan=plan,
            actor_id="forged-worker", timestamp=44,
        )


def test_transition_revalidates_current_assignment_authority(tmp_path, monkeypatch) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    record = admit(app, outcome, plan)
    assignment = app._roles.get_assignment(record.project_id, record.assignment_id)
    monkeypatch.setattr(
        app._roles, "get_assignment",
        lambda project_id, assignment_id: assignment.model_copy(
            update={"assigned_agent_id": "replacement-agent"}
        ),
    )
    with pytest.raises(RuntimeExecutionError, match="assignment authority"):
        start(app, record, plan)


def test_models_reject_secrets_and_no_background_execution_surface_exists(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path)
    record = admit(app, outcome, plan)
    payload = record.model_dump(mode="python")
    payload["reason"] = "token=do-not-store"
    with pytest.raises(ValueError, match="secrets"):
        RuntimeExecutionRecord.model_validate(payload)
    assert not hasattr(app, "run")
    assert not hasattr(app, "retry")
    assert not hasattr(app, "promote")
