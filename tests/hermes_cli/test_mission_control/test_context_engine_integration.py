"""Shared Engineering Context to Mission Control integration tests."""

from __future__ import annotations

from pathlib import Path

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli.context_engine import models as context_models
from hermes_cli.context_engine.service import ContextService
from hermes_cli.context_engine.store import ContextStore
from hermes_cli.mission_control.service import MissionControlService


def test_context_launch_updates_flow_to_mission_control_snapshot(tmp_path: Path) -> None:
    token = set_hermes_home_override(tmp_path / "hermes-home")
    try:
        context = ContextService(store=ContextStore(root=tmp_path / "context"))
        context.register_project(
            display_name="Hermes Platform",
            project_id="proj_a",
            repository_identity="repo-a",
            actor="test",
        )
        launch = context.start_launch(
            "proj_a",
            launch_id="launch_1",
            task_id="task_1",
            backlog_id="backlog_1",
            selected_agents=["builder"],
            actor="test",
        )
        context.update_launch(
            "proj_a",
            launch.launch_id,
            status=context_models.LaunchStatus.COMPLETE,
            stage=context_models.LaunchStage.COMPLETE,
            evidence_refs=["evidence/run.log"],
            actor="test",
        )

        snapshot = MissionControlService().get_snapshot("proj_a")
    finally:
        reset_hermes_home_override(token)

    statuses = [
        event.payload.get("status")
        for event in snapshot.events
        if event.event_type == "context_launch_imported"
    ]
    assert statuses == ["pending", "complete"]
    assert [agent.agent_id for agent in snapshot.agent_states] == ["agnt_builder"]
    assert snapshot.backlog_states[0].backlog_id == "backlog_1"
    assert snapshot.evidence_states[0].source_path == "evidence/run.log"
