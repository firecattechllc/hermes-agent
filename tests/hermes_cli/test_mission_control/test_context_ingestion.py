"""Mission Control Shared Engineering Context ingestion tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.mission_control.adapters.context_adapter import ContextAdapter
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def _launch(**overrides):
    data = {
        "project_id": "proj_a",
        "launch_id": "launch_1",
        "task_id": "task_1",
        "backlog_id": "backlog_1",
        "stage": "implementation",
        "status": "running",
        "selected_agents": ["builder", "reviewer"],
        "evidence_refs": ["evidence/run.log"],
        "commits": [],
        "branches": ["dev"],
        "pull_request_urls": [],
        "promotion_state": None,
        "failure_reason": None,
        "updated_at": 10,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _record(**overrides):
    data = {
        "project_id": "proj_a",
        "record_id": "record_1",
        "record_type": "objective",
        "title": "Certify Mission Control",
        "body": "Finish the foundation safely.",
        "updated_at": 20,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_context_adapter_translates_launch_to_events() -> None:
    events = ContextAdapter().translate_launch_to_events(_launch())

    assert [event.event_type for event in events] == [
        "context_launch_imported",
        "agent_started",
        "agent_started",
        "backlog_item_created",
        "evidence_requested",
        "context_ingested",
    ]
    assert all(event.project_id == "proj_a" for event in events)
    assert all(event.launch_id == "launch_1" for event in events)
    assert all(event.payload.get("source") == "context_engine" for event in events)
    assert all(event.payload.get("source_idempotency_key") for event in events)


def test_context_ingestion_persists_records_argument(tmp_path: Path) -> None:
    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))

    appended = service.ingest_context_launch(_launch(), records=[_record()])

    assert any(event.payload.get("record_id") == "record_1" for event in appended)
    snapshot = service.get_snapshot("proj_a")
    assert any(item.title == "Certify Mission Control" for item in snapshot.backlog_states)


def test_context_ingestion_is_idempotent_for_repeated_same_launch(tmp_path: Path) -> None:
    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))
    launch = _launch()

    first = service.ingest_context_launch(launch, records=[_record()])
    second = service.ingest_context_launch(launch, records=[_record()])

    assert first
    assert second == []
    assert service.event_count("proj_a") == len(first)


def test_context_ingestion_allows_follow_up_for_known_launch(tmp_path: Path) -> None:
    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))

    first = service.ingest_context_launch(_launch(status="running", updated_at=10))
    second = service.ingest_context_launch(
        _launch(status="complete", stage="complete", updated_at=11)
    )

    assert first
    assert second
    events = service.get_events("proj_a")
    statuses = [
        event.payload.get("status")
        for event in events
        if event.event_type == "context_launch_imported"
    ]
    assert statuses == ["running", "complete"]


def test_context_adapter_handles_missing_optional_fields() -> None:
    launch = SimpleNamespace(project_id="proj_a", launch_id="launch_1")

    events = ContextAdapter().translate_launch_to_events(launch)

    assert [event.event_type for event in events] == [
        "context_launch_imported",
        "context_ingested",
    ]
    assert events[0].task_id is None
    assert events[0].backlog_id is None


@pytest.mark.parametrize("bad_launch", [object(), SimpleNamespace(project_id="proj_a")])
def test_context_adapter_rejects_invalid_launch_inputs(bad_launch) -> None:
    with pytest.raises(ValueError, match="launch"):
        ContextAdapter().translate_launch_to_events(bad_launch)


@pytest.mark.parametrize("bad_record", [object(), SimpleNamespace(project_id="proj_a")])
def test_context_adapter_rejects_invalid_record_inputs(bad_record) -> None:
    with pytest.raises(ValueError, match="record"):
        ContextAdapter().translate_record_to_events(bad_record)
