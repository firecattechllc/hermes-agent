"""Cross-domain Engineering Memory adapter tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.adapters import (
    ContextMemoryAdapter,
    MissionControlMemoryAdapter,
)
from hermes_cli.engineering_memory.service import EngineeringMemoryService
from hermes_cli.engineering_memory.store import EngineeringMemoryStore


def _service(tmp_path: Path) -> EngineeringMemoryService:
    return EngineeringMemoryService(
        store=EngineeringMemoryStore(root=tmp_path)
    )


def _context_record(
    *,
    project_id: str = "hermes-platform",
    record_id: str = "rec_1",
    record_type: str = "engineering_lesson",
    title: str = "Use locked journal writes",
    body: str = "Sequence allocation and append share one lock.",
    status: str = "active",
    updated_at: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        record_id=record_id,
        record_type=record_type,
        title=title,
        body=body,
        status=status,
        created_at=10,
        updated_at=updated_at,
        metadata={"tags": ["persistence"]},
        supersedes=[],
        source_refs=[
            SimpleNamespace(
                source_identifier="tests/evidence.txt"
            )
        ],
    )


def _telemetry_event(
    *,
    event_id: str = "tevt_1",
    project_id: str = "hermes-platform",
    event_type: str = "launch_failed",
    payload: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        project_id=project_id,
        launch_id="launch_1",
        task_id="task_1",
        backlog_id="backlog_1",
        agent_id="agent_1",
        severity="error",
        timestamp=30,
        correlation_id="corr_1",
        causation_id="cause_1",
        payload=payload or {
            "failure_reason": "Journal write failed.",
            "evidence_refs": ["tests/failure.log"],
        },
    )


def test_context_record_becomes_candidate_memory(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()

    memory = adapter.ingest_record(
        service,
        _context_record(),
    )

    assert memory.status == m.MemoryStatus.CANDIDATE
    assert memory.project_id == "hermes-platform"
    assert memory.title == "Use locked journal writes"
    assert memory.structured_payload is not None
    assert (
        memory.structured_payload["source_domain"]
        == "context_engine"
    )
    assert memory.provenance.source_ids == (
        "context_record:rec_1",
    )


def test_context_adapter_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()
    record = _context_record()

    first = adapter.ingest_record(service, record)
    second = adapter.ingest_record(service, record)

    assert second.memory_id == first.memory_id
    assert service.build_snapshot(
        "hermes-platform"
    ).event_count == 1


def test_context_record_update_creates_no_duplicate_content(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()

    first = adapter.ingest_record(
        service,
        _context_record(updated_at=20),
    )
    second = adapter.ingest_record(
        service,
        _context_record(updated_at=30),
    )

    assert second.memory_id == first.memory_id


def test_context_adapter_requires_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()
    record = _context_record()
    record.record_id = ""

    with pytest.raises(
        ValueError,
        match="missing required record_id",
    ):
        adapter.ingest_record(service, record)


def test_context_batch_ingestion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()

    memories = adapter.ingest_records(
        service,
        [
            _context_record(record_id="rec_1"),
            _context_record(
                record_id="rec_2",
                title="Preserve provenance",
                body="Every memory must retain source evidence.",
            ),
        ],
    )

    assert len(memories) == 2
    assert all(
        memory.status == m.MemoryStatus.CANDIDATE
        for memory in memories
    )


def test_context_project_isolation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = ContextMemoryAdapter()

    adapter.ingest_record(
        service,
        _context_record(
            project_id="project-a",
            record_id="rec_a",
        ),
    )
    adapter.ingest_record(
        service,
        _context_record(
            project_id="project-b",
            record_id="rec_b",
        ),
    )

    assert len(
        service.list_memories("project-a")
    ) == 1
    assert len(
        service.list_memories("project-b")
    ) == 1


def test_eligible_telemetry_becomes_candidate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()

    memory = adapter.ingest_event(
        service,
        _telemetry_event(),
    )

    assert memory is not None
    assert memory.status == m.MemoryStatus.CANDIDATE
    assert memory.title == "Journal write failed."
    assert memory.structured_payload is not None
    assert (
        memory.structured_payload["source_domain"]
        == "mission_control"
    )
    assert memory.provenance.source_ids == (
        "telemetry_event:tevt_1",
    )


def test_routine_telemetry_is_ignored(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()

    memory = adapter.ingest_event(
        service,
        _telemetry_event(
            event_type="agent_started",
            payload={"agent_slug": "builder"},
        ),
    )

    assert memory is None
    assert service.list_memories(
        "hermes-platform",
        include_inactive=True,
    ) == []


def test_payload_can_explicitly_request_candidate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()

    memory = adapter.ingest_event(
        service,
        _telemetry_event(
            event_type="custom_observation",
            payload={
                "memory_candidate": True,
                "memory_title": "Observed retry pattern",
                "memory_summary": (
                    "Repeated provider retries exhausted."
                ),
            },
        ),
    )

    assert memory is not None
    assert memory.status == m.MemoryStatus.CANDIDATE
    assert memory.title == "Observed retry pattern"


def test_telemetry_adapter_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()
    event = _telemetry_event()

    first = adapter.ingest_event(service, event)
    second = adapter.ingest_event(service, event)

    assert first is not None
    assert second is not None
    assert second.memory_id == first.memory_id
    assert service.build_snapshot(
        "hermes-platform"
    ).event_count == 1


def test_telemetry_batch_filters_routine_events(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()

    memories = adapter.ingest_events(
        service,
        [
            _telemetry_event(
                event_id="tevt_failed",
                event_type="launch_failed",
            ),
            _telemetry_event(
                event_id="tevt_started",
                event_type="agent_started",
                payload={"agent_slug": "builder"},
            ),
            _telemetry_event(
                event_id="tevt_decision",
                event_type="decision_recorded",
                payload={
                    "decision": "Use append-only journals.",
                },
            ),
        ],
    )

    assert len(memories) == 2
    assert all(
        memory.status == m.MemoryStatus.CANDIDATE
        for memory in memories
    )


def test_telemetry_project_isolation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    adapter = MissionControlMemoryAdapter()

    adapter.ingest_event(
        service,
        _telemetry_event(
            event_id="tevt_a",
            project_id="project-a",
        ),
    )
    adapter.ingest_event(
        service,
        _telemetry_event(
            event_id="tevt_b",
            project_id="project-b",
        ),
    )

    assert len(service.list_memories("project-a")) == 1
    assert len(service.list_memories("project-b")) == 1


def test_adapters_never_auto_verify(tmp_path: Path) -> None:
    service = _service(tmp_path)

    context_memory = ContextMemoryAdapter().ingest_record(
        service,
        _context_record(record_id="rec_context"),
    )
    telemetry_memory = (
        MissionControlMemoryAdapter().ingest_event(
            service,
            _telemetry_event(event_id="tevt_failure"),
        )
    )

    assert context_memory.status == m.MemoryStatus.CANDIDATE
    assert telemetry_memory is not None
    assert telemetry_memory.status == m.MemoryStatus.CANDIDATE
    assert context_memory.reviewed_by is None
    assert telemetry_memory.reviewed_by is None
