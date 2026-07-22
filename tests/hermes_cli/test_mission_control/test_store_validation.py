"""Mission Control store validation and fail-closed behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.store import (
    MissionControlStore,
    event_log_path,
    project_event_log_path,
)


def _event(project_id: str = "proj_a", payload: dict | None = None) -> m.TelemetryEvent:
    return m.TelemetryEvent(
        event_id="event_1",
        event_type="agent_started",
        project_id=project_id,
        agent_id="agent_1",
        payload=payload or {},
    )


def _unsafe_event(project_id: str) -> m.TelemetryEvent:
    return m.TelemetryEvent.model_construct(
        event_id="event_1",
        event_type="agent_started",
        project_id=project_id,
        agent_id="agent_1",
        timestamp=1,
        sequence=0,
        severity="info",
        payload={},
        schema_version=m.CURRENT_SCHEMA_VERSION,
    )


@pytest.mark.parametrize(
    "project_id",
    [
        "../escape",
        "safe/escape",
        "safe\\escape",
        "has\x00nul",
        "",
    ],
)
def test_append_event_rejects_unsafe_project_ids_before_writing(
    tmp_path: Path,
    project_id: str,
) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")

    with pytest.raises(ValueError, match="unsafe identifier"):
        store.append_event(_unsafe_event(project_id))

    assert not event_log_path(root=tmp_path / "mission_control").exists()


def test_append_event_rejects_non_json_payload_before_writing(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    event = _event(payload={"bad": object()})

    with pytest.raises(ValueError, match="JSON serializable"):
        store.append_event(event)

    root = tmp_path / "mission_control"
    assert not event_log_path(root=root).exists()
    assert not project_event_log_path("proj_a", root=root).exists()


def test_append_events_validates_entire_batch_before_any_write(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    good = _event(project_id="proj_a")
    bad = _event(project_id="../escape")

    with pytest.raises(ValueError, match="unsafe identifier"):
        store.append_events([good, bad])

    root = tmp_path / "mission_control"
    assert not event_log_path(root=root).exists()
    assert not project_event_log_path("proj_a", root=root).exists()


def test_malformed_global_jsonl_replay_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"
    store = MissionControlStore(root=root)
    good = _event().model_dump_json()
    event_log_path(root=root).write_text(f"{good}\n{{not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSONL line 2"):
        list(store.iter_events())


def test_malformed_project_jsonl_replay_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"
    store = MissionControlStore(root=root)
    path = project_event_log_path("proj_a", root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSONL line 1"):
        list(store.iter_events(project_id="proj_a"))


def test_failed_project_validation_does_not_diverge_journals(tmp_path: Path) -> None:
    root = tmp_path / "mission_control"
    store = MissionControlStore(root=root)

    with pytest.raises(ValueError):
        store.append_event(_unsafe_event("../escape"))

    assert not event_log_path(root=root).exists()
    assert not (root / "projects").exists()
