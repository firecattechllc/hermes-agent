"""Step 13 governed runtime orchestration certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.execution import ExecutionOutcome
from hermes_cli.agent_roles.launch_validation import RuntimeCompatibility
from hermes_cli.agent_roles.runtime_orchestration import (
    GovernedRuntimeOrchestrationCoordinator,
    RuntimeOrchestrationError,
    RuntimeOrchestrationPublicationError,
    RuntimeOrchestrationState,
    RuntimeOrchestrationStore,
)
from hermes_cli.agent_roles.workflow_result import WorkflowResultCoordinator

from .test_runtime_execution import required_evidence, runtime_app


def orchestration_app(tmp_path, *, visibility=None):
    runtime, _, dispatch, plan, dispatch_app = runtime_app(
        tmp_path,
        record_step7=False,
    )
    results = WorkflowResultCoordinator(
        executions=runtime._executions,
        workflows=runtime._workflows,
    )
    store = RuntimeOrchestrationStore(tmp_path / "runtime-orchestration")
    app = GovernedRuntimeOrchestrationCoordinator(
        dispatch=dispatch_app,
        runtime=runtime,
        results=results,
        scheduling=dispatch_app._scheduling,
        orchestrations=store,
        visibility=visibility,
    )
    claimed = dispatch_app._scheduling.get(
        dispatch.project_id,
        dispatch.intent_id,
    )
    assert claimed is not None
    return app, store, dispatch, plan, claimed


def create_prepared_record(app, dispatch, plan, claimed):
    return app.prepare(
        project_id=dispatch.project_id,
        intent_id=dispatch.intent_id,
        expected_claim_id=dispatch.claim_id,
        plan=plan,
        compatibility=RuntimeCompatibility(
            runtime=dispatch.contract.environment.runtime,
        ),
        repository_root=dispatch.contract.workspace.repository_root,
        runtime=dispatch.contract.environment.runtime,
        actor_id=dispatch.claimed_by,
        timestamp=dispatch.created_at,
        base_ref=dispatch.contract.workspace.base_ref,
        engine=dispatch.contract.environment.engine,
        provider=dispatch.contract.environment.provider,
        model=dispatch.contract.environment.model,
        environment=(),
    )


def test_full_runtime_orchestration_is_durable_and_explicit(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)

    prepared = create_prepared_record(
        app,
        dispatch,
        plan,
        claimed,
    )
    assert prepared.state == RuntimeOrchestrationState.PREPARED

    ready = app.admit(
        project_id=prepared.project_id,
        orchestration_id=prepared.orchestration_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=44,
    )
    assert ready.state == RuntimeOrchestrationState.READY

    running = app.start(
        project_id=ready.project_id,
        orchestration_id=ready.orchestration_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=45,
    )
    assert running.state == RuntimeOrchestrationState.RUNNING

    terminal = app.complete(
        project_id=running.project_id,
        orchestration_id=running.orchestration_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=46,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="governed orchestration completed",
        evidence=required_evidence(plan),
    )
    assert terminal.state == RuntimeOrchestrationState.TERMINAL
    assert terminal.result_id is not None

    finalized = app.finalize(
        project_id=terminal.project_id,
        orchestration_id=terminal.orchestration_id,
        plan=plan,
        timestamp=47,
    )
    assert finalized.state == RuntimeOrchestrationState.FINALIZED
    assert finalized.workflow_version == terminal.workflow_version + 1
    assert store.history(
        finalized.project_id,
        finalized.orchestration_id,
    ) == (
        prepared,
        ready,
        running,
        terminal,
        finalized,
    )



def test_prepare_uses_exact_dispatch_scheduling_revision(tmp_path) -> None:
    app, store, dispatch, plan, latest = orchestration_app(tmp_path)

    assert latest.version >= dispatch.intent_version
    assert (
        latest.version != dispatch.intent_version
        or latest.fingerprint != dispatch.intent_fingerprint
    )

    dispatched_intent = app._scheduling.get_revision(
        dispatch.project_id,
        dispatch.intent_id,
        dispatch.intent_version,
    )
    assert dispatched_intent is not None
    assert dispatched_intent.fingerprint == dispatch.intent_fingerprint

    prepared = create_prepared_record(
        app,
        dispatch,
        plan,
        latest,
    )

    assert prepared.workflow_version == dispatched_intent.workflow_version
    assert store.get(
        prepared.project_id,
        prepared.orchestration_id,
    ) == prepared

def test_prepare_is_idempotent_for_existing_dispatch(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)
    first = create_prepared_record(app, dispatch, plan, claimed)
    second = create_prepared_record(app, dispatch, plan, claimed)
    assert second == first
    assert store.list(dispatch.project_id) == (first,)


def test_forged_plan_fails_closed(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)
    prepared = create_prepared_record(app, dispatch, plan, claimed)
    forged = plan.model_copy(update={"agent_id": "forged-agent"})

    with pytest.raises(
        RuntimeOrchestrationError,
        match="plan association mismatch",
    ):
        app.admit(
            project_id=prepared.project_id,
            orchestration_id=prepared.orchestration_id,
            plan=forged,
            actor_id=dispatch.claimed_by,
            timestamp=44,
        )

    assert store.get(
        prepared.project_id,
        prepared.orchestration_id,
    ) == prepared


def test_orchestration_cannot_skip_lifecycle_states(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)
    prepared = create_prepared_record(app, dispatch, plan, claimed)

    with pytest.raises(
        RuntimeOrchestrationError,
        match="must be running",
    ):
        app.complete(
            project_id=prepared.project_id,
            orchestration_id=prepared.orchestration_id,
            plan=plan,
            actor_id=dispatch.claimed_by,
            timestamp=46,
            outcome=ExecutionOutcome.SUCCEEDED,
            summary="attempted state skip",
            evidence=(),
        )

    assert store.history(
        prepared.project_id,
        prepared.orchestration_id,
    ) == (prepared,)


def test_duplicate_transition_fails_closed(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)
    prepared = create_prepared_record(app, dispatch, plan, claimed)
    ready = app.admit(
        project_id=prepared.project_id,
        orchestration_id=prepared.orchestration_id,
        plan=plan,
        actor_id=dispatch.claimed_by,
        timestamp=44,
    )

    with pytest.raises(
        RuntimeOrchestrationError,
        match="must be prepared",
    ):
        app.admit(
            project_id=prepared.project_id,
            orchestration_id=prepared.orchestration_id,
            plan=plan,
            actor_id=dispatch.claimed_by,
            timestamp=45,
        )

    assert store.history(
        ready.project_id,
        ready.orchestration_id,
    ) == (prepared, ready)


def test_persistence_precedes_visibility_failure_and_reconcile(tmp_path) -> None:
    class Visibility:
        def __init__(self):
            self.fail = True
            self.items = []

        def publish(self, record):
            if self.fail:
                raise RuntimeError("offline")
            self.items.append(record)

    visibility = Visibility()
    app, store, dispatch, plan, claimed = orchestration_app(
        tmp_path,
        visibility=visibility,
    )

    with pytest.raises(RuntimeOrchestrationPublicationError) as exc:
        create_prepared_record(app, dispatch, plan, claimed)

    record = exc.value.record
    assert store.get(
        record.project_id,
        record.orchestration_id,
    ) == record

    visibility.fail = False
    assert app.reconcile(
        record.project_id,
        record.orchestration_id,
    ) == record
    assert visibility.items == [record]


def test_store_recovers_torn_tail(tmp_path) -> None:
    app, store, dispatch, plan, claimed = orchestration_app(tmp_path)
    prepared = create_prepared_record(app, dispatch, plan, claimed)
    path = store.journal_path(prepared.project_id)

    with path.open("ab") as handle:
        handle.write(b'{"torn":')

    recovered = store.get(
        prepared.project_id,
        prepared.orchestration_id,
    )
    assert recovered == prepared
    assert path.read_bytes().endswith(b"\n")
