"""Mission Control service sequence and append-safety tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore, project_event_log_path


def _event(event_id: str, project_id: str = "proj_a") -> m.TelemetryEvent:
    return m.TelemetryEvent(
        event_id=event_id,
        event_type="agent_started",
        project_id=project_id,
        agent_id=event_id.replace("event_", "agent_"),
        sequence=0,
    )


def test_concurrent_service_appends_assign_unique_monotonic_sequences(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"

    def worker(index: int) -> int:
        service = MissionControlService(store=MissionControlStore(root=root))
        event = service.append_event(_event(f"event_{index:02d}"))
        return event.sequence

    with ThreadPoolExecutor(max_workers=12) as pool:
        sequences = list(pool.map(worker, range(30)))

    assert sorted(sequences) == list(range(1, 31))

    events = MissionControlService(store=MissionControlStore(root=root)).get_events("proj_a")
    assert [event.sequence for event in events] == list(range(1, 31))
    assert len({event.sequence for event in events}) == 30


def test_concurrent_batch_appends_keep_project_sequences_monotonic(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"

    def worker(index: int) -> list[int]:
        service = MissionControlService(store=MissionControlStore(root=root))
        events = service.append_events([
            _event(f"event_{index:02d}_a"),
            _event(f"event_{index:02d}_b"),
        ])
        return [event.sequence for event in events]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assigned = [seq for batch in pool.map(worker, range(12)) for seq in batch]

    assert sorted(assigned) == list(range(1, 25))


def test_partial_jsonl_line_fails_closed_on_replay(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"
    service = MissionControlService(store=MissionControlStore(root=root))
    service.append_event(_event("event_1"))
    path = project_event_log_path("proj_a", root=root)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"event_id": "partial"')

    with pytest.raises(ValueError, match="malformed JSONL line 2"):
        service.get_events("proj_a")
