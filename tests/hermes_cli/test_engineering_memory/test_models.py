"""Structured Engineering Memory domain model tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.engineering_memory import models as m


def _provenance() -> m.MemoryProvenance:
    return m.MemoryProvenance(
        source_type=m.MemorySourceType.TEST_RESULT,
        source_ids=("pytest:mission-control", "pytest:mission-control", " "),
        evidence_refs=("logs/tests.txt", "logs/tests.txt"),
        captured_at=10,
        captured_by="tester",
        content_hash="abc123",
    )


def _memory(**overrides) -> m.MemoryRecord:
    data = {
        "memory_id": "mem_1",
        "project_id": "hermes-platform",
        "memory_type": m.MemoryType.TEST_EVIDENCE,
        "title": "Mission Control tests passed",
        "summary": "The focused Mission Control suite passed.",
        "body": "77 focused tests passed.",
        "status": m.MemoryStatus.CANDIDATE,
        "confidence": 1.0,
        "provenance": _provenance(),
        "tags": ["Testing", " testing ", "mission-control"],
        "created_at": 20,
        "updated_at": 20,
        "created_by": "human",
    }
    data.update(overrides)
    return m.MemoryRecord(**data)


def test_candidate_memory_defaults_and_normalisation() -> None:
    memory = _memory()

    assert memory.status == m.MemoryStatus.CANDIDATE
    assert memory.tags == ["testing", "mission-control"]
    assert memory.provenance.source_ids == ("pytest:mission-control",)
    assert memory.provenance.evidence_refs == ("logs/tests.txt",)


def test_provenance_is_immutable() -> None:
    provenance = _provenance()

    with pytest.raises(ValidationError):
        provenance.captured_by = "rewriter"


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError, match="schema version 999 not supported"):
        _memory(schema_version=999)


def test_verified_memory_requires_reviewer_and_timestamp() -> None:
    with pytest.raises(ValidationError, match="verified memory requires reviewed_by"):
        _memory(status=m.MemoryStatus.VERIFIED)

    with pytest.raises(ValidationError, match="verified memory requires reviewed_at"):
        _memory(
            status=m.MemoryStatus.VERIFIED,
            reviewed_by="maintainer",
        )


def test_verified_memory_is_valid_with_review_metadata() -> None:
    memory = _memory(
        status=m.MemoryStatus.VERIFIED,
        reviewed_by="maintainer",
        reviewed_at=30,
        review_note="Reproduced from focused test output.",
    )

    assert memory.status == m.MemoryStatus.VERIFIED
    assert memory.reviewed_by == "maintainer"
    assert memory.reviewed_at == 30


def test_rejected_memory_requires_explanation() -> None:
    with pytest.raises(ValidationError, match="rejected memory requires review_note"):
        _memory(
            status=m.MemoryStatus.REJECTED,
            reviewed_by="maintainer",
            reviewed_at=30,
        )


def test_superseded_memory_requires_replacement() -> None:
    with pytest.raises(ValidationError, match="superseded memory requires superseded_by"):
        _memory(status=m.MemoryStatus.SUPERSEDED)


def test_memory_cannot_supersede_itself() -> None:
    with pytest.raises(ValidationError, match="memory cannot supersede itself"):
        _memory(
            status=m.MemoryStatus.SUPERSEDED,
            superseded_by="mem_1",
        )

    with pytest.raises(ValidationError, match="memory cannot list itself"):
        _memory(supersedes=["mem_1"])


def test_content_fingerprint_is_stable_across_identity_and_time() -> None:
    first = _memory(memory_id="mem_a", created_at=20, updated_at=20)
    second = _memory(memory_id="mem_b", created_at=99, updated_at=100)

    assert first.content_fingerprint() == second.content_fingerprint()


def test_content_fingerprint_changes_with_semantic_content() -> None:
    first = _memory()
    second = _memory(summary="A different test result.")

    assert first.content_fingerprint() != second.content_fingerprint()


def test_memory_event_stable_sort_key() -> None:
    event = m.MemoryEvent(
        event_id="mevt_1",
        event_type=m.MemoryEventType.CREATED,
        project_id="hermes-platform",
        memory_id="mem_1",
        timestamp=10,
        sequence=2,
    )

    assert event.stable_sort_key() == (10, 2, "mevt_1")


def test_snapshot_integrity_hash_is_deterministic() -> None:
    first = m.MemorySnapshot(
        version=1,
        generated_at=100,
        generated_by="first",
        project_id="hermes-platform",
        event_count=1,
        memories=[_memory()],
    )
    second = m.MemorySnapshot(
        version=1,
        generated_at=200,
        generated_by="second",
        project_id="hermes-platform",
        event_count=1,
        memories=[_memory()],
    )

    assert first.integrity_hash() == second.integrity_hash()


def test_timestamp_before_creation_is_normalised() -> None:
    memory = _memory(created_at=20, updated_at=10)

    assert memory.updated_at == 20


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _memory(confidence=1.1)

    with pytest.raises(ValidationError):
        _memory(confidence=-0.1)
