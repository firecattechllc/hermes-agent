"""Step 8 Mission Control scheduling visibility certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.workflow_scheduling import CoordinationStatus
from hermes_cli.agent_roles.workflow_scheduling_visibility import (
    WorkflowSchedulingVisibilityAdapter, WorkflowSchedulingVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

from .test_workflow_scheduling_store import intent


def test_visibility_is_idempotent_latest_state_and_project_isolated(tmp_path) -> None:
    service = WorkflowSchedulingVisibilityService(
        MissionControlService(store=MissionControlStore(tmp_path / "mission"))
    )
    scheduled = intent()
    first = service.publish(scheduled)
    assert service.publish(scheduled) == first
    claimed = scheduled.model_copy(update={
        "version": 2, "status": CoordinationStatus.CLAIMED,
        "actor_id": "worker", "updated_at": 50,
        "claim_id": "claim_1", "claimed_by": "worker", "lease_expires_at": 60,
    })
    service.publish(claimed)
    records = service.list_records("project_1", workflow_id="workflow_1", status="claimed")
    assert len(records) == 1 and records[0].version == 2
    assert service.list_records("project_2") == ()


def test_visibility_rejects_mismatched_association_and_replays_deterministically() -> None:
    adapter = WorkflowSchedulingVisibilityAdapter()
    scheduled = intent()
    claimed = scheduled.model_copy(update={
        "version": 2, "status": CoordinationStatus.CLAIMED,
        "actor_id": "worker", "updated_at": 50,
        "claim_id": "claim_1", "claimed_by": "worker", "lease_expires_at": 60,
    })
    early, late = adapter.to_event(scheduled), adapter.to_event(claimed)
    assert adapter.from_events((early, late)) == adapter.from_events((late, early))
    bad = early.model_copy(update={"project_id": "project_2"})
    with pytest.raises(ValueError, match="association"):
        adapter.from_events((bad,))


def test_visibility_rejects_malformed_payload_and_ignores_older_revision() -> None:
    adapter = WorkflowSchedulingVisibilityAdapter()
    scheduled = intent()
    claimed = scheduled.model_copy(update={
        "version": 2,
        "status": CoordinationStatus.CLAIMED,
        "actor_id": "worker",
        "updated_at": 50,
        "claim_id": "claim_1",
        "claimed_by": "worker",
        "lease_expires_at": 60,
    })
    latest = adapter.from_events((
        adapter.to_event(claimed),
        adapter.to_event(scheduled),
    ))
    assert latest[0].version == 2
    malformed = adapter.to_event(scheduled).model_copy(update={
        "payload": {"intent": {"intent_id": "incomplete"}},
    })
    with pytest.raises(Exception):
        adapter.from_events((malformed,))
    wrong_time = adapter.to_event(scheduled).model_copy(update={"timestamp": 99})
    with pytest.raises(ValueError, match="association"):
        adapter.from_events((wrong_time,))
    wrong_source = adapter.to_event(scheduled).model_copy(update={
        "payload": {
            **adapter.to_event(scheduled).payload,
            "source_idempotency_key": "workflow_scheduling:wrong:1",
        },
    })
    with pytest.raises(ValueError, match="association"):
        adapter.from_events((wrong_source,))
    conflicting = claimed.model_copy(update={"actor_id": "other-worker"})
    with pytest.raises(ValueError, match="collision"):
        adapter.from_events((adapter.to_event(claimed), adapter.to_event(conflicting)))


def test_publish_rejects_existing_idempotency_key_with_different_payload(
    tmp_path,
) -> None:
    mission = MissionControlService(
        store=MissionControlStore(tmp_path / "mission")
    )
    service = WorkflowSchedulingVisibilityService(mission)
    scheduled = intent()
    forged = scheduled.model_copy(update={"actor_id": "forged-coordinator"})
    intended_event = service._adapter.to_event(scheduled)
    forged_event = service._adapter.to_event(forged).model_copy(update={
        "payload": {
            **service._adapter.to_event(forged).payload,
            "source_idempotency_key": intended_event.payload[
                "source_idempotency_key"
            ],
        },
    })
    mission.append_event(forged_event)
    with pytest.raises(ValueError, match="idempotency collision"):
        service.publish(scheduled)
