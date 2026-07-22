"""Workflow dispatch Mission Control visibility certification."""

from __future__ import annotations

from hermes_cli.agent_roles.workflow_dispatch_visibility import (
    WorkflowDispatchVisibilityAdapter,
    WorkflowDispatchVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow_dispatch import prepare, prepared_app


def test_visibility_publishes_idempotent_read_only_dispatch_projection(tmp_path) -> None:
    mission = MissionControlService(MissionControlStore(tmp_path / "mission"))
    visibility = WorkflowDispatchVisibilityService(mission)
    app, _, _, claimed, plan, compatibility = prepared_app(
        tmp_path, visibility=visibility
    )
    outcome = prepare(app, claimed, plan, compatibility)
    assert visibility.publish(outcome) == visibility.publish(outcome)
    records = visibility.list_records(claimed.project_id)
    assert len(records) == 1
    assert records[0].dispatch_id == outcome.dispatch_id
    assert records[0].session_id == outcome.session.session_id
    assert records[0].dispatch_fingerprint == outcome.fingerprint


def test_visibility_rejects_forged_event_identity(tmp_path) -> None:
    app, _, _, claimed, plan, compatibility = prepared_app(tmp_path)
    outcome = prepare(app, claimed, plan, compatibility)
    adapter = WorkflowDispatchVisibilityAdapter()
    forged = adapter.to_event(outcome).model_copy(update={"event_id": "telemetry_forged"})
    try:
        adapter.from_events((forged,))
    except ValueError as exc:
        assert "association mismatch" in str(exc)
    else:  # pragma: no cover - explicit false-positive guard
        raise AssertionError("forged event identity was accepted")
