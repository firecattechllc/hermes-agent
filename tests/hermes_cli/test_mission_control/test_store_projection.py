"""Mission Control store projection regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.store import MissionControlStore


def _event(
    event_id: str,
    event_type: str,
    *,
    project_id: str = "proj_a",
    sequence: int = 1,
    timestamp: int | None = None,
    agent_id: str | None = None,
    backlog_id: str | None = None,
    payload: dict | None = None,
) -> m.TelemetryEvent:
    return m.TelemetryEvent(
        event_id=event_id,
        event_type=event_type,
        project_id=project_id,
        launch_id="launch_1",
        task_id="task_1",
        backlog_id=backlog_id,
        agent_id=agent_id,
        sequence=sequence,
        timestamp=timestamp if timestamp is not None else sequence,
        payload=payload or {},
    )


def _snapshot(store: MissionControlStore, project_id: str = "proj_a") -> m.MissionControlSnapshot:
    try:
        return store.build_snapshot(project_id)
    except TypeError as exc:
        if "multiple values for keyword argument" in str(exc):
            pytest.fail(f"projection update passed duplicate model fields: {exc}")
        raise


def test_agent_projection_repeated_status_changes_and_completion(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event("e1", "agent_started", agent_id="agent_1", sequence=1),
        _event("e2", "agent_thinking", agent_id="agent_1", sequence=2),
        _event("e3", "agent_tools_started", agent_id="agent_1", sequence=3),
        _event("e4", "agent_tools_completed", agent_id="agent_1", sequence=4),
        _event("e5", "agent_complete", agent_id="agent_1", sequence=5),
    ])

    snapshot = _snapshot(store)

    assert len(snapshot.agent_states) == 1
    agent = snapshot.agent_states[0]
    assert agent.agent_id == "agent_1"
    assert agent.state == m.AgentState.COMPLETE
    assert agent.last_event_id == "e5"
    assert agent.last_event_type == "agent_complete"
    assert agent.project_id == "proj_a"


def test_backlog_projection_repeated_status_changes(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event(
            "e1",
            "backlog_item_created",
            backlog_id="backlog_1",
            sequence=1,
            payload={"title": "Implement Mission Control", "description": "Initial work"},
        ),
        _event("e2", "backlog_item_started", backlog_id="backlog_1", sequence=2),
        _event("e3", "backlog_item_blocked", backlog_id="backlog_1", sequence=3),
        _event("e4", "backlog_item_done", backlog_id="backlog_1", sequence=4),
    ])

    snapshot = _snapshot(store)

    assert len(snapshot.backlog_states) == 1
    backlog = snapshot.backlog_states[0]
    assert backlog.backlog_id == "backlog_1"
    assert backlog.state == m.BacklogItemState.DONE
    assert backlog.title == "Implement Mission Control"
    assert backlog.description == "Initial work"


def test_approval_projection_requested_and_resolved(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event(
            "e1",
            "approval_requested",
            sequence=1,
            payload={
                "approval_id": "approval_1",
                "requested_by": "agent",
                "summary": "Run command",
            },
        ),
        _event(
            "e2",
            "approval_granted",
            sequence=2,
            payload={"approval_id": "approval_1", "resolved_by": "user"},
        ),
    ])

    snapshot = _snapshot(store)

    assert len(snapshot.approval_states) == 1
    approval = snapshot.approval_states[0]
    assert approval.approval_id == "approval_1"
    assert approval.state == m.ApprovalState.APPROVED
    assert approval.requested_by == "agent"
    assert approval.resolved_by == "user"
    assert approval.summary == "Run command"


def test_evidence_projection_recorded_and_updated(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event(
            "e1",
            "evidence_requested",
            sequence=1,
            payload={"evidence_id": "evidence_1", "source_path": "logs/run.txt"},
        ),
        _event(
            "e2",
            "evidence_collected",
            sequence=2,
            payload={"evidence_id": "evidence_1"},
        ),
        _event(
            "e3",
            "evidence_verified",
            sequence=3,
            payload={"evidence_id": "evidence_1", "content_hash": "abc123"},
        ),
    ])

    snapshot = _snapshot(store)

    assert len(snapshot.evidence_states) == 1
    evidence = snapshot.evidence_states[0]
    assert evidence.evidence_id == "evidence_1"
    assert evidence.state == m.EvidenceState.VERIFIED
    assert evidence.source_path == "logs/run.txt"
    assert evidence.content_hash == "abc123"


@pytest.mark.parametrize(
    ("event_type", "expected_state"),
    [
        ("promotion_started", m.PromotionState.IN_PROGRESS),
        ("promotion_approved", m.PromotionState.APPROVED),
        ("promotion_rejected", m.PromotionState.REJECTED),
        ("promotion_deployed", m.PromotionState.DEPLOYED),
    ],
)
def test_promotion_projection_repeated_events(
    tmp_path: Path,
    event_type: str,
    expected_state: m.PromotionState,
) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event(
            "e1",
            "promotion_requested",
            sequence=1,
            payload={
                "promotion_id": "promotion_1",
                "requested_by": "agent",
                "target_ref": "refs/heads/dev",
            },
        ),
        _event(
            "e2",
            event_type,
            sequence=2,
            payload={"promotion_id": "promotion_1", "approved_by": "maintainer"},
        ),
    ])

    snapshot = _snapshot(store)

    assert len(snapshot.promotion_states) == 1
    promotion = snapshot.promotion_states[0]
    assert promotion.promotion_id == "promotion_1"
    assert promotion.state == expected_state
    assert promotion.requested_by == "agent"
    assert promotion.target_ref == "refs/heads/dev"


@pytest.mark.parametrize(
    ("status", "stage", "failure_reason"),
    [
        ("running", "implementation", None),
        ("complete", "complete", None),
        ("failed", "failed", "validation failed"),
        ("cancelled", "failed", "cancelled by user"),
    ],
)
def test_launch_import_events_preserved_for_status_lifecycle(
    tmp_path: Path,
    status: str,
    stage: str,
    failure_reason: str | None,
) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_event(
        _event(
            "e1",
            "context_launch_imported",
            sequence=1,
            payload={
                "launch_id": "launch_1",
                "status": status,
                "stage": stage,
                "failure_reason": failure_reason,
            },
        )
    )

    snapshot = _snapshot(store)

    assert snapshot.event_count == 1
    assert snapshot.events[0].payload["status"] == status
    assert snapshot.events[0].payload["stage"] == stage
    assert snapshot.events[0].payload["failure_reason"] == failure_reason


def test_project_isolation_uses_project_journals(tmp_path: Path) -> None:
    store = MissionControlStore(root=tmp_path / "mission_control")
    store.append_events([
        _event("a1", "agent_started", project_id="proj_a", agent_id="agent_a", sequence=1),
        _event("b1", "agent_started", project_id="proj_b", agent_id="agent_b", sequence=1),
    ])

    snap_a = _snapshot(store, "proj_a")
    snap_b = _snapshot(store, "proj_b")

    assert [agent.agent_id for agent in snap_a.agent_states] == ["agent_a"]
    assert [agent.agent_id for agent in snap_b.agent_states] == ["agent_b"]
