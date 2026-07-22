"""Step 7 Mission Control execution-evidence visibility certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.workflow_execution_visibility import (
    WorkflowExecutionVisibilityAdapter,
    WorkflowExecutionVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow_execution import release_run


def test_visibility_publishes_latest_safe_run_summary(tmp_path) -> None:
    _, summary, _ = release_run()
    service = WorkflowExecutionVisibilityService(
        MissionControlService(store=MissionControlStore(tmp_path / "mission"))
    )
    first = service.publish(summary)
    duplicate = service.publish(summary)
    assert duplicate == first
    records = service.list_records(
        summary.project_id,
        workflow_id=summary.workflow_id,
        status="succeeded",
    )
    assert len(records) == 1
    assert records[0].run_id == summary.run_id
    assert records[0].node_count == 1
    assert records[0].event_count == summary.event_count


def test_visibility_rejects_cross_project_payload() -> None:
    _, summary, _ = release_run()
    adapter = WorkflowExecutionVisibilityAdapter()
    event = adapter.to_event(summary).model_copy(update={"project_id": "project_2"})
    with pytest.raises(ValueError, match="association"):
        adapter.from_events((event,))


def test_visibility_projection_is_deterministic_for_reordered_events() -> None:
    events, final, _ = release_run()
    adapter = WorkflowExecutionVisibilityAdapter()
    from hermes_cli.agent_roles.workflow_execution import WorkflowExecutionProjector

    projector = WorkflowExecutionProjector()
    early = projector.replay(events[:3])
    early_event = adapter.to_event(early)
    final_event = adapter.to_event(final)
    forward = adapter.from_events((early_event, final_event))
    reverse = adapter.from_events((final_event, early_event))
    assert forward == reverse
    assert forward[0].status == "succeeded"
