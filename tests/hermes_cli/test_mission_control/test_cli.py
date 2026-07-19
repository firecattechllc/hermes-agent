"""Mission Control CLI command tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.mission_control_commands import (
    build_mission_control_parser,
    mission_control_command,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    mc_parser = build_mission_control_parser(sub)
    mc_parser.set_defaults(func=mission_control_command)
    return parser


def _service(tmp_path: Path) -> MissionControlService:
    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))
    service.append_event(m.TelemetryEvent(
        event_id="event_1",
        event_type="context_launch_imported",
        project_id="proj_a",
        launch_id="launch_1",
        task_id="task_1",
        backlog_id="backlog_1",
        payload={"status": "running", "stage": "implementation"},
    ))
    service.append_event(m.TelemetryEvent(
        event_id="event_2",
        event_type="agent_started",
        project_id="proj_a",
        launch_id="launch_1",
        agent_id="agent_1",
    ))
    return service


def test_mission_control_status_json(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "status", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["project_count"] == 1
    assert payload["event_count"] == 2


def test_mission_control_projects_text(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "projects"])

    assert args.func(args) == 0

    assert "proj_a" in capsys.readouterr().out


def test_mission_control_launches_json(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "launches", "proj_a", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["launches"][0]["launch_id"] == "launch_1"
    assert payload["launches"][0]["status"] == "running"


def test_mission_control_snapshot_json(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "snapshot", "proj_a", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == "proj_a"
    assert payload["event_count"] == 2
    assert payload["integrity_hash"]


def test_mission_control_events_limit(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "events", "proj_a", "--limit", "1", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_id"] == "event_2"


def test_mission_control_overview_json(monkeypatch, tmp_path: Path, capsys) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "overview", "proj_a", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == "proj_a"
    assert payload["launches"][0]["launch_id"] == "launch_1"
    assert payload["agents"][0]["agent_id"] == "agent_1"
    assert "recent_events" in payload


def test_main_builtin_subcommands_include_mission_control() -> None:
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert "mission-control" in _BUILTIN_SUBCOMMANDS
    assert "mission_control" in _BUILTIN_SUBCOMMANDS
