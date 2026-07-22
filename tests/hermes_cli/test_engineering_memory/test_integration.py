"""Real-contract integration certification for Engineering Memory.

These tests intentionally reuse the adapter suite's canonical source fixtures.
That prevents this integration layer from inventing unsupported Context or
Mission Control event shapes.
"""

from __future__ import annotations

from pathlib import Path

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.adapters.context_adapter import (
    ContextMemoryAdapter,
)
from hermes_cli.engineering_memory.adapters.mission_control_adapter import (
    MissionControlMemoryAdapter,
)
from hermes_cli.engineering_memory.service import (
    EngineeringMemoryService,
)
from hermes_cli.engineering_memory.store import (
    EngineeringMemoryStore,
)
from tests.hermes_cli.test_engineering_memory.test_adapters import (
    _context_record,
    _telemetry_event,
)


def _service(root: Path) -> EngineeringMemoryService:
    return EngineeringMemoryService(
        store=EngineeringMemoryStore(root=root)
    )


def test_context_candidate_can_be_verified_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")

    source = _context_record(
        record_id="rec_integration_verify",
        project_id="hermes-platform",
    )
    candidate = ContextMemoryAdapter().ingest_record(
        service,
        source,
    )

    assert candidate.project_id == "hermes-platform"
    assert candidate.status == m.MemoryStatus.CANDIDATE
    assert candidate.reviewed_by is None
    assert candidate.reviewed_at is None
    assert f"context_record:{source.record_id}" in candidate.provenance.source_ids

    verified = service.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="independent-reviewer",
        review_note="Source record and claim were inspected.",
        confidence=1.0,
    )

    assert verified.status == m.MemoryStatus.VERIFIED
    assert verified.reviewed_by == "independent-reviewer"
    assert verified.reviewed_at is not None
    assert verified.review_note == (
        "Source record and claim were inspected."
    )
    assert verified.provenance == candidate.provenance

    events = service.list_events("hermes-platform")
    assert [event.event_type for event in events] == [
        m.MemoryEventType.CREATED,
        m.MemoryEventType.VERIFIED,
    ]


def test_context_ingestion_is_idempotent_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")
    source = _context_record(
        record_id="rec_integration_idempotent",
        project_id="hermes-platform",
    )
    adapter = ContextMemoryAdapter()

    first = adapter.ingest_record(service, source)
    second = adapter.ingest_record(service, source)

    assert second.memory_id == first.memory_id
    assert len(
        service.list_memories(
            "hermes-platform",
            include_inactive=True,
        )
    ) == 1
    assert len(service.list_events("hermes-platform")) == 1


def test_mission_control_candidate_can_be_rejected_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")

    source = _telemetry_event(
        event_id="tevt_integration_reject",
        project_id="hermes-platform",
    )
    candidate = MissionControlMemoryAdapter().ingest_event(
        service,
        source,
    )

    assert candidate is not None
    assert candidate.project_id == "hermes-platform"
    assert candidate.status == m.MemoryStatus.CANDIDATE
    assert candidate.reviewed_by is None
    assert candidate.reviewed_at is None
    assert f"telemetry_event:{source.event_id}" in candidate.provenance.source_ids

    rejected = service.reject_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="independent-reviewer",
        review_note=(
            "Synthetic integration telemetry is not durable knowledge."
        ),
    )

    assert rejected.status == m.MemoryStatus.REJECTED
    assert rejected.reviewed_by == "independent-reviewer"
    assert rejected.reviewed_at is not None

    assert service.list_memories("hermes-platform") == []

    historical = service.list_memories(
        "hermes-platform",
        include_inactive=True,
    )
    assert len(historical) == 1
    assert historical[0].status == m.MemoryStatus.REJECTED

    events = service.list_events("hermes-platform")
    assert [event.event_type for event in events] == [
        m.MemoryEventType.CREATED,
        m.MemoryEventType.REJECTED,
    ]


def test_mission_control_ingestion_is_idempotent_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")
    source = _telemetry_event(
        event_id="tevt_integration_idempotent",
        project_id="hermes-platform",
    )
    adapter = MissionControlMemoryAdapter()

    first = adapter.ingest_event(service, source)
    second = adapter.ingest_event(service, source)

    assert first is not None
    assert second is not None
    assert second.memory_id == first.memory_id
    assert len(
        service.list_memories(
            "hermes-platform",
            include_inactive=True,
        )
    ) == 1
    assert len(service.list_events("hermes-platform")) == 1


def test_cross_source_project_isolation_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")

    context_memory = ContextMemoryAdapter().ingest_record(
        service,
        _context_record(
            record_id="rec_project_a",
            project_id="project-a",
        ),
    )
    telemetry_memory = MissionControlMemoryAdapter().ingest_event(
        service,
        _telemetry_event(
            event_id="tevt_project_b",
            project_id="project-b",
        ),
    )

    assert telemetry_memory is not None

    assert [
        memory.memory_id
        for memory in service.list_memories("project-a")
    ] == [context_memory.memory_id]

    assert [
        memory.memory_id
        for memory in service.list_memories("project-b")
    ] == [telemetry_memory.memory_id]

    assert service.get_memory(
        "project-a",
        telemetry_memory.memory_id,
    ) is None
    assert service.get_memory(
        "project-b",
        context_memory.memory_id,
    ) is None

    snapshot_a = service.build_snapshot("project-a")
    snapshot_b = service.build_snapshot("project-b")

    assert {
        memory.project_id
        for memory in snapshot_a.memories
    } == {"project-a"}
    assert {
        memory.project_id
        for memory in snapshot_b.memories
    } == {"project-b"}


def test_replay_reconstructs_identical_governed_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    original = _service(root)

    candidate = ContextMemoryAdapter().ingest_record(
        original,
        _context_record(
            record_id="rec_replay",
            project_id="hermes-platform",
        ),
    )
    original.verify_memory(
        "hermes-platform",
        candidate.memory_id,
        reviewed_by="reviewer",
        review_note="Replay evidence accepted.",
    )

    telemetry = MissionControlMemoryAdapter().ingest_event(
        original,
        _telemetry_event(
            event_id="tevt_replay",
            project_id="hermes-platform",
        ),
    )
    assert telemetry is not None

    original.reject_memory(
        "hermes-platform",
        telemetry.memory_id,
        reviewed_by="reviewer",
        review_note="Telemetry rejected during replay certification.",
    )

    before = original.build_snapshot("hermes-platform")

    replayed = _service(root)
    after = replayed.build_snapshot("hermes-platform")

    assert before.version == after.version
    assert before.event_count == after.event_count
    assert before.memories == after.memories
    assert before.integrity_hash() == after.integrity_hash()


def test_adapters_cannot_bypass_governance_end_to_end(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")

    context_candidate = ContextMemoryAdapter().ingest_record(
        service,
        _context_record(
            record_id="rec_governance",
            project_id="hermes-platform",
        ),
    )
    telemetry_candidate = MissionControlMemoryAdapter().ingest_event(
        service,
        _telemetry_event(
            event_id="tevt_governance",
            project_id="hermes-platform",
        ),
    )

    assert telemetry_candidate is not None

    for candidate in (
        context_candidate,
        telemetry_candidate,
    ):
        assert candidate.status == m.MemoryStatus.CANDIDATE
        assert candidate.reviewed_by is None
        assert candidate.reviewed_at is None
        assert candidate.review_note is None

    assert service.list_memories(
        "hermes-platform",
        status=m.MemoryStatus.VERIFIED,
        include_inactive=True,
    ) == []


def test_snapshot_captures_governed_cross_source_state(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "memory")

    context_candidate = ContextMemoryAdapter().ingest_record(
        service,
        _context_record(
            record_id="rec_snapshot",
            project_id="hermes-platform",
        ),
    )
    telemetry_candidate = MissionControlMemoryAdapter().ingest_event(
        service,
        _telemetry_event(
            event_id="tevt_snapshot",
            project_id="hermes-platform",
        ),
    )
    assert telemetry_candidate is not None

    service.verify_memory(
        "hermes-platform",
        context_candidate.memory_id,
        reviewed_by="reviewer",
    )
    service.archive_memory(
        "hermes-platform",
        telemetry_candidate.memory_id,
        actor="reviewer",
        archive_note="Archived during integration certification.",
    )

    snapshot = service.build_snapshot(
        "hermes-platform",
        generated_by="integration-test",
    )

    assert snapshot.event_count == 4
    assert len(snapshot.memories) == 2
    assert {
        memory.status for memory in snapshot.memories
    } == {
        m.MemoryStatus.VERIFIED,
        m.MemoryStatus.ARCHIVED,
    }
    assert len(snapshot.integrity_hash()) == 32
