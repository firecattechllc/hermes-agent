from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hermes_cli.knowledge.mission_control_bridge import (
    KNOWLEDGE_DRIFT_EVENT,
    KNOWLEDGE_SNAPSHOT_EVENT,
    KnowledgeVisibilityService,
)
from hermes_cli.knowledge.models import (
    ChangeSeverity,
    ChangeType,
    GraphChange,
    KnowledgeEntity,
    stable_id,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

NOW = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)


def _entity(name: str = "Titan") -> KnowledgeEntity:
    return KnowledgeEntity(
        entity_id=stable_id("entity", "titan", "host", name.lower()),
        entity_type="host",
        name=name,
        canonical_name=name.lower(),
        node_id="titan",
        operational_status="online",
        first_seen_at=NOW,
        last_seen_at=NOW,
        observed_at=NOW,
        evidence_refs=("evidence:1",),
        source_collectors=("fixture",),
    )


def _change(
    entity_id: str, severity: ChangeSeverity = ChangeSeverity.INFO
) -> GraphChange:
    return GraphChange(
        change_id=stable_id("change", entity_id, "added"),
        change_type=ChangeType.ADDED,
        entity_id=entity_id,
        detected_at=NOW,
        severity=severity,
        summary="entity discovered",
    )


@pytest.fixture()
def mission_control(tmp_path: Path) -> MissionControlService:
    return MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))


def test_knowledge_snapshot_reaches_mission_control(
    mission_control: MissionControlService,
) -> None:
    entity = _entity()
    service = KnowledgeVisibilityService(mission_control)
    event = service.publish_snapshot(
        project_id="proj1",
        node_id="titan",
        entities=(entity,),
        relationships=(),
        timestamp=1_800_000_000,
    )
    assert event.event_type == KNOWLEDGE_SNAPSHOT_EVENT
    stored = mission_control.get_events("proj1")
    assert len(stored) == 1
    assert stored[0].payload["entity_count"] == 1


def test_knowledge_drift_reaches_mission_control_with_severity(
    mission_control: MissionControlService,
) -> None:
    entity = _entity()
    change = _change(entity.entity_id, severity=ChangeSeverity.CRITICAL)
    service = KnowledgeVisibilityService(mission_control)
    events = service.publish_drift(
        project_id="proj1", node_id="titan", changes=(change,)
    )
    assert len(events) == 1
    assert events[0].event_type == KNOWLEDGE_DRIFT_EVENT
    assert events[0].severity == "warning"


def test_knowledge_snapshot_publish_is_idempotent(
    mission_control: MissionControlService,
) -> None:
    entity = _entity()
    service = KnowledgeVisibilityService(mission_control)
    service.publish_snapshot(
        project_id="proj1",
        node_id="titan",
        entities=(entity,),
        relationships=(),
        timestamp=1_800_000_000,
    )
    service.publish_snapshot(
        project_id="proj1",
        node_id="titan",
        entities=(entity,),
        relationships=(),
        timestamp=1_800_000_000,
    )
    assert len(mission_control.get_events("proj1")) == 1
