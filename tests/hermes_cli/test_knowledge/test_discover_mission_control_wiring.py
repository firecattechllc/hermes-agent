"""Hermes add-on Phase C: KnowledgeService.discover() -> Mission Control wiring.

Prior to this wiring, KnowledgeVisibilityService (mission_control_bridge.py)
was fully built and tested but invoked by nothing outside its own module and
test (per the Hermes add-on audit). These tests exercise the real call site
added in KnowledgeService.discover().
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import hermes_cli.knowledge.service as service_module
from hermes_cli.knowledge.collectors import CollectorResult
from hermes_cli.knowledge.config import KnowledgeConfig
from hermes_cli.knowledge.mission_control_bridge import (
    KNOWLEDGE_DRIFT_EVENT,
    KNOWLEDGE_SNAPSHOT_EVENT,
    KnowledgeVisibilityService,
)
from hermes_cli.knowledge.models import KnowledgeEntity, stable_id
from hermes_cli.knowledge.service import KnowledgeService
from hermes_cli.knowledge.store import KnowledgeGraphStore
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore

NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


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


def _fake_run_collectors(config, *, selected=None, execute=None):
    entities = (_entity(),)
    result = CollectorResult(
        collector_id="fixture",
        success=True,
        duration_ms=1,
        entity_ids=tuple(e.entity_id for e in entities),
    )
    return (result,), entities, (), ()


def _service(tmp_path: Path, *, visibility: KnowledgeVisibilityService | None) -> KnowledgeService:
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    config = KnowledgeConfig(database_path=tmp_path / "graph.sqlite3", node_id="titan")
    return KnowledgeService(
        config, store=store, visibility=visibility, project_id="hermes-platform"
    )


def test_discover_without_visibility_service_is_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "run_collectors", _fake_run_collectors)
    service = _service(tmp_path, visibility=None)

    snapshot, changes = service.discover()

    assert snapshot.entity_count == 1
    assert len(changes) == 1  # the entity was newly added


def test_discover_publishes_snapshot_to_mission_control(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "run_collectors", _fake_run_collectors)
    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    visibility = KnowledgeVisibilityService(mission_control)
    service = _service(tmp_path, visibility=visibility)

    snapshot, changes = service.discover()

    events = mission_control.get_events("hermes-platform")
    event_types = {event.event_type for event in events}
    assert KNOWLEDGE_SNAPSHOT_EVENT in event_types
    assert KNOWLEDGE_DRIFT_EVENT in event_types  # one new entity == one drift change

    snapshot_event = next(e for e in events if e.event_type == KNOWLEDGE_SNAPSHOT_EVENT)
    assert snapshot_event.payload["entity_count"] == 1
    assert snapshot_event.correlation_id == snapshot.snapshot_id


def test_discover_publishing_is_idempotent_on_repeated_identical_snapshot(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(service_module, "run_collectors", _fake_run_collectors)
    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    visibility = KnowledgeVisibilityService(mission_control)
    service = _service(tmp_path, visibility=visibility)

    service.discover()
    first_count = len(mission_control.get_events("hermes-platform"))
    # A second discover() over a genuinely unchanged fixture (same entity,
    # same relationships) must not duplicate-append the same
    # content-addressed idempotency key: no new entity means no new drift
    # change, and an identical entity/relationship set means an identical
    # snapshot event ID, so append_event_once must dedupe it -- the event
    # count must stay exactly the same, not grow.
    service.discover()
    second_count = len(mission_control.get_events("hermes-platform"))

    assert second_count == first_count


def test_discover_never_publishes_credentials_or_raw_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "run_collectors", _fake_run_collectors)
    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    visibility = KnowledgeVisibilityService(mission_control)
    service = _service(tmp_path, visibility=visibility)

    service.discover()

    events = mission_control.get_events("hermes-platform")
    serialized = str([event.payload for event in events]).lower()
    for forbidden in ("password", "api_key", "access_token", "secret"):
        assert forbidden not in serialized
