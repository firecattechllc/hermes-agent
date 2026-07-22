"""Step 6 persistence and Mission Control replay certification."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.agent_roles.workflow import GovernedWorkflowService
from hermes_cli.agent_roles.workflow_store import GovernedWorkflowStore
from hermes_cli.agent_roles.workflow_visibility import WorkflowVisibilityService
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow import plan, result


def test_store_replays_versions_and_isolates_projects(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "roles")
    service = GovernedWorkflowService()
    p = plan()
    created = service.create(p, created_at=10)
    store.append(created)
    waiting = service.record_result(created, p, result(p), timestamp=31, next_role_id="reviewer")
    store.append(waiting, expected_version=1)
    assert store.get("project_1", created.workflow_id) == waiting
    assert store.list("project_2") == ()
    assert store.journal_path("project_1").stat().st_mode & 0o077 == 0


def test_store_rejects_version_conflict_and_tampered_chain(tmp_path) -> None:
    store = GovernedWorkflowStore(tmp_path / "roles")
    created = GovernedWorkflowService().create(plan(), created_at=10)
    store.append(created)
    with pytest.raises(ValueError, match="expected_version"):
        store.append(created.model_copy(update={"version": 2}), expected_version=0)
    path = store.journal_path("project_1")
    data = json.loads(path.read_text().splitlines()[0])
    data["workflow_version"] = 2
    path.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="initial"):
        store.list("project_1")


def test_visibility_is_idempotent_and_replays_latest_version(tmp_path) -> None:
    mission = MissionControlService(store=MissionControlStore(tmp_path / "mission"))
    visibility = WorkflowVisibilityService(mission)
    service = GovernedWorkflowService()
    p = plan()
    created = service.create(p, created_at=10)
    first = visibility.publish(created)
    duplicate = visibility.publish(created)
    assert duplicate == first
    waiting = service.record_result(created, p, result(p), timestamp=31, next_role_id="reviewer")
    visibility.publish(waiting)
    records = visibility.list_records("project_1")
    assert len(records) == 1
    assert records[0].version == 2
    assert records[0].proposed_role_id == "reviewer"
    assert records[0].state == "awaiting_authorization"


def test_cross_instance_writers_cannot_fork_revision_chain(tmp_path) -> None:
    root = tmp_path / "roles"
    initial = GovernedWorkflowService().create(plan(), created_at=10)
    GovernedWorkflowStore(root).append(initial)
    candidate = GovernedWorkflowService().record_result(
        initial,
        plan(),
        result(plan()),
        timestamp=31,
        next_role_id="reviewer",
    )

    def append_from_independent_store() -> str:
        try:
            GovernedWorkflowStore(root).append(candidate, expected_version=1)
        except ValueError:
            return "conflict"
        return "written"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: append_from_independent_store(), range(2)))
    assert sorted(outcomes) == ["conflict", "written"]
    assert GovernedWorkflowStore(root).get(
        "project_1", initial.workflow_id
    ).version == 2
