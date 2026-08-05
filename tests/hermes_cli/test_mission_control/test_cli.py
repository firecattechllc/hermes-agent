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


def test_mission_control_fleet_json(monkeypatch, tmp_path: Path, capsys) -> None:
    from hermes_cli.prime.evidence import PrimeEvidenceStore
    from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
    from hermes_cli.prime.fleet_runtime import FleetRuntime
    from hermes_cli.prime.health import LivenessState, ReadinessState
    from hermes_cli.prime.heartbeat import HeartbeatSubmission

    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))
    runtime = FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="proj_a",
        mission_control=service,
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )
    now = 1_700_000_000
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="req-titan", natural_key="titan", role=FleetNodeRole.TITAN,
            declared_capabilities=("worker_heartbeat",),
            endpoint="http://titan.tailnet.internal:11434",
            software_version="1.0.0", protocol_version=1, requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key="titan", liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )

    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "fleet", "proj_a", "--json"])

    assert args.func(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["fleet_nodes"][0]["natural_key"] == "titan"
    assert payload["fleet_nodes"][0]["connection_state"] == "connected"


def test_mission_control_fleet_text_with_no_nodes(monkeypatch, tmp_path: Path, capsys) -> None:
    service = MissionControlService(store=MissionControlStore(root=tmp_path / "mission_control"))
    monkeypatch.setattr("hermes_cli.mission_control_commands._get_service", lambda: service)
    args = _parser().parse_args(["mission-control", "fleet", "empty_project"])

    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "no fleet nodes registered" in out


def test_main_builtin_subcommands_include_mission_control() -> None:
    from hermes_cli.main import _BUILTIN_SUBCOMMANDS

    assert "mission-control" in _BUILTIN_SUBCOMMANDS
    assert "mission_control" in _BUILTIN_SUBCOMMANDS
