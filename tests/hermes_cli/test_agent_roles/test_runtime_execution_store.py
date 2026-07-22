"""Runtime execution append-only store certification."""

from __future__ import annotations

import json

import pytest

from hermes_cli.agent_roles.runtime_execution_store import RuntimeExecutionStore

from .test_runtime_execution import admit, runtime_app, start


def test_store_replays_revisions_and_is_project_isolated(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    running = start(app, ready, plan)
    reopened = RuntimeExecutionStore(tmp_path / "runtime-execution")
    assert reopened.get(outcome.project_id, ready.execution_id) == running
    assert reopened.history(outcome.project_id, ready.execution_id) == (ready, running)
    assert reopened.list("another-project") == ()


def test_store_detects_corruption_and_invalid_project_paths(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    admit(app, outcome, plan)
    path = store.journal_path(outcome.project_id)
    payload = json.loads(path.read_text())
    payload["record"]["reason"] = "tampered"
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ValueError, match="corrupt runtime execution journal"):
        store.list(outcome.project_id)
    with pytest.raises(ValueError, match="invalid runtime execution project_id"):
        store.list("../escape")


def test_store_recovers_torn_tail_and_enforces_capacity(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    path = store.journal_path(outcome.project_id)
    with path.open("ab") as handle:
        handle.write(b'{"journal_sequence":2,"torn"')
    reopened = RuntimeExecutionStore(tmp_path / "runtime-execution", capacity=1)
    assert reopened.create(ready) == ready
    with pytest.raises(OverflowError, match="capacity"):
        reopened.append(
            ready.model_copy(update={
                "revision": 2, "state": "running", "updated_at": 45,
                "started_at": 45, "causation_id": ready.fingerprint,
                "reason": "explicit start",
            }),
            expected_revision=1,
        )
    assert path.read_bytes().endswith(b"\n")


def test_store_read_recovers_torn_tail_and_rejects_actor_substitution(tmp_path) -> None:
    app, store, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    path = store.journal_path(outcome.project_id)
    with path.open("ab") as handle:
        handle.write(b'{"torn"')
    reopened = RuntimeExecutionStore(tmp_path / "runtime-execution")
    assert reopened.get(outcome.project_id, ready.execution_id) == ready
    assert path.read_bytes().endswith(b"\n")
    forged = ready.model_copy(update={
        "revision": 2, "state": "running", "updated_at": 45,
        "started_at": 45, "actor_id": "forged-worker",
        "causation_id": ready.fingerprint,
        "reason": "forged start",
        "session": app._execution_service.start(ready.session, plan, started_at=45),
    })
    with pytest.raises(ValueError, match="authority changed"):
        reopened.append(forged, expected_revision=1)


def test_store_rejects_zero_progress_write(tmp_path, monkeypatch) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    store = RuntimeExecutionStore(tmp_path / "zero-progress")
    monkeypatch.setattr("os.write", lambda fd, data: 0)
    with pytest.raises(OSError, match="no progress"):
        admit(
            GovernedStoreApp(app, store), outcome, plan
        )


class GovernedStoreApp:
    def __init__(self, source, store):
        from hermes_cli.agent_roles.runtime_execution import (
            GovernedRuntimeExecutionCoordinator,
        )

        self._app = GovernedRuntimeExecutionCoordinator(
            roles=source._roles, dispatches=source._dispatches, scheduling=source._scheduling,
            workflows=source._workflows,
            workflow_evidence=source._workflow_evidence, executions=store,
        )

    def admit(self, **kwargs):
        return self._app.admit(**kwargs)
