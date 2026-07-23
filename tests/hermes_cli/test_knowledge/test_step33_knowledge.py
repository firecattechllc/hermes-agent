from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from hermes_cli.knowledge.collectors import (
    CommandResult,
    GitCollector,
    CollectorContext,
    strip_remote_credentials,
)
from hermes_cli.knowledge.config import KnowledgeConfig
from hermes_cli.knowledge.models import (
    ChangeType,
    CollectorResult,
    DiscoveryEvidence,
    DiscoverySnapshot,
    FederationMessageType,
    FederationPayload,
    KnowledgeEntity,
    KnowledgeFederationEnvelope,
    KnowledgeRelationship,
    RelationshipType,
    stable_hash,
    stable_id,
)
from hermes_cli.knowledge.service import KnowledgeService
from hermes_cli.knowledge.store import KnowledgeGraphStore

NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


def entity(name: str = "Titan", *, collector: str = "fixture", status: str = "online"):
    return KnowledgeEntity(
        entity_id=stable_id("entity", "titan", "host", name.lower()),
        entity_type="host",
        name=name,
        canonical_name=name.lower(),
        node_id="titan",
        operational_status=status,
        first_seen_at=NOW,
        last_seen_at=NOW,
        observed_at=NOW,
        evidence_refs=("evidence:1",),
        source_collectors=(collector,),
    )


def relationship(source: KnowledgeEntity, target: KnowledgeEntity):
    return KnowledgeRelationship(
        relationship_id=stable_id(
            "rel", source.entity_id, RelationshipType.DEPENDS_ON.value, target.entity_id
        ),
        source_entity_id=source.entity_id,
        relationship_type=RelationshipType.DEPENDS_ON,
        target_entity_id=target.entity_id,
        first_seen_at=NOW,
        last_seen_at=NOW,
        observed_at=NOW,
        evidence_refs=("evidence:1",),
        source_collectors=("fixture",),
    )


def evidence():
    record = {"safe": "value", "token": "fixture-secret"}
    return DiscoveryEvidence(
        evidence_id="evidence:1",
        collector="fixture",
        node_id="titan",
        collected_at=NOW,
        source_kind="fixture",
        source_locator="fixture://graph",
        content_hash=stable_hash(record),
        summary="fixture evidence",
        raw_record=record,
    )


def snapshot(number: int, *, success: bool = True):
    return DiscoverySnapshot(
        snapshot_id=f"snapshot:{number}",
        node_id="titan",
        started_at=NOW + timedelta(minutes=number),
        completed_at=NOW + timedelta(minutes=number, seconds=1),
        collector_results=(
            CollectorResult(
                collector_id="fixture",
                success=success,
                duration_ms=1,
                error=None if success else "unavailable",
            ),
        ),
        entity_count=0,
        relationship_count=0,
        evidence_count=0,
    )


def test_stable_ids_hashes_and_aware_timestamp_validation():
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})
    assert entity().entity_id == entity().entity_id
    with pytest.raises(ValidationError, match="timezone-aware"):
        entity().model_copy(update={"observed_at": datetime(2026, 1, 1)}).__class__(
            **entity().model_dump(exclude={"observed_at"}),
            observed_at=datetime(2026, 1, 1),
        )


def test_evidence_redacts_sensitive_keys():
    item = evidence()
    assert item.raw_record["token"] == "[REDACTED]"
    assert "fixture-secret" not in item.model_dump_json()


def test_sqlite_migration_and_upsert_preserves_first_seen(tmp_path):
    path = tmp_path / "knowledge.sqlite3"
    store = KnowledgeGraphStore(path)
    KnowledgeGraphStore(path)
    first = entity()
    initial = snapshot(1)
    store.apply_snapshot(initial, (first,), (), (evidence(),))
    later = first.model_copy(
        update={
            "first_seen_at": NOW + timedelta(days=1),
            "last_seen_at": NOW + timedelta(days=1),
            "observed_at": NOW + timedelta(days=1),
            "operational_status": "degraded",
        }
    )
    changes = store.apply_snapshot(snapshot(2), (later,), (), (evidence(),))
    stored = store.entity(first.entity_id)
    assert stored.first_seen_at == NOW
    assert stored.operational_status == "degraded"
    assert changes[0].change_type == ChangeType.CHANGED


def test_failed_collector_does_not_stale_and_threshold_then_restore(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    item = entity()
    store.apply_snapshot(snapshot(1), (item,), (), (evidence(),), missed_threshold=2)
    assert (
        store.apply_snapshot(snapshot(2, success=False), (), (), (), missed_threshold=2)
        == ()
    )
    assert store.apply_snapshot(snapshot(3), (), (), (), missed_threshold=2) == ()
    changes = store.apply_snapshot(snapshot(4), (), (), (), missed_threshold=2)
    assert changes[0].change_type == ChangeType.STALE
    restored = store.apply_snapshot(
        snapshot(5), (item,), (), (evidence(),), missed_threshold=2
    )
    assert restored[0].change_type == ChangeType.RESTORED


def test_conflicting_weaker_observation_retains_stronger_fact(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    trusted = entity(status="online")
    store.apply_snapshot(snapshot(1), (trusted,), (), (evidence(),))
    conflicting = trusted.model_copy(
        update={
            "operational_status": "offline",
            "confidence": 0.2,
            "source_collectors": ("federated-peer",),
            "evidence_refs": ("evidence:peer",),
        }
    )
    changes = store.apply_snapshot(snapshot(2), (conflicting,), (), ())
    assert changes[0].change_type == ChangeType.CONFLICT
    assert store.entity(trusted.entity_id).operational_status == "online"
    assert set(changes[0].evidence_refs) == {"evidence:1", "evidence:peer"}


def test_cycle_safe_traversal_and_impact_path(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    titan, docker, app = entity("Titan"), entity("Docker"), entity("App")
    edges = (
        relationship(titan, docker),
        relationship(docker, app),
        relationship(app, titan),
    )
    store.apply_snapshot(snapshot(1), (titan, docker, app), edges, (evidence(),))
    entities, found_edges = store.neighbors(titan.entity_id, direction="both", depth=10)
    assert len(entities) == 3
    assert len(found_edges) == 3
    service = KnowledgeService(
        KnowledgeConfig(database_path=tmp_path / "graph.sqlite3", node_id="titan"),
        store=store,
    )
    report = service.impact(titan.entity_id)
    assert report.paths
    assert "evidence:1" in report.paths[0].evidence_refs
    assert not report.unaffected_capabilities


def test_remote_credentials_and_git_fixture(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    outputs = {
        ("rev-parse", "HEAD"): "a" * 40 + "\n",
        ("branch", "--show-current"): "step33\n",
        ("status", "--porcelain"): " M safe.py\n",
        (
            "remote",
            "get-url",
            "--all",
            "origin",
        ): "https://user:password@example.test/org/repo.git\n",
    }

    def execute(argv, timeout, maximum):
        return CommandResult(argv, 0, outputs[argv[3:]], "")

    config = KnowledgeConfig(approved_repository_roots=(repo,), node_id="titan")
    result = GitCollector().collect(CollectorContext(config, "titan", NOW, execute))
    assert result.entities[0].operational_status == "dirty"
    assert result.entities[0].attributes["remotes"] == [
        "https://example.test/org/repo.git"
    ]
    assert (
        strip_remote_credentials("git@example.test:org/repo.git")
        == "example.test:org/repo.git"
    )


def test_federation_integrity_idempotency_and_collision(tmp_path):
    payload = FederationPayload(
        message_type=FederationMessageType.DISCOVERY_CHANGE_BATCH,
        records=({"change_id": "change:1"},),
    )
    envelope = KnowledgeFederationEnvelope.build(
        sender_node="titan",
        recipient_node="mac-hermes",
        message_id="message:1",
        correlation_id="correlation:1",
        created_at=NOW,
        payload=payload,
    )
    service = KnowledgeService(
        KnowledgeConfig(database_path=tmp_path / "graph.sqlite3", node_id="mac-hermes")
    )
    assert service.merge_federation(envelope)
    assert not service.merge_federation(envelope)
    with pytest.raises(ValueError, match="identity collision"):
        service.store.accept_federation(envelope.message_id, "f" * 64)


def test_redacted_export_contains_no_fixture_secret(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "graph.sqlite3")
    store.apply_snapshot(snapshot(1), (entity(),), (), (evidence(),))
    exported = json.dumps(store.export_redacted())
    assert "fixture-secret" not in exported
