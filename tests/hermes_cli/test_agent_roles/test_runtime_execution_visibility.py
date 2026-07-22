"""Runtime execution Mission Control visibility certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.runtime_execution_visibility import (
    RuntimeExecutionVisibilityAdapter,
    RuntimeExecutionVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_runtime_execution import admit, runtime_app, start


def test_visibility_publishes_latest_revision_idempotently(tmp_path) -> None:
    service = RuntimeExecutionVisibilityService(
        MissionControlService(MissionControlStore(tmp_path / "mission"))
    )
    app, _, outcome, plan, _ = runtime_app(
        tmp_path, visibility=service, record_step7=False
    )
    ready = admit(app, outcome, plan)
    running = start(app, ready, plan)
    assert service.publish(running) == service.publish(running)
    records = service.list_records(outcome.project_id, state="running")
    assert len(records) == 1
    assert records[0].revision == 2
    assert records[0].execution_fingerprint == running.fingerprint


def test_visibility_rejects_forged_association(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    record = admit(app, outcome, plan)
    adapter = RuntimeExecutionVisibilityAdapter()
    forged = adapter.to_event(record).model_copy(update={"project_id": "forged"})
    with pytest.raises(ValueError, match="association"):
        adapter.from_events((forged,))


def test_visibility_projection_is_deterministic_for_reordered_revisions(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    running = start(app, ready, plan)
    adapter = RuntimeExecutionVisibilityAdapter()
    forward = adapter.from_events((adapter.to_event(ready), adapter.to_event(running)))
    reverse = adapter.from_events((adapter.to_event(running), adapter.to_event(ready)))
    assert forward == reverse
    assert forward[0].revision == 2


def test_visibility_rejects_revision_gaps(tmp_path) -> None:
    app, _, outcome, plan, _ = runtime_app(tmp_path, record_step7=False)
    ready = admit(app, outcome, plan)
    running = start(app, ready, plan)
    adapter = RuntimeExecutionVisibilityAdapter()
    revision_three = running.model_copy(update={"revision": 3})
    with pytest.raises(ValueError, match="revision gap"):
        adapter.from_events((adapter.to_event(ready), adapter.to_event(revision_three)))
