"""Mission Control runtime telemetry bridge tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli import projects_db as pdb
from hermes_cli.mission_control.runtime import mark_turn_result, observe_hook, telemetry_turn
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.mission_control import models as mc_models


def _activate_project(home: Path, repo: Path) -> str:
    with pdb.connect_closing(db_path=home / "projects.db") as conn:
        project_id = pdb.create_project(
            conn,
            name="Hermes Platform",
            slug="hermes-platform",
            folders=[str(repo)],
            primary_path=str(repo),
        )
        pdb.set_active(conn, project_id)
        return project_id


def test_runtime_turn_creates_context_launch_and_mission_control_events(
    tmp_path: Path,
) -> None:
    home = tmp_path / "hermes-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    token = set_hermes_home_override(home)
    old_cwd = Path.cwd()
    try:
        project_id = _activate_project(home, repo)
        import os

        os.chdir(repo)
        agent = SimpleNamespace(session_id="sess_1", platform="cli")

        with telemetry_turn(agent, "implement telemetry", "task_1"):
            observe_hook(
                "pre_tool_call",
                tool_name="terminal",
                args={"command": "pytest"},
                task_id="task_1",
                session_id="sess_1",
                tool_call_id="call_1",
            )
            observe_hook(
                "post_tool_call",
                tool_name="terminal",
                args={"command": "pytest"},
                result=json.dumps({
                    "verification_evidence": {
                        "status": "collected",
                        "kind": "terminal",
                        "scope": "focused tests",
                        "canonical_command": "pytest tests/hermes_cli/test_mission_control",
                    }
                }),
                status="ok",
                task_id="task_1",
                session_id="sess_1",
                tool_call_id="call_1",
                duration_ms=12,
            )
            observe_hook(
                "pre_approval_request",
                command="rm file",
                description="Dangerous command",
                pattern_key="rm",
                session_key="sess_1",
                surface="cli",
            )
            observe_hook(
                "post_approval_response",
                command="rm file",
                description="Dangerous command",
                pattern_key="rm",
                session_key="sess_1",
                surface="cli",
                choice="deny",
            )
            mark_turn_result({"completed": True})
            observe_hook(
                "on_session_end",
                session_id="sess_1",
                task_id="task_1",
                completed=True,
                interrupted=False,
                model="test-model",
                platform="cli",
            )

        snapshot = MissionControlService().get_snapshot(project_id)
    finally:
        import os

        os.chdir(old_cwd)
        reset_hermes_home_override(token)

    event_types = [event.event_type for event in snapshot.events]
    assert event_types.count("agent_tools_started") == 1
    assert event_types.count("agent_tools_completed") == 1
    assert event_types.count("approval_requested") == 1
    assert event_types.count("approval_denied") == 1
    assert event_types.count("agent_complete") == 1
    assert "evidence_collected" in event_types
    assert snapshot.agent_states[0].state.value == "complete"
    assert len(snapshot.evidence_states) == 1
    assert snapshot.evidence_states[0].source_path == (
        "pytest tests/hermes_cli/test_mission_control"
    )


def test_runtime_hook_observation_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "hermes-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    token = set_hermes_home_override(home)
    old_cwd = Path.cwd()
    try:
        project_id = _activate_project(home, repo)
        import os

        os.chdir(repo)
        agent = SimpleNamespace(session_id="sess_2", platform="cli")

        with telemetry_turn(agent, "repeat", "task_2"):
            for _ in range(2):
                observe_hook(
                    "pre_tool_call",
                    tool_name="read_file",
                    args={"path": "README.md"},
                    task_id="task_2",
                    session_id="sess_2",
                    tool_call_id="call_repeat",
                )
            mark_turn_result({"completed": True})

        events = MissionControlService().get_events(project_id)
    finally:
        import os

        os.chdir(old_cwd)
        reset_hermes_home_override(token)

    starts = [
        event for event in events
        if event.event_type == "agent_tools_started"
        and event.payload.get("tool_call_id") == "call_repeat"
    ]
    assert len(starts) == 1


def test_post_tool_bridge_derives_error_status_without_plugins(tmp_path: Path) -> None:
    home = tmp_path / "hermes-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    token = set_hermes_home_override(home)
    old_cwd = Path.cwd()
    try:
        project_id = _activate_project(home, repo)
        import os
        from model_tools import _emit_post_tool_call_hook

        os.chdir(repo)
        agent = SimpleNamespace(session_id="sess_error", platform="cli")

        with telemetry_turn(agent, "error", "task_error"):
            _emit_post_tool_call_hook(
                function_name="terminal",
                function_args={"command": "false"},
                result=json.dumps({"error": "command failed"}),
                task_id="task_error",
                session_id="sess_error",
                tool_call_id="call_error",
            )
            mark_turn_result({"failed": True, "error": "command failed"})

        events = MissionControlService().get_events(project_id)
    finally:
        import os

        os.chdir(old_cwd)
        reset_hermes_home_override(token)

    completed = [
        event for event in events
        if event.event_type == "agent_tools_completed"
        and event.payload.get("tool_call_id") == "call_error"
    ]
    assert completed[0].payload["status"] == "error"
    assert completed[0].payload["error_message"] == "command failed"
    assert any(event.event_type == "agent_error" for event in events)


def test_runtime_without_project_is_noop(tmp_path: Path) -> None:
    token = set_hermes_home_override(tmp_path / "hermes-home")
    try:
        agent = SimpleNamespace(session_id="sess_3", platform="cli")
        with telemetry_turn(agent, "no project", "task_3"):
            observe_hook("pre_tool_call", tool_name="read_file", tool_call_id="call_1")
            mark_turn_result({"completed": True})

        assert MissionControlService().list_project_ids() == []
    finally:
        reset_hermes_home_override(token)


def test_service_append_event_once_uses_source_idempotency_key(tmp_path: Path) -> None:
    service = MissionControlService(
        store=MissionControlStore(root=tmp_path / "mission_control")
    )
    event = mc_models.TelemetryEvent(
        event_id="tevt_once",
        event_type="agent_started",
        project_id="proj_once",
        agent_id="agent_once",
        payload={"source_idempotency_key": "runtime:once"},
    )

    first = service.append_event_once(event)
    second = service.append_event_once(mc_models.TelemetryEvent(
        event_id="tevt_once_different_id",
        event_type="agent_started",
        project_id="proj_once",
        agent_id="agent_once",
        payload={"source_idempotency_key": "runtime:once"},
    ))

    assert first is not None
    assert second is None
    assert service.event_count("proj_once") == 1
