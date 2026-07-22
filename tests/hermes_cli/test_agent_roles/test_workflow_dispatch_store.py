"""Workflow dispatch append-only store certification."""

from __future__ import annotations

import json

import pytest

from hermes_cli.agent_roles.workflow_dispatch_store import WorkflowDispatchStore

from .test_workflow_dispatch import prepare, prepared_app


def test_store_replays_idempotently_and_is_project_isolated(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(app, claimed, plan, compatibility)
    reopened = WorkflowDispatchStore(tmp_path / "dispatch")
    assert reopened.get(claimed.project_id, outcome.dispatch_id) == outcome
    assert reopened.list("another-project") == ()
    assert reopened.append(outcome) == outcome
    assert reopened.list(claimed.project_id) == (outcome,)


def test_store_detects_checksum_corruption(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    prepare(app, claimed, plan, compatibility)
    path = store.journal_path(claimed.project_id)
    record = json.loads(path.read_text())
    record["outcome"]["reason"] = "tampered"
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="corrupt workflow dispatch journal"):
        store.list(claimed.project_id)


def test_store_recovers_interrupted_final_record_before_append(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(app, claimed, plan, compatibility)
    path = store.journal_path(claimed.project_id)
    with path.open("ab") as handle:
        handle.write(b'{"journal_sequence":2,"torn"')
    reopened = WorkflowDispatchStore(tmp_path / "dispatch")
    assert reopened.list(claimed.project_id) == (outcome,)
    assert reopened.append(outcome) == outcome
    assert path.read_bytes().endswith(b"\n")


def test_store_rejects_invalid_project_paths(tmp_path) -> None:
    store = WorkflowDispatchStore(tmp_path)
    with pytest.raises(ValueError, match="invalid workflow dispatch project_id"):
        store.list("../escape")


def test_store_atomically_rejects_second_dispatch_for_same_intent(tmp_path) -> None:
    app, store, _, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(app, claimed, plan, compatibility)
    conflicting = outcome.model_copy(update={"dispatch_id": "dispatch_conflict"})
    with pytest.raises(ValueError, match="intent collision"):
        WorkflowDispatchStore(tmp_path / "dispatch").append(conflicting)
    assert store.list(claimed.project_id) == (outcome,)
