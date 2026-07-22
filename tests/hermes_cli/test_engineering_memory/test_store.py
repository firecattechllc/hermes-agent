"""Structured Engineering Memory persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.store import (
    EngineeringMemoryStore,
    event_log_path,
    meta_path,
    project_event_log_path,
)


def _memory(
    *,
    memory_id: str = "mem_1",
    project_id: str = "hermes-platform",
    status: m.MemoryStatus = m.MemoryStatus.CANDIDATE,
    reviewed_by: str | None = None,
    reviewed_at: int | None = None,
) -> m.MemoryRecord:
    return m.MemoryRecord(
        memory_id=memory_id,
        project_id=project_id,
        memory_type=m.MemoryType.IMPLEMENTATION_LESSON,
        title="Use locked journal writes",
        summary="Journal sequence allocation and append must share a lock.",
        status=status,
        confidence=0.95,
        provenance=m.MemoryProvenance(
            source_type=m.MemorySourceType.HUMAN,
            source_ids=("manual:step-3.1",),
            captured_at=10,
            captured_by="maintainer",
        ),
        created_at=20,
        updated_at=20 if reviewed_at is None else reviewed_at,
        created_by="maintainer",
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )


def _event(
    memory: m.MemoryRecord,
    *,
    event_id: str = "mevt_1",
    event_type: m.MemoryEventType = m.MemoryEventType.CREATED,
    timestamp: int = 20,
    sequence: int = 0,
    source_key: str | None = None,
) -> m.MemoryEvent:
    payload = {"memory": memory.model_dump(mode="json")}
    if source_key is not None:
        payload["source_idempotency_key"] = source_key

    return m.MemoryEvent(
        event_id=event_id,
        event_type=event_type,
        project_id=memory.project_id,
        memory_id=memory.memory_id,
        actor="maintainer",
        timestamp=timestamp,
        sequence=sequence,
        payload=payload,
    )


def test_store_initialises_manifest(tmp_path: Path) -> None:
    EngineeringMemoryStore(root=tmp_path)

    metadata = json.loads(meta_path(root=tmp_path).read_text())
    assert metadata["schema_version"] == m.CURRENT_SCHEMA_VERSION
    assert metadata["version"] == 1


def test_existing_unsupported_manifest_fails_closed(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    meta_path(root=tmp_path).write_text(
        json.dumps({"schema_version": 999, "version": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema version 999 not supported"):
        EngineeringMemoryStore(root=tmp_path)


def test_append_event_writes_global_and_project_journals(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)
    event = store.append_event(_event(_memory()))

    assert event.sequence == 1
    assert store.event_count() == 1
    assert store.event_count(project_id="hermes-platform") == 1

    global_data = json.loads(
        event_log_path(root=tmp_path).read_text().strip()
    )
    project_data = json.loads(
        project_event_log_path(
            "hermes-platform",
            root=tmp_path,
        ).read_text().strip()
    )

    assert global_data == project_data
    assert global_data["event_id"] == "mevt_1"


def test_sequence_is_monotonic_per_project(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    first = store.append_event(_event(_memory(), event_id="mevt_1"))
    second = store.append_event(
        _event(
            _memory(memory_id="mem_2"),
            event_id="mevt_2",
        )
    )

    other_project = store.append_event(
        _event(
            _memory(memory_id="mem_3", project_id="other"),
            event_id="mevt_3",
        )
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert other_project.sequence == 1


def test_append_event_once_deduplicates_event_id(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)
    event = _event(_memory())

    assert store.append_event_once(event) is not None
    assert store.append_event_once(event) is None
    assert store.event_count(project_id="hermes-platform") == 1


def test_append_event_once_deduplicates_source_key(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    first = _event(
        _memory(memory_id="mem_1"),
        event_id="mevt_1",
        source_key="context:rec_1",
    )
    duplicate = _event(
        _memory(memory_id="mem_2"),
        event_id="mevt_2",
        source_key="context:rec_1",
    )

    assert store.append_event_once(first) is not None
    assert store.append_event_once(duplicate) is None


def test_batch_append_assigns_project_sequences(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    events = store.append_events(
        [
            _event(_memory(memory_id="mem_1"), event_id="mevt_1"),
            _event(_memory(memory_id="mem_2"), event_id="mevt_2"),
            _event(
                _memory(memory_id="mem_3", project_id="other"),
                event_id="mevt_3",
            ),
        ]
    )

    assert [event.sequence for event in events] == [1, 2, 1]


def test_snapshot_replays_latest_memory_version(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    candidate = _memory()
    verified = _memory(
        status=m.MemoryStatus.VERIFIED,
        reviewed_by="reviewer",
        reviewed_at=30,
    )

    store.append_event(
        _event(
            candidate,
            event_id="mevt_created",
            event_type=m.MemoryEventType.CREATED,
            timestamp=20,
        )
    )
    store.append_event(
        _event(
            verified,
            event_id="mevt_verified",
            event_type=m.MemoryEventType.VERIFIED,
            timestamp=30,
        )
    )

    snapshot = store.build_snapshot(
        "hermes-platform",
        generated_by="test",
    )

    assert snapshot.version == 2
    assert snapshot.event_count == 2
    assert len(snapshot.memories) == 1
    assert snapshot.memories[0].status == m.MemoryStatus.VERIFIED
    assert snapshot.memories[0].reviewed_by == "reviewer"


def test_snapshot_is_deterministic_for_same_journal(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)
    store.append_event(_event(_memory()))

    first = store.build_snapshot(
        "hermes-platform",
        generated_by="first",
    )
    second = store.build_snapshot(
        "hermes-platform",
        generated_by="second",
    )

    assert first.integrity_hash() == second.integrity_hash()


def test_project_isolation_uses_project_journal(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    store.append_event(
        _event(
            _memory(project_id="project-a"),
            event_id="mevt_a",
        )
    )
    store.append_event(
        _event(
            _memory(memory_id="mem_2", project_id="project-b"),
            event_id="mevt_b",
        )
    )

    project_a = store.build_snapshot("project-a")
    project_b = store.build_snapshot("project-b")

    assert [memory.project_id for memory in project_a.memories] == ["project-a"]
    assert [memory.project_id for memory in project_b.memories] == ["project-b"]


def test_project_journal_cross_project_record_fails_closed(
    tmp_path: Path,
) -> None:
    store = EngineeringMemoryStore(root=tmp_path)
    event = _event(_memory(project_id="project-b"))

    path = project_event_log_path("project-a", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="project isolation violation"):
        list(store.iter_events(project_id="project-a"))


def test_memory_payload_project_mismatch_fails_closed(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    memory = _memory(project_id="project-b")
    event = _event(memory)
    event.project_id = "project-a"

    path = project_event_log_path("project-a", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="belongs to project-b"):
        store.build_snapshot("project-a")


def test_memory_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    event = _event(_memory())
    event.memory_id = "mem_different"

    path = project_event_log_path("hermes-platform", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="memory identity mismatch"):
        store.build_snapshot("hermes-platform")


def test_event_without_memory_payload_fails_closed(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    event = _event(_memory())
    event.payload = {}

    path = project_event_log_path("hermes-platform", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(event.model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lacks payload.memory"):
        store.build_snapshot("hermes-platform")


def test_malformed_jsonl_fails_closed(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    path = project_event_log_path("hermes-platform", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"broken":\n', encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSONL line 1"):
        list(store.iter_events(project_id="hermes-platform"))


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../escape",
        "a/b",
        "a\\b",
        "",
        "bad\x00id",
    ],
)
def test_unsafe_project_ids_are_rejected(
    tmp_path: Path,
    unsafe_id: str,
) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    with pytest.raises(ValueError, match="unsafe identifier"):
        store.build_snapshot(unsafe_id)


def test_list_project_ids_is_sorted(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    store.append_event(
        _event(
            _memory(project_id="zeta"),
            event_id="mevt_z",
        )
    )
    store.append_event(
        _event(
            _memory(memory_id="mem_2", project_id="alpha"),
            event_id="mevt_a",
        )
    )

    assert store.list_project_ids() == ["alpha", "zeta"]


def test_list_memories_filters_status_and_type(tmp_path: Path) -> None:
    store = EngineeringMemoryStore(root=tmp_path)

    candidate = _memory(memory_id="mem_candidate")
    verified_data = _memory(memory_id="mem_verified").model_dump()
    verified_data.update(
        {
            "memory_type": m.MemoryType.ARCHITECTURE_DECISION,
            "status": m.MemoryStatus.VERIFIED,
            "reviewed_by": "reviewer",
            "reviewed_at": 30,
            "updated_at": 30,
        }
    )
    verified = m.MemoryRecord(**verified_data)

    store.append_event(
        _event(candidate, event_id="mevt_candidate")
    )
    store.append_event(
        _event(
            verified,
            event_id="mevt_verified",
            event_type=m.MemoryEventType.VERIFIED,
            timestamp=30,
        )
    )

    verified_results = store.list_memories(
        "hermes-platform",
        status=m.MemoryStatus.VERIFIED,
    )
    architecture_results = store.list_memories(
        "hermes-platform",
        memory_type=m.MemoryType.ARCHITECTURE_DECISION,
    )

    assert [memory.memory_id for memory in verified_results] == ["mem_verified"]
    assert [memory.memory_id for memory in architecture_results] == [
        "mem_verified"
    ]
