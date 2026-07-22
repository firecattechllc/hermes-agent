"""Step 7 workflow execution evidence store certification."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.agent_roles.workflow_execution import WorkflowRunStatus
from hermes_cli.agent_roles.workflow_execution_store import (
    WorkflowExecutionRecorder,
    WorkflowExecutionStore,
    WorkflowExecutionVisibilityError,
)
from hermes_cli.agent_roles.workflow_execution_visibility import (
    WorkflowExecutionVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow_execution import release_run


def test_store_replays_and_queries_run_workflow_status_and_node(tmp_path) -> None:
    events, expected, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")
    for event in events:
        store.append(event)
    assert store.get_summary(expected.project_id, expected.run_id) == expected
    assert store.list_summaries(
        expected.project_id,
        workflow_id=expected.workflow_id,
    ) == (expected,)
    assert store.list_summaries(
        expected.project_id,
        status=WorkflowRunStatus.SUCCEEDED,
    ) == (expected,)
    assert store.list_summaries(
        expected.project_id,
        node_run_id=expected.nodes[0].node_run_id,
    ) == (expected,)


def test_store_is_project_isolated_and_files_are_private(tmp_path) -> None:
    events, summary, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")
    store.append(events[0])
    assert store.list_summaries("project_2") == ()
    assert store.journal_path(summary.project_id).stat().st_mode & 0o077 == 0


def test_identical_event_append_is_idempotent(tmp_path) -> None:
    events, summary, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")
    assert store.append(events[0]) == events[0]
    assert store.append(events[0]) == events[0]
    assert store.get_summary(summary.project_id, summary.run_id).event_count == 1


def test_store_rejects_tampered_checksum(tmp_path) -> None:
    events, summary, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")
    store.append(events[0])
    path = store.journal_path(summary.project_id)
    record = json.loads(path.read_text().splitlines()[0])
    record["event"]["actor_id"] = "tampered"
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="corrupt"):
        store.list_summaries(summary.project_id)


def test_store_completes_short_journal_writes(tmp_path, monkeypatch) -> None:
    events, summary, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(os, "write", short_write)
    store.append(events[0])
    assert store.get_summary(summary.project_id, summary.run_id).event_count == 1


def test_store_rejects_invalid_project_paths(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path / "evidence")
    for project_id in ("", ".", "..", "../escape", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="project_id"):
            store.list_summaries(project_id)


def test_cross_instance_duplicate_append_remains_single_event(tmp_path) -> None:
    events, summary, _ = release_run()
    root = tmp_path / "evidence"

    def append() -> str:
        return WorkflowExecutionStore(root).append(events[0]).event_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = tuple(pool.map(lambda _: append(), range(2)))
    assert ids == (events[0].event_id, events[0].event_id)
    assert WorkflowExecutionStore(root).get_summary(
        summary.project_id,
        summary.run_id,
    ).event_count == 1


def test_recorder_persists_before_visibility_failure_and_reconciles(tmp_path) -> None:
    events, summary, _ = release_run()
    store = WorkflowExecutionStore(tmp_path / "evidence")

    class FailingVisibility:
        def publish(self, run_summary):
            raise RuntimeError("temporary Mission Control failure")

    recorder = WorkflowExecutionRecorder(store, FailingVisibility())
    with pytest.raises(WorkflowExecutionVisibilityError) as captured:
        recorder.record(events[0])
    assert captured.value.summary.event_count == 1
    assert store.get_summary(summary.project_id, summary.run_id) is not None

    visibility = WorkflowExecutionVisibilityService(
        MissionControlService(store=MissionControlStore(tmp_path / "mission"))
    )
    recorder.visibility = visibility
    reconciled = recorder.reconcile_visibility(summary.project_id)
    assert len(reconciled) == 1
    assert visibility.list_records(summary.project_id)[0].event_count == 1
